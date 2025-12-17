"""FastAPI веб-интерфейс для управления Job Notification Bot"""
import os
import asyncio
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
import logging

import sys
sys.path.append(str(Path(__file__).parent.parent))

from config_manager import ConfigManager, ChannelConfig, FilterConfig
from auth import agent_auth_manager, bot_auth_manager

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Job Notification Bot - Management Interface")

# Очередь отложенных удалений Telegram сущностей
pending_telegram_deletions: List[Dict[str, Any]] = []
deletion_worker_started = False

# Setup templates and static files
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Initialize config manager
config_manager = ConfigManager()

# Storage for templates and source lists
import json

TEMPLATES_FILE = BASE_DIR.parent / "configs" / "templates.json"
SOURCE_LISTS_FILE = BASE_DIR.parent / "configs" / "source_lists.json"


def load_templates():
    """Load saved auto-response templates"""
    if TEMPLATES_FILE.exists():
        try:
            with open(TEMPLATES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    # Default templates
    return [
        {"id": "default", "name": "Стандартный отклик", "text": "Здравствуйте! Меня заинтересовала ваша вакансия. Буду рад обсудить детали!"},
        {"id": "detailed", "name": "Подробный отклик", "text": "Здравствуйте!\n\nМеня заинтересовала ваша вакансия. Имею релевантный опыт и готов обсудить условия сотрудничества.\n\nБуду рад ответить на ваши вопросы!"}
    ]


def save_templates(templates):
    """Save templates to file"""
    TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TEMPLATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


def load_source_lists():
    """Load saved source channel lists"""
    if SOURCE_LISTS_FILE.exists():
        try:
            with open(SOURCE_LISTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []


def save_source_lists(lists):
    """Save source lists to file"""
    SOURCE_LISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SOURCE_LISTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(lists, f, ensure_ascii=False, indent=2)


def get_available_agents():
    """Get list of all authorized agent sessions"""
    agents = []
    sessions_dir = BASE_DIR.parent / "sessions"
    if sessions_dir.exists():
        for session_file in sessions_dir.glob("agent_*.session"):
            session_name = session_file.stem
            # Try to get agent info
            agents.append({
                "session_name": session_name,
                "phone": "",  # Will be filled if we can read it
                "name": session_name.replace("agent_", "Агент ")
            })
    return agents


# Pydantic models for API
class AgentRequest(BaseModel):
    phone: str
    session_name: str


class ChannelCreateRequest(BaseModel):
    name: str
    telegram_id: int
    input_sources: List[str]
    include_keywords: List[str]
    exclude_keywords: List[str] = []
    enabled: bool = True
    
    # CRM поля
    crm_enabled: bool = False
    crm_group_id: int = 0
    agents: List[AgentRequest] = []
    auto_response_enabled: bool = False
    auto_response_template: str = ""


class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    telegram_id: Optional[int] = None
    input_sources: Optional[List[str]] = None
    include_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    enabled: Optional[bool] = None
    
    # CRM поля
    crm_enabled: Optional[bool] = None
    crm_group_id: Optional[int] = None
    agents: Optional[List[AgentRequest]] = None
    auto_response_enabled: Optional[bool] = None
    auto_response_template: Optional[str] = None


# Routes
@app.get("/")
async def index(request: Request):
    """Главная страница со списком каналов"""
    channels = config_manager.load()
    return templates.TemplateResponse(
        "channels_list.html",
        {
            "request": request,
            "channels": channels
        }
    )


@app.get("/channel/new")
async def new_channel_page(request: Request):
    """Новая страница создания канала (wizard)"""
    return templates.TemplateResponse(
        "channel_create.html",
        {"request": request}
    )


@app.get("/channel/new-legacy")
async def new_channel_page_legacy(request: Request):
    """Старая страница создания канала"""
    return templates.TemplateResponse(
        "channel_edit.html",
        {
            "request": request,
            "channel": None,
            "is_new": True
        }
    )


@app.get("/channel/{channel_id}")
async def edit_channel_page(request: Request, channel_id: str):
    """Страница редактирования канала"""
    channel = config_manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(404, "Канал не найден")
    
    return templates.TemplateResponse(
        "channel_edit.html",
        {
            "request": request,
            "channel": channel,
            "is_new": False
        }
    )


# API Endpoints
@app.get("/api/channels")
async def get_channels():
    """Получить список всех каналов"""
    channels = config_manager.load()
    return {
        "success": True,
        "channels": [ch.to_dict() for ch in channels]
    }


@app.get("/api/channels/{channel_id}")
async def get_channel(channel_id: str):
    """Получить конфигурацию канала"""
    channel = config_manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(404, "Канал не найден")

    return {
        "success": True,
        "channel": channel.to_dict()
    }


async def _add_agents_to_crm_group(crm_group_id: int, agents: list) -> dict:
    """
    Вспомогательная функция для добавления агентов в CRM группу.
    Возвращает dict с invited (список добавленных) и errors (список ошибок).
    """
    from telethon.tl.functions.channels import InviteToChannelRequest
    from auth.base import TimeoutSQLiteSession
    from telethon import TelegramClient
    from config import config

    invited = []
    errors = []

    # Проверяем сессию бота
    session_status = await bot_auth_manager.check_session_status()
    if not session_status.get("authenticated"):
        return {"invited": [], "errors": ["Бот не авторизован"]}

    # Создаём клиент бота
    session = TimeoutSQLiteSession(config.SESSION_NAME)
    client = TelegramClient(session, config.API_ID, config.API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return {"invited": [], "errors": ["Сессия бота недействительна"]}

    try:
        # Получаем группу
        group = await client.get_entity(crm_group_id)

        for agent_data in agents:
            # Поддержка разных форматов agent_data
            if isinstance(agent_data, dict):
                agent_session = agent_data.get('session_name')
            elif hasattr(agent_data, 'session_name'):
                agent_session = agent_data.session_name
            else:
                agent_session = str(agent_data)

            if not agent_session:
                continue

            try:
                agent_session_path = f"sessions/{agent_session}"
                agent_tg_session = TimeoutSQLiteSession(agent_session_path)
                agent_client = TelegramClient(agent_tg_session, config.API_ID, config.API_HASH)
                await agent_client.connect()

                if await agent_client.is_user_authorized():
                    agent_me = await agent_client.get_me()
                    try:
                        await client(InviteToChannelRequest(
                            channel=group,
                            users=[agent_me.id]
                        ))
                        agent_name = agent_me.username or agent_me.first_name
                        invited.append(f"@{agent_name}")
                        logger.info(f"Агент {agent_session} добавлен в CRM группу {crm_group_id}")
                    except Exception as invite_err:
                        # Возможно уже в группе
                        if "USER_ALREADY_PARTICIPANT" in str(invite_err):
                            agent_name = agent_me.username or agent_me.first_name
                            invited.append(f"@{agent_name} (уже в группе)")
                        else:
                            errors.append(f"{agent_session}: {str(invite_err)}")

                await agent_client.disconnect()
            except Exception as e:
                errors.append(f"{agent_session}: {str(e)}")
                logger.warning(f"Не удалось добавить агента {agent_session}: {e}")

    finally:
        await client.disconnect()

    return {"invited": invited, "errors": errors}


@app.post("/api/channels")
async def create_channel(data: ChannelCreateRequest):
    """Создать новый канал"""
    try:
        # Генерируем уникальный ID
        import uuid
        channel_id = f"channel_{uuid.uuid4().hex[:8]}"
        
        # Создаем конфигурацию
        filters = FilterConfig(
            include_keywords=data.include_keywords,
            exclude_keywords=data.exclude_keywords,
            require_all_includes=False
        )
        
        # Конвертируем агентов
        from config_manager import AgentConfig
        agents = []
        for agent_req in data.agents:
            agents.append(AgentConfig(
                phone=agent_req.phone,
                session_name=agent_req.session_name
            ))
        
        channel = ChannelConfig(
            id=channel_id,
            name=data.name,
            telegram_id=data.telegram_id,
            enabled=data.enabled,
            input_sources=data.input_sources,
            filters=filters,
            crm_enabled=data.crm_enabled,
            crm_group_id=data.crm_group_id,
            agents=agents,
            auto_response_enabled=data.auto_response_enabled,
            auto_response_template=data.auto_response_template
        )
        
        # Добавляем
        if config_manager.add_channel(channel):
            # Автодобавление агентов в CRM группу
            agents_added = []
            agents_errors = []
            if channel.crm_enabled and channel.crm_group_id and channel.agents:
                try:
                    add_result = await _add_agents_to_crm_group(channel.crm_group_id, channel.agents)
                    agents_added = add_result.get('invited', [])
                    agents_errors = add_result.get('errors', [])
                except Exception as e:
                    logger.warning(f"Не удалось добавить агентов в CRM: {e}")

            response = {
                "success": True,
                "message": "Канал создан успешно",
                "channel_id": channel_id
            }
            if agents_added:
                response["agents_added"] = agents_added
            if agents_errors:
                response["agents_errors"] = agents_errors
            return response
        else:
            raise HTTPException(400, "Ошибка создания канала")

    except Exception as e:
        logger.error(f"Ошибка создания канала: {e}")
        raise HTTPException(500, str(e))


@app.put("/api/channels/{channel_id}")
async def update_channel(channel_id: str, data: ChannelUpdateRequest):
    """Обновить существующий канал"""
    try:
        # Получаем существующий канал
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")
        
        # Обновляем поля
        if data.name is not None:
            channel.name = data.name
        if data.telegram_id is not None:
            channel.telegram_id = data.telegram_id
        if data.input_sources is not None:
            channel.input_sources = data.input_sources
        if data.enabled is not None:
            channel.enabled = data.enabled
        
        # Обновляем фильтры
        if data.include_keywords is not None:
            channel.filters.include_keywords = data.include_keywords
        if data.exclude_keywords is not None:
            channel.filters.exclude_keywords = data.exclude_keywords
        
        # Обновляем CRM поля
        if data.crm_enabled is not None:
            channel.crm_enabled = data.crm_enabled
        if data.crm_group_id is not None:
            channel.crm_group_id = data.crm_group_id
        
        # Обновляем агентов
        if data.agents is not None:
            from config_manager import AgentConfig
            agents = []
            for agent_req in data.agents:
                agents.append(AgentConfig(
                    phone=agent_req.phone,
                    session_name=agent_req.session_name
                ))
            channel.agents = agents

        if data.auto_response_enabled is not None:
            channel.auto_response_enabled = data.auto_response_enabled
        if data.auto_response_template is not None:
            channel.auto_response_template = data.auto_response_template
        
        # Сохраняем
        if config_manager.update_channel(channel_id, channel):
            # Автодобавление агентов в CRM группу
            agents_added = []
            agents_errors = []
            if channel.crm_enabled and channel.crm_group_id and channel.agents:
                try:
                    add_result = await _add_agents_to_crm_group(channel.crm_group_id, channel.agents)
                    agents_added = add_result.get('invited', [])
                    agents_errors = add_result.get('errors', [])
                except Exception as e:
                    logger.warning(f"Не удалось добавить агентов в CRM: {e}")

            response = {
                "success": True,
                "message": "Канал обновлен успешно"
            }
            if agents_added:
                response["agents_added"] = agents_added
            if agents_errors:
                response["agents_errors"] = agents_errors
            return response
        else:
            raise HTTPException(400, "Ошибка обновления канала")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обновления канала: {e}")
        raise HTTPException(500, str(e))


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str, delete_telegram: bool = True):
    """
    Удалить канал с опциональной очисткой Telegram сущностей.

    Args:
        channel_id: ID канала в конфиге
        delete_telegram: Если True, удаляет также Telegram канал и CRM группу
    """
    try:
        # Получаем данные канала ДО удаления
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")

        deleted_entities = []
        pending_entities = []
        errors = []

        if delete_telegram:
            # Удаляем Telegram канал (для уведомлений)
            if channel.telegram_id:
                result = await execute_telegram_deletion(channel.telegram_id, "Telegram канал")
                if result:
                    deleted_entities.append(f"Telegram канал {channel.telegram_id}")
                elif any(t['entity_id'] == channel.telegram_id for t in pending_telegram_deletions):
                    pending_entities.append(f"Telegram канал {channel.telegram_id}")
                else:
                    errors.append(f"Не удалось удалить Telegram канал {channel.telegram_id}")

            # Удаляем CRM группу
            if channel.crm_enabled and channel.crm_group_id:
                result = await execute_telegram_deletion(channel.crm_group_id, "CRM группа")
                if result:
                    deleted_entities.append(f"CRM группа {channel.crm_group_id}")
                elif any(t['entity_id'] == channel.crm_group_id for t in pending_telegram_deletions):
                    pending_entities.append(f"CRM группа {channel.crm_group_id}")
                else:
                    errors.append(f"Не удалось удалить CRM группу {channel.crm_group_id}")

        # Удаляем записи из базы данных
        if channel.crm_enabled and channel.crm_group_id:
            try:
                import aiosqlite
                from config import config
                # Используем отдельное соединение с timeout чтобы избежать lock
                async with aiosqlite.connect(config.DATABASE_PATH, timeout=10) as conn:
                    cursor = await conn.execute(
                        "DELETE FROM crm_topic_contacts WHERE group_id = ?",
                        (channel.crm_group_id,)
                    )
                    await conn.commit()
                    deleted_count = cursor.rowcount
                    if deleted_count > 0:
                        deleted_entities.append(f"{deleted_count} записей из БД")
                        logger.info(f"Удалено {deleted_count} записей crm_topic_contacts")
            except Exception as e:
                error_msg = f"Ошибка очистки БД: {e}"
                logger.warning(error_msg)
                errors.append(error_msg)

        # Удаляем из конфига
        if config_manager.delete_channel(channel_id):
            deleted_entities.append("конфигурация канала")

            message = "Канал удален успешно"
            if pending_entities:
                message += " (некоторые Telegram сущности будут удалены позже из-за rate limit)"

            return {
                "success": True,
                "message": message,
                "deleted": deleted_entities,
                "pending": pending_entities if pending_entities else None,
                "errors": errors if errors else None
            }
        else:
            raise HTTPException(500, "Не удалось удалить канал из конфигурации")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления канала: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/stats")
async def get_stats():
    """Получить общую статистику"""
    try:
        channels = config_manager.load()
        
        total_sources = config_manager.get_all_input_sources()
        enabled_channels = [ch for ch in channels if ch.enabled]
        
        # Подсчет агентов
        total_agents = 0
        crm_enabled_channels = 0
        for ch in channels:
            if ch.crm_enabled:
                crm_enabled_channels += 1
                total_agents += len(ch.agents)
        
        return {
            "success": True,
            "stats": {
                "total_channels": len(channels),
                "enabled_channels": len(enabled_channels),
                "total_input_sources": len(total_sources),
                "total_agents": total_agents,
                "crm_enabled_channels": crm_enabled_channels,
                "channels_breakdown": [
                    {
                        "name": ch.name,
                        "sources_count": len(ch.input_sources),
                        "enabled": ch.enabled,
                        "crm_enabled": ch.crm_enabled,
                        "agents_count": len(ch.agents) if ch.crm_enabled else 0
                    }
                    for ch in channels
                ]
            }
        }
    
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(500, str(e))


# Глобальное состояние бота (устанавливается из main_multi.py)
bot_state = {
    "status": "unknown",
    "error": None,
    "user_info": None,
    "flood_wait_until": None
}


# ==================== Bot Status Endpoints ====================

@app.get("/api/bot/status")
async def get_bot_status():
    """Получить статус бота"""
    import time

    result = {
        "status": bot_state.get("status", "unknown"),
        "error": bot_state.get("error"),
        "user_info": bot_state.get("user_info")
    }

    # Если FloodWait - показываем оставшееся время
    flood_until = bot_state.get("flood_wait_until")
    if flood_until:
        remaining = int(flood_until - time.time())
        if remaining > 0:
            result["flood_wait_remaining"] = remaining
            result["flood_wait_remaining_human"] = f"{remaining // 3600}ч {(remaining % 3600) // 60}м"
        else:
            result["flood_wait_remaining"] = 0

    return result


@app.post("/api/bot/upload-session")
async def upload_session(request: Request):
    """Загрузить файл сессии (base64)"""
    try:
        data = await request.json()
        session_b64 = data.get("session_base64")

        if not session_b64:
            raise HTTPException(400, "session_base64 is required")

        import base64
        session_data = base64.b64decode(session_b64)

        # Сохраняем сессию
        session_path = Path("bot_session.session")
        session_path.write_bytes(session_data)

        logger.info("Сессия загружена через API")

        return {"success": True, "message": "Сессия загружена. Бот перезапустится автоматически."}

    except Exception as e:
        logger.error(f"Ошибка загрузки сессии: {e}")
        raise HTTPException(500, str(e))


# ==================== Bot Authentication Endpoints ====================

class BotAuthInitRequest(BaseModel):
    """Запрос на инициализацию аутентификации бота"""
    phone: str


class BotAuthVerifyCodeRequest(BaseModel):
    """Запрос на проверку кода аутентификации"""
    code: str


class BotAuthVerifyPasswordRequest(BaseModel):
    """Запрос на проверку 2FA пароля"""
    password: str


@app.get("/auth")
async def auth_page(request: Request):
    """Страница аутентификации бота"""
    return templates.TemplateResponse(
        "bot_auth.html",
        {
            "request": request,
            "bot_state": bot_state
        }
    )


@app.post("/api/bot/auth/init")
async def init_bot_auth(request: BotAuthInitRequest):
    """Инициирует аутентификацию основного бота"""
    try:
        logger.info(f"Инициализация аутентификации бота для {request.phone}")
        result = await bot_auth_manager.init_auth(request.phone)
        return result
    except Exception as e:
        logger.error(f"Ошибка инициализации аутентификации бота: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/bot/auth/verify-code")
async def verify_bot_code(request: BotAuthVerifyCodeRequest):
    """Проверяет код подтверждения"""
    try:
        logger.info("Проверка кода для основного бота")
        result = await bot_auth_manager.verify_code(request.code)

        # Если успешно - обновляем bot_state чтобы main_multi.py увидел изменение
        if result.get("authenticated"):
            bot_state["status"] = "authenticated"
            bot_state["error"] = None

        return result
    except Exception as e:
        logger.error(f"Ошибка проверки кода бота: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/bot/auth/verify-password")
async def verify_bot_password(request: BotAuthVerifyPasswordRequest):
    """Проверяет 2FA пароль"""
    try:
        logger.info("Проверка 2FA пароля для основного бота")
        result = await bot_auth_manager.verify_password(request.password)

        # Если успешно - обновляем bot_state чтобы main_multi.py увидел изменение
        if result.get("authenticated"):
            bot_state["status"] = "authenticated"
            bot_state["error"] = None

        return result
    except Exception as e:
        logger.error(f"Ошибка проверки 2FA пароля бота: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/bot/auth/status")
async def check_bot_auth_status():
    """Проверяет статус аутентификации основного бота"""
    try:
        result = await bot_auth_manager.check_session_status()
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки статуса бота: {e}")
        raise HTTPException(500, str(e))


@app.delete("/api/bot/auth/pending")
async def cleanup_bot_pending_auth():
    """Очищает незавершенную аутентификацию бота"""
    try:
        await bot_auth_manager.cleanup()
        return {"success": True, "message": "Pending auth cleaned up"}
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        raise HTTPException(500, str(e))


# Startup event
@app.on_event("startup")
async def startup_event():
    """Запуск веб-приложения"""
    logger.info("Web interface starting...")

    # Создаем необходимые директории
    Path("configs").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    Path("sessions").mkdir(exist_ok=True)

    # Загружаем конфигурацию
    config_manager.load()

    logger.info(f"Loaded {len(config_manager.channels)} channels")

    # Запускаем worker для отложенных удалений
    global deletion_worker_started
    if not deletion_worker_started:
        deletion_worker_started = True
        asyncio.create_task(deletion_worker())


async def deletion_worker():
    """Фоновый worker для отложенных удалений Telegram сущностей"""
    logger.info("🗑️ Deletion worker started")

    while True:
        try:
            await asyncio.sleep(10)  # Проверяем каждые 10 секунд

            if not pending_telegram_deletions:
                continue

            now = time.time()
            tasks_to_process = []

            # Находим задачи, которые пора выполнить
            for task in pending_telegram_deletions[:]:
                if task['retry_after'] <= now:
                    tasks_to_process.append(task)
                    pending_telegram_deletions.remove(task)

            for task in tasks_to_process:
                logger.info(f"🔄 Повторная попытка удаления: {task['type']} {task['entity_id']}")
                await execute_telegram_deletion(task['entity_id'], task['type'])

        except Exception as e:
            logger.error(f"Ошибка в deletion worker: {e}")


async def execute_telegram_deletion(entity_id: int, entity_type: str) -> bool:
    """Выполняет удаление Telegram сущности с обработкой rate limit"""
    try:
        from telethon.tl.functions.channels import DeleteChannelRequest
        from telethon.errors import FloodWaitError
        from telethon import TelegramClient
        from telethon.sessions import SQLiteSession
        from config import config
        import shutil

        # Используем копию сессии чтобы не блокировать основного бота
        web_session_path = f"sessions/{config.SESSION_NAME}_web"
        original_session_path = f"{config.SESSION_NAME}.session"  # Сессия в корне проекта

        # Копируем сессию если её нет или она устарела
        if os.path.exists(original_session_path):
            if not os.path.exists(f"{web_session_path}.session") or \
               os.path.getmtime(original_session_path) > os.path.getmtime(f"{web_session_path}.session"):
                shutil.copy2(original_session_path, f"{web_session_path}.session")
        else:
            logger.error(f"Сессия бота не найдена: {original_session_path}")
            return False

        client = TelegramClient(web_session_path, config.API_ID, config.API_HASH)

        await client.connect()

        if not await client.is_user_authorized():
            logger.warning("Бот не авторизован для удаления")
            await client.disconnect()
            return False

        try:
            await client(DeleteChannelRequest(entity_id))
            logger.info(f"✅ Удалён {entity_type}: {entity_id}")
            await client.disconnect()
            return True

        except FloodWaitError as e:
            # Rate limit - добавляем в очередь на повторную попытку
            retry_after = time.time() + e.seconds + 5  # +5 секунд запаса
            pending_telegram_deletions.append({
                'entity_id': entity_id,
                'type': entity_type,
                'retry_after': retry_after
            })
            logger.warning(f"⏳ Rate limit для {entity_type} {entity_id}, повтор через {e.seconds} сек")
            await client.disconnect()
            return False

        except Exception as e:
            logger.error(f"Ошибка удаления {entity_type} {entity_id}: {e}")
            await client.disconnect()
            return False

    except Exception as e:
        logger.error(f"Ошибка подключения для удаления: {e}")
        return False


@app.get("/api/channels/{channel_id}/agents")
async def get_channel_agents(channel_id: str):
    """Получить список агентов канала"""
    try:
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")
        
        agents_data = []
        for agent in channel.agents:
            agents_data.append({
                "phone": agent.phone,
                "session_name": agent.session_name
            })
        
        return {"agents": agents_data}
    
    except Exception as e:
        logger.error(f"Ошибка получения агентов: {e}")
        return {"error": str(e)}, 500


@app.post("/api/channels/{channel_id}/agents")
async def add_channel_agent(channel_id: str, agent: AgentRequest):
    """Добавить агента к каналу"""
    try:
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")
        
        # Проверяем что агент с таким session_name еще не существует
        for existing_agent in channel.agents:
            if existing_agent.session_name == agent.session_name:
                raise HTTPException(400, f"Агент с session_name '{agent.session_name}' уже существует")
        
        # Добавляем нового агента
        from config_manager import AgentConfig
        new_agent = AgentConfig(
            phone=agent.phone,
            session_name=agent.session_name
        )
        channel.agents.append(new_agent)
        
        # Сохраняем
        if config_manager.update_channel(channel_id, channel):
            return {"success": True, "message": "Агент добавлен"}
        else:
            raise HTTPException(500, "Не удалось сохранить конфигурацию")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка добавления агента: {e}")
        raise HTTPException(500, str(e))


@app.delete("/api/channels/{channel_id}/agents/{session_name}")
async def remove_channel_agent(channel_id: str, session_name: str):
    """Удалить агента из канала"""
    try:
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")
        
        # Ищем и удаляем агента
        original_count = len(channel.agents)
        channel.agents = [agent for agent in channel.agents if agent.session_name != session_name]
        
        if len(channel.agents) == original_count:
            raise HTTPException(404, f"Агент с session_name '{session_name}' не найден")
        
        # Сохраняем
        if config_manager.update_channel(channel_id, channel):
            return {"success": True, "message": "Агент удален"}
        else:
            raise HTTPException(500, "Не удалось сохранить конфигурацию")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления агента: {e}")
        raise HTTPException(500, str(e))


# ==================== Agent Authentication Endpoints ====================

class AgentAuthInitRequest(BaseModel):
    """Запрос на инициализацию аутентификации агента"""
    phone: str
    session_name: str

class AgentAuthVerifyRequest(BaseModel):
    """Запрос на проверку кода аутентификации"""
    session_name: str
    code: str


class AgentAuthPasswordRequest(BaseModel):
    """Запрос на проверку 2FA пароля"""
    session_name: str
    password: str


@app.post("/api/agents/init")
async def init_agent_auth(request: AgentAuthInitRequest):
    """Инициирует аутентификацию агента"""
    try:
        logger.info(f"Инициализация аутентификации для {request.phone}")
        result = await agent_auth_manager.init_auth(request.phone, request.session_name)
        return result
    except Exception as e:
        logger.error(f"Ошибка инициализации аутентификации: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/agents/verify")
async def verify_agent_code(request: AgentAuthVerifyRequest):
    """Проверяет код подтверждения"""
    try:
        logger.info(f"Проверка кода для сессии {request.session_name}")
        result = await agent_auth_manager.verify_code(request.session_name, request.code)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки кода: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/agents/verify-password")
async def verify_agent_password(request: AgentAuthPasswordRequest):
    """Проверяет 2FA пароль"""
    try:
        logger.info(f"Проверка 2FA пароля для сессии {request.session_name}")
        result = await agent_auth_manager.verify_password(request.session_name, request.password)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки 2FA пароля: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/agents/{session_name}/status")
async def check_agent_status(session_name: str):
    """Проверяет статус сессии агента"""
    try:
        result = await agent_auth_manager.check_session_status(session_name)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки статуса агента: {e}")
        raise HTTPException(500, str(e))


@app.delete("/api/agents/{session_name}/pending")
async def cleanup_pending_auth(session_name: str):
    """Очищает незавершенную аутентификацию"""
    try:
        await agent_auth_manager.cleanup_pending(session_name)
        return {"success": True, "message": "Pending auth cleaned up"}
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        raise HTTPException(500, str(e))


# ==================== Auto-create Telegram Entities ====================

class CreateChannelRequest(BaseModel):
    """Запрос на создание канала"""
    title: str
    about: str = ""


class CreateCrmGroupRequest(BaseModel):
    """Запрос на создание CRM группы с топиками"""
    title: str
    about: str = ""
    owner_username: str = ""  # Username владельца для автодобавления
    channel_id: str = ""  # ID канала для получения списка агентов


@app.post("/api/telegram/create-channel")
async def create_telegram_channel(request: CreateChannelRequest):
    """
    Создаёт приватный канал в Telegram для уведомлений.
    Требует авторизованную сессию бота.
    """
    try:
        from telethon.tl.functions.channels import CreateChannelRequest as TgCreateChannel
        from auth.base import TimeoutSQLiteSession
        from telethon import TelegramClient
        from config import config

        # Проверяем что бот авторизован
        session_status = await bot_auth_manager.check_session_status()
        if not session_status.get("authenticated"):
            return {
                "success": False,
                "message": "Бот не авторизован. Сначала пройдите авторизацию на странице /auth"
            }

        # Создаём клиент с таймаутом для SQLite
        session = TimeoutSQLiteSession(config.SESSION_NAME)
        client = TelegramClient(session, config.API_ID, config.API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return {
                "success": False,
                "message": "Сессия бота недействительна. Пройдите авторизацию заново."
            }

        try:
            # Создаём канал (megagroup=False для канала)
            result = await client(TgCreateChannel(
                title=request.title,
                about=request.about,
                broadcast=True,  # broadcast=True для канала
                megagroup=False
            ))

            # Получаем ID созданного канала
            channel = result.chats[0]
            channel_id = -1000000000000 - channel.id  # Формат ID для каналов

            logger.info(f"Канал '{request.title}' создан с ID {channel_id}")

            return {
                "success": True,
                "message": f"Канал '{request.title}' успешно создан!",
                "channel_id": channel_id,
                "channel_title": request.title
            }

        finally:
            await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка создания канала: {e}")
        return {
            "success": False,
            "message": f"Ошибка создания канала: {str(e)}"
        }


@app.post("/api/telegram/create-crm-group")
async def create_telegram_crm_group(request: CreateCrmGroupRequest):
    """
    Создаёт группу с включенными топиками для CRM.
    Требует авторизованную сессию бота.
    """
    logger.info(f"📋 Создание CRM группы: title={request.title}, owner={request.owner_username}, channel_id={request.channel_id}")
    try:
        from telethon.tl.functions.channels import CreateChannelRequest as TgCreateChannel
        from telethon.tl.functions.channels import ToggleForumRequest
        from auth.base import TimeoutSQLiteSession
        from telethon import TelegramClient
        from config import config

        # Проверяем что бот авторизован
        session_status = await bot_auth_manager.check_session_status()
        if not session_status.get("authenticated"):
            return {
                "success": False,
                "message": "Бот не авторизован. Сначала пройдите авторизацию на странице /auth"
            }

        # Создаём клиент с таймаутом для SQLite
        session = TimeoutSQLiteSession(config.SESSION_NAME)
        client = TelegramClient(session, config.API_ID, config.API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return {
                "success": False,
                "message": "Сессия бота недействительна. Пройдите авторизацию заново."
            }

        try:
            # Создаём супергруппу (megagroup=True)
            result = await client(TgCreateChannel(
                title=request.title,
                about=request.about,
                broadcast=False,
                megagroup=True  # megagroup=True для группы
            ))

            # Получаем созданную группу
            group = result.chats[0]

            # Включаем форум (топики)
            try:
                await client(ToggleForumRequest(
                    channel=group,
                    enabled=True,
                    tabs=[]  # Пустой список табов
                ))
                topics_enabled = True
            except Exception as e:
                logger.warning(f"Не удалось включить топики: {e}")
                topics_enabled = False

            # Формируем ID
            group_id = -1000000000000 - group.id

            # Приглашаем владельца и агентов
            invited_users = []
            invite_errors = []

            from telethon.tl.functions.channels import InviteToChannelRequest
            from telethon.tl.functions.messages import ExportChatInviteRequest

            # 1. Приглашаем владельца (отправляем инвайт-ссылку в личку)
            if request.owner_username:
                try:
                    owner_username = request.owner_username.lstrip('@')
                    owner_entity = await client.get_entity(owner_username)

                    # Создаём инвайт-ссылку
                    invite = await client(ExportChatInviteRequest(
                        peer=group,
                        expire_date=None,
                        usage_limit=1,  # Одноразовая ссылка
                        title="CRM доступ"
                    ))
                    invite_link = invite.link

                    # Отправляем ссылку в личку владельцу
                    await client.send_message(
                        owner_entity,
                        f"🔗 **Приглашение в CRM группу**\n\n"
                        f"Группа: **{request.title}**\n"
                        f"Ссылка для вступления: {invite_link}\n\n"
                        f"_Ссылка одноразовая_"
                    )
                    invited_users.append(f"@{owner_username} (ссылка отправлена)")
                    logger.info(f"Инвайт-ссылка отправлена владельцу @{owner_username}")
                except Exception as e:
                    invite_errors.append(f"Владелец @{owner_username}: {str(e)}")
                    logger.warning(f"Не удалось отправить инвайт владельцу: {e}")

            # 2. Приглашаем агентов из канала (если указан)
            if request.channel_id:
                try:
                    channel = config_manager.get_channel(request.channel_id)
                    if channel and channel.agents:
                        for agent_session in channel.agents:
                            try:
                                # Создаём клиент агента чтобы получить его entity
                                agent_session_path = f"sessions/{agent_session}"
                                from auth.base import TimeoutSQLiteSession
                                agent_tg_session = TimeoutSQLiteSession(agent_session_path)
                                agent_client = TelegramClient(agent_tg_session, config.API_ID, config.API_HASH)
                                await agent_client.connect()

                                if await agent_client.is_user_authorized():
                                    agent_me = await agent_client.get_me()
                                    # Приглашаем агента в группу через основной клиент
                                    await client(InviteToChannelRequest(
                                        channel=group,
                                        users=[agent_me.id]
                                    ))
                                    agent_name = agent_me.username or agent_me.first_name
                                    invited_users.append(f"@{agent_name} (агент)")
                                    logger.info(f"Агент {agent_session} добавлен в CRM группу")

                                await agent_client.disconnect()
                            except Exception as e:
                                invite_errors.append(f"Агент {agent_session}: {str(e)}")
                                logger.warning(f"Не удалось добавить агента {agent_session}: {e}")
                except Exception as e:
                    logger.warning(f"Ошибка при добавлении агентов: {e}")

            logger.info(f"CRM группа '{request.title}' создана с ID {group_id}, топики: {topics_enabled}")

            result_message = f"Группа '{request.title}' успешно создана!"
            if invited_users:
                result_message += f" Добавлены: {', '.join(invited_users)}"

            return {
                "success": True,
                "message": result_message,
                "group_id": group_id,
                "group_title": request.title,
                "topics_enabled": topics_enabled,
                "invited_users": invited_users,
                "invite_errors": invite_errors,
                "note": "Топики включены автоматически" if topics_enabled else "Включите топики вручную в настройках группы"
            }

        finally:
            await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка создания CRM группы: {e}")
        return {
            "success": False,
            "message": f"Ошибка создания группы: {str(e)}"
        }


class AddAgentsToCrmRequest(BaseModel):
    """Запрос на добавление агентов в CRM группу"""
    crm_group_id: int
    channel_id: str


@app.post("/api/telegram/add-agents-to-crm")
async def add_agents_to_crm(request: AddAgentsToCrmRequest):
    """Добавляет агентов канала в существующую CRM группу"""
    logger.info(f"📋 Добавление агентов в CRM группу: group_id={request.crm_group_id}, channel_id={request.channel_id}")
    try:
        from telethon.tl.functions.channels import InviteToChannelRequest
        from auth.base import TimeoutSQLiteSession
        from telethon import TelegramClient
        from config import config

        # Проверяем что бот авторизован
        session_status = await bot_auth_manager.check_session_status()
        if not session_status.get("authenticated"):
            return {"success": False, "message": "Бот не авторизован"}

        # Получаем канал и его агентов
        channel = config_manager.get_channel(request.channel_id)
        if not channel or not channel.agents:
            return {"success": False, "message": "Канал не найден или нет агентов"}

        # Создаём клиент бота
        session = TimeoutSQLiteSession(config.SESSION_NAME)
        client = TelegramClient(session, config.API_ID, config.API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return {"success": False, "message": "Сессия бота недействительна"}

        try:
            # Получаем группу
            group = await client.get_entity(request.crm_group_id)
            invited = []
            errors = []

            for agent_data in channel.agents:
                agent_session = agent_data.get('session_name') if isinstance(agent_data, dict) else agent_data
                try:
                    agent_session_path = f"sessions/{agent_session}"
                    agent_tg_session = TimeoutSQLiteSession(agent_session_path)
                    agent_client = TelegramClient(agent_tg_session, config.API_ID, config.API_HASH)
                    await agent_client.connect()

                    if await agent_client.is_user_authorized():
                        agent_me = await agent_client.get_me()
                        await client(InviteToChannelRequest(
                            channel=group,
                            users=[agent_me.id]
                        ))
                        agent_name = agent_me.username or agent_me.first_name
                        invited.append(f"@{agent_name}")
                        logger.info(f"Агент {agent_session} добавлен в CRM группу")

                    await agent_client.disconnect()
                except Exception as e:
                    errors.append(f"{agent_session}: {str(e)}")
                    logger.warning(f"Не удалось добавить агента {agent_session}: {e}")

            return {
                "success": True,
                "message": f"Добавлено агентов: {len(invited)}",
                "invited": invited,
                "errors": errors
            }

        finally:
            await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка добавления агентов: {e}")
        return {"success": False, "message": str(e)}


# ==================== Wizard API Endpoints ====================

@app.get("/api/agents")
async def get_agents_list():
    """Получить список всех доступных агентов"""
    try:
        agents = get_available_agents()
        return {"success": True, "agents": agents}
    except Exception as e:
        logger.error(f"Ошибка получения списка агентов: {e}")
        return {"success": False, "message": str(e), "agents": []}


@app.get("/api/templates")
async def get_templates():
    """Получить список сохранённых шаблонов автоответов"""
    try:
        templates = load_templates()
        return {"success": True, "templates": templates}
    except Exception as e:
        logger.error(f"Ошибка загрузки шаблонов: {e}")
        return {"success": False, "message": str(e), "templates": []}


class SaveTemplateRequest(BaseModel):
    name: str
    text: str


@app.post("/api/templates")
async def save_template_endpoint(request: SaveTemplateRequest):
    """Сохранить новый шаблон автоответа"""
    try:
        templates = load_templates()

        # Генерируем уникальный ID
        import uuid
        new_template = {
            "id": f"template_{uuid.uuid4().hex[:8]}",
            "name": request.name,
            "text": request.text
        }
        templates.append(new_template)
        save_templates(templates)

        return {"success": True, "template": new_template}
    except Exception as e:
        logger.error(f"Ошибка сохранения шаблона: {e}")
        return {"success": False, "message": str(e)}


@app.get("/api/source-lists")
async def get_source_lists():
    """Получить сохранённые списки каналов-источников"""
    try:
        lists = load_source_lists()
        return {"success": True, "lists": lists}
    except Exception as e:
        logger.error(f"Ошибка загрузки списков источников: {e}")
        return {"success": False, "message": str(e), "lists": []}


class SaveSourceListRequest(BaseModel):
    name: str
    sources: List[str]


@app.post("/api/source-lists")
async def save_source_list_endpoint(request: SaveSourceListRequest):
    """Сохранить новый список каналов-источников"""
    try:
        lists = load_source_lists()

        import uuid
        new_list = {
            "id": f"list_{uuid.uuid4().hex[:8]}",
            "name": request.name,
            "sources": request.sources
        }
        lists.append(new_list)
        save_source_lists(lists)

        return {"success": True, "list": new_list}
    except Exception as e:
        logger.error(f"Ошибка сохранения списка источников: {e}")
        return {"success": False, "message": str(e)}


# Aliases for agent auth endpoints (frontend uses different paths)
class AgentAuthStartRequest(BaseModel):
    phone: str


@app.post("/api/agents/auth/start")
async def agent_auth_start(request: AgentAuthStartRequest):
    """Начать авторизацию агента (alias for /api/agents/init)"""
    try:
        # Генерируем имя сессии из номера телефона
        import re
        phone_digits = re.sub(r'\D', '', request.phone)
        session_name = f"agent_{phone_digits[-4:]}"

        logger.info(f"Начало авторизации агента: phone={request.phone}, session={session_name}")
        result = await agent_auth_manager.init_auth(request.phone, session_name)

        if result.get("success") or result.get("code_sent"):
            return {"success": True, "session_name": session_name, "message": "Код отправлен"}
        return result
    except Exception as e:
        logger.error(f"Ошибка инициализации авторизации агента: {e}")
        return {"success": False, "message": str(e)}


class AgentAuthVerifyRequest(BaseModel):
    session_name: str
    code: str


@app.post("/api/agents/auth/verify")
async def agent_auth_verify(request: AgentAuthVerifyRequest):
    """Проверить код авторизации агента"""
    try:
        logger.info(f"Проверка кода для агента: session={request.session_name}")
        result = await agent_auth_manager.verify_code(request.session_name, request.code)

        if result.get("authenticated"):
            return {"success": True, "name": result.get("name", "Агент")}
        elif result.get("requires_2fa"):
            return {"success": False, "requires_2fa": True, "message": "Требуется 2FA пароль"}
        return {"success": False, "message": result.get("error", "Неверный код")}
    except Exception as e:
        logger.error(f"Ошибка проверки кода: {e}")
        return {"success": False, "message": str(e)}


class AgentAuth2FARequest(BaseModel):
    session_name: str
    password: str


@app.post("/api/agents/auth/2fa")
async def agent_auth_2fa(request: AgentAuth2FARequest):
    """Проверить 2FA пароль агента"""
    try:
        logger.info(f"Проверка 2FA для агента: session={request.session_name}")
        result = await agent_auth_manager.verify_password(request.session_name, request.password)

        if result.get("authenticated"):
            return {"success": True, "name": result.get("name", "Агент")}
        return {"success": False, "message": result.get("error", "Неверный пароль")}
    except Exception as e:
        logger.error(f"Ошибка проверки 2FA: {e}")
        return {"success": False, "message": str(e)}


# Full channel creation endpoint
class FullChannelCreateRequest(BaseModel):
    name: str
    input_sources: List[str]
    agents: List[str]  # List of session names
    auto_response_template: str = ""
    include_keywords: List[str] = []
    exclude_keywords: List[str] = []


@app.post("/api/channels/create-full")
async def create_channel_full(data: FullChannelCreateRequest):
    """
    Создать канал с полной автоматизацией:
    1. Создаёт Telegram канал для уведомлений
    2. Создаёт CRM группу с топиками
    3. Добавляет агентов в CRM
    4. Сохраняет конфигурацию
    """
    logger.info(f"📋 Создание полного канала: name={data.name}")

    try:
        from telethon.tl.functions.channels import CreateChannelRequest as TgCreateChannel
        from telethon.tl.functions.channels import ToggleForumRequest, InviteToChannelRequest
        from telethon.tl.functions.messages import ExportChatInviteRequest
        from auth.base import TimeoutSQLiteSession
        from telethon import TelegramClient
        from config import config

        # Проверяем что бот авторизован
        session_status = await bot_auth_manager.check_session_status()
        if not session_status.get("authenticated"):
            return {
                "success": False,
                "message": "Бот не авторизован. Сначала пройдите авторизацию на странице /auth"
            }

        # Создаём клиент
        session = TimeoutSQLiteSession(config.SESSION_NAME)
        client = TelegramClient(session, config.API_ID, config.API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return {"success": False, "message": "Сессия бота недействительна"}

        try:
            # 1. Создаём канал для уведомлений
            logger.info("Создание канала для уведомлений...")
            notification_result = await client(TgCreateChannel(
                title=f"{data.name} - Вакансии",
                about=f"Уведомления о вакансиях: {data.name}",
                broadcast=True,
                megagroup=False
            ))
            notification_channel = notification_result.chats[0]
            notification_channel_id = -1000000000000 - notification_channel.id
            logger.info(f"Канал уведомлений создан: {notification_channel_id}")

            # 2. Создаём CRM группу с топиками
            logger.info("Создание CRM группы...")
            crm_result = await client(TgCreateChannel(
                title=f"{data.name} - CRM",
                about=f"CRM для управления откликами: {data.name}",
                broadcast=False,
                megagroup=True
            ))
            crm_group = crm_result.chats[0]
            crm_group_id = -1000000000000 - crm_group.id

            # Включаем топики
            topics_enabled = False
            try:
                await client(ToggleForumRequest(
                    channel=crm_group,
                    enabled=True,
                    tabs=[]
                ))
                topics_enabled = True
                logger.info("Топики включены")
            except Exception as e:
                logger.warning(f"Не удалось включить топики: {e}")

            logger.info(f"CRM группа создана: {crm_group_id}")

            # 3. Добавляем агентов в CRM
            agents_invited = []
            agents_errors = []

            for agent_session in data.agents:
                try:
                    agent_session_path = f"sessions/{agent_session}"
                    agent_tg_session = TimeoutSQLiteSession(agent_session_path)
                    agent_client = TelegramClient(agent_tg_session, config.API_ID, config.API_HASH)
                    await agent_client.connect()

                    if await agent_client.is_user_authorized():
                        agent_me = await agent_client.get_me()
                        try:
                            await client(InviteToChannelRequest(
                                channel=crm_group,
                                users=[agent_me.id]
                            ))
                            agent_name = agent_me.username or agent_me.first_name
                            agents_invited.append(f"@{agent_name}")
                            logger.info(f"Агент {agent_session} добавлен в CRM")
                        except Exception as invite_err:
                            if "USER_ALREADY_PARTICIPANT" in str(invite_err):
                                agents_invited.append(f"@{agent_me.username or agent_me.first_name} (уже в группе)")
                            else:
                                agents_errors.append(f"{agent_session}: {str(invite_err)}")

                    await agent_client.disconnect()
                except Exception as e:
                    agents_errors.append(f"{agent_session}: {str(e)}")
                    logger.warning(f"Не удалось добавить агента {agent_session}: {e}")

            # 4. Отправляем инвайт владельцу (kbrejes)
            owner_invited = False
            try:
                owner_entity = await client.get_entity("kbrejes")
                invite = await client(ExportChatInviteRequest(
                    peer=crm_group,
                    expire_date=None,
                    usage_limit=1,
                    title="CRM доступ"
                ))
                await client.send_message(
                    owner_entity,
                    f"🔗 **Приглашение в CRM группу**\n\n"
                    f"Канал: **{data.name}**\n"
                    f"Группа CRM: **{data.name} - CRM**\n"
                    f"Ссылка: {invite.link}\n\n"
                    f"_Ссылка одноразовая_"
                )
                owner_invited = True
                logger.info("Инвайт отправлен владельцу")
            except Exception as e:
                logger.warning(f"Не удалось отправить инвайт владельцу: {e}")

            # 5. Сохраняем конфигурацию канала
            import uuid
            from config_manager import AgentConfig

            channel_id = f"channel_{uuid.uuid4().hex[:8]}"

            filters = FilterConfig(
                include_keywords=data.include_keywords,
                exclude_keywords=data.exclude_keywords,
                require_all_includes=False
            )

            agents_config = []
            for agent_session in data.agents:
                agents_config.append(AgentConfig(
                    phone="",
                    session_name=agent_session
                ))

            channel = ChannelConfig(
                id=channel_id,
                name=data.name,
                telegram_id=notification_channel_id,
                enabled=True,
                input_sources=data.input_sources,
                filters=filters,
                crm_enabled=True,
                crm_group_id=crm_group_id,
                agents=agents_config,
                auto_response_enabled=True,
                auto_response_template=data.auto_response_template or "Здравствуйте! Меня заинтересовала ваша вакансия. Буду рад обсудить детали!"
            )

            if config_manager.add_channel(channel):
                logger.info(f"Канал {channel_id} сохранён в конфигурации")

            return {
                "success": True,
                "message": f"Канал '{data.name}' создан успешно!",
                "channel_id": channel_id,
                "notification_channel_id": notification_channel_id,
                "crm_group_id": crm_group_id,
                "topics_enabled": topics_enabled,
                "agents_invited": agents_invited,
                "agents_errors": agents_errors,
                "owner_invited": owner_invited
            }

        finally:
            await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка создания полного канала: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        reload=True
    )

