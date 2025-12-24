"""FastAPI веб-интерфейс для управления Job Notification Bot"""
import os
import time
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import sys
sys.path.append(str(Path(__file__).parent.parent))

from src.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Job Notification Bot - Management Interface")

# Setup static files
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Initialize config manager
config_manager = ConfigManager()

# Очередь отложенных удалений Telegram сущностей
pending_telegram_deletions: List[Dict[str, Any]] = []
deletion_worker_started = False


# ==================== Telegram Deletion Functions ====================

async def execute_telegram_deletion(entity_id: int, entity_type: str) -> bool:
    """Выполняет удаление Telegram сущности с обработкой rate limit"""
    client = None
    try:
        from telethon.tl.functions.channels import DeleteChannelRequest
        from telethon.errors import FloodWaitError
        from web.utils import get_or_create_bot_client

        # Используем StringSession чтобы не блокировать SQLite файл бота
        # (см. CLAUDE.md: "Each thread must have its own TelegramClient instance")
        client, should_disconnect = await get_or_create_bot_client()

        if not await client.is_user_authorized():
            logger.warning("Бот не авторизован для удаления")
            if should_disconnect:
                await client.disconnect()
            return False

        try:
            await client(DeleteChannelRequest(entity_id))
            logger.info(f"Удалён {entity_type}: {entity_id}")
            if should_disconnect:
                await client.disconnect()
            return True

        except FloodWaitError as e:
            retry_after = time.time() + e.seconds + 5
            pending_telegram_deletions.append({
                'entity_id': entity_id,
                'type': entity_type,
                'retry_after': retry_after
            })
            logger.warning(f"Rate limit для {entity_type} {entity_id}, повтор через {e.seconds} сек")
            if should_disconnect:
                await client.disconnect()
            return False

        except Exception as e:
            logger.error(f"Ошибка удаления {entity_type} {entity_id}: {e}")
            if should_disconnect:
                await client.disconnect()
            return False

    except Exception as e:
        logger.error(f"Ошибка подключения для удаления: {e}")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False


async def deletion_worker():
    """Фоновый worker для отложенных удалений Telegram сущностей"""
    logger.info("Deletion worker started")

    while True:
        try:
            await asyncio.sleep(10)

            if not pending_telegram_deletions:
                continue

            now = time.time()
            tasks_to_process = []

            for task in pending_telegram_deletions[:]:
                if task['retry_after'] <= now:
                    tasks_to_process.append(task)
                    pending_telegram_deletions.remove(task)

            for task in tasks_to_process:
                logger.info(f"Повторная попытка удаления: {task['type']} {task['entity_id']}")
                await execute_telegram_deletion(task['entity_id'], task['type'])

        except Exception as e:
            logger.error(f"Ошибка в deletion worker: {e}")


# ==================== Import and Register Routers ====================

from web.routes.pages import router as pages_router
from web.routes.channels import router as channels_router
from web.routes.agents import router as agents_router, templates_router, source_lists_router
from web.routes.auth import router as auth_router, bot_state
from web.routes.telegram import router as telegram_router
from web.routes.channel_creation import router as channel_creation_router

# Register all routers
app.include_router(pages_router)
app.include_router(channels_router)
app.include_router(agents_router)
app.include_router(templates_router)
app.include_router(source_lists_router)
app.include_router(auth_router)
app.include_router(telegram_router)
app.include_router(channel_creation_router)


# ==================== Stats Endpoint ====================

@app.get("/api/stats")
async def get_stats():
    """Получить общую статистику"""
    try:
        channels = config_manager.load()
        total_sources = config_manager.get_all_input_sources()
        enabled_channels = [ch for ch in channels if ch.enabled]

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
        from fastapi import HTTPException
        raise HTTPException(500, str(e))


# ==================== Startup Event ====================

@app.on_event("startup")
async def startup_event():
    """Запуск веб-приложения"""
    logger.info("Web interface starting...")

    # Создаем необходимые директории
    Path("configs").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    # sessions директория создается в session_config.py с абсолютным путем

    # Загружаем конфигурацию
    config_manager.load()
    logger.info(f"Loaded {len(config_manager.channels)} channels")

    # Запускаем worker для отложенных удалений
    global deletion_worker_started
    if not deletion_worker_started:
        deletion_worker_started = True
        asyncio.create_task(deletion_worker())


# ==================== Main ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        reload=True
    )
