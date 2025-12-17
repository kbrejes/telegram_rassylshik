"""FastAPI веб-интерфейс для управления Job Notification Bot"""
import os
from pathlib import Path
from typing import List, Optional
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

# Setup templates and static files
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Initialize config manager
config_manager = ConfigManager()


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
    """Страница создания нового канала"""
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
async def delete_channel(channel_id: str):
    """Удалить канал"""
    try:
        if config_manager.delete_channel(channel_id):
            return {
                "success": True,
                "message": "Канал удален успешно"
            }
        else:
            raise HTTPException(404, "Канал не найден")
    
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        reload=True
    )

