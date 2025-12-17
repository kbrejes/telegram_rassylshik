"""API для управления каналами"""
import uuid
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from config_manager import ConfigManager, ChannelConfig, FilterConfig, AgentConfig
from web.utils import ChannelCreateRequest, ChannelUpdateRequest, get_agent_client
from auth import bot_auth_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels", tags=["channels"])

config_manager = ConfigManager()

# Очередь отложенных удалений (импортируется из app.py)
pending_telegram_deletions: List = []


async def create_new_bot_client():
    """Создаёт новый клиент бота для веб-запросов"""
    from auth.base import TimeoutSQLiteSession
    from telethon import TelegramClient
    from config import config

    session = TimeoutSQLiteSession(config.SESSION_NAME)
    client = TelegramClient(session, config.API_ID, config.API_HASH)
    await client.connect()
    return client


async def _add_agents_to_crm_group(crm_group_id: int, agents: list) -> dict:
    """Добавить агентов в CRM группу"""
    from telethon.tl.functions.channels import InviteToChannelRequest

    invited = []
    errors = []

    session_status = await bot_auth_manager.check_session_status(quick_check=True)
    if not session_status.get("authenticated"):
        return {"invited": [], "errors": ["Бот не авторизован"]}

    client = await create_new_bot_client()

    if not await client.is_user_authorized():
        await client.disconnect()
        return {"invited": [], "errors": ["Сессия бота недействительна"]}

    try:
        group = await client.get_entity(crm_group_id)

        for agent_data in agents:
            if isinstance(agent_data, dict):
                agent_session = agent_data.get('session_name')
            elif hasattr(agent_data, 'session_name'):
                agent_session = agent_data.session_name
            else:
                agent_session = str(agent_data)

            if not agent_session:
                continue

            try:
                agent_client, _ = await get_agent_client(agent_session)

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


@router.get("")
async def get_channels():
    """Получить список всех каналов"""
    channels = config_manager.load()
    return {
        "success": True,
        "channels": [ch.to_dict() for ch in channels]
    }


@router.get("/{channel_id}")
async def get_channel(channel_id: str):
    """Получить конфигурацию канала"""
    channel = config_manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(404, "Канал не найден")
    return {"success": True, "channel": channel.to_dict()}


@router.post("")
async def create_channel(data: ChannelCreateRequest):
    """Создать новый канал"""
    try:
        channel_id = f"channel_{uuid.uuid4().hex[:8]}"

        filters = FilterConfig(
            include_keywords=data.include_keywords,
            exclude_keywords=data.exclude_keywords,
            require_all_includes=False
        )

        agents = [
            AgentConfig(phone=a.phone, session_name=a.session_name)
            for a in data.agents
        ]

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

        if config_manager.add_channel(channel):
            agents_added = []
            agents_errors = []
            if channel.crm_enabled and channel.crm_group_id and channel.agents:
                try:
                    add_result = await _add_agents_to_crm_group(channel.crm_group_id, channel.agents)
                    agents_added = add_result.get('invited', [])
                    agents_errors = add_result.get('errors', [])
                except Exception as e:
                    logger.warning(f"Не удалось добавить агентов в CRM: {e}")

            response = {"success": True, "message": "Канал создан успешно", "channel_id": channel_id}
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


@router.put("/{channel_id}")
async def update_channel(channel_id: str, data: ChannelUpdateRequest):
    """Обновить существующий канал"""
    try:
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
        if data.include_keywords is not None:
            channel.filters.include_keywords = data.include_keywords
        if data.exclude_keywords is not None:
            channel.filters.exclude_keywords = data.exclude_keywords
        if data.crm_enabled is not None:
            channel.crm_enabled = data.crm_enabled
        if data.crm_group_id is not None:
            channel.crm_group_id = data.crm_group_id

        if data.agents is not None:
            channel.agents = [
                AgentConfig(phone=a.phone, session_name=a.session_name)
                for a in data.agents
            ]

        if data.auto_response_enabled is not None:
            channel.auto_response_enabled = data.auto_response_enabled
        if data.auto_response_template is not None:
            channel.auto_response_template = data.auto_response_template

        if config_manager.update_channel(channel_id, channel):
            agents_added = []
            agents_errors = []
            if channel.crm_enabled and channel.crm_group_id and channel.agents:
                try:
                    add_result = await _add_agents_to_crm_group(channel.crm_group_id, channel.agents)
                    agents_added = add_result.get('invited', [])
                    agents_errors = add_result.get('errors', [])
                except Exception as e:
                    logger.warning(f"Не удалось добавить агентов в CRM: {e}")

            response = {"success": True, "message": "Канал обновлен успешно"}
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


@router.delete("/{channel_id}")
async def delete_channel(channel_id: str, delete_telegram: bool = True):
    """Удалить канал"""
    from web.app import execute_telegram_deletion, pending_telegram_deletions

    try:
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")

        deleted_entities = []
        pending_entities = []
        errors = []

        if delete_telegram:
            if channel.telegram_id:
                result = await execute_telegram_deletion(channel.telegram_id, "Telegram канал")
                if result:
                    deleted_entities.append(f"Telegram канал {channel.telegram_id}")
                elif any(t['entity_id'] == channel.telegram_id for t in pending_telegram_deletions):
                    pending_entities.append(f"Telegram канал {channel.telegram_id}")
                else:
                    errors.append(f"Не удалось удалить Telegram канал {channel.telegram_id}")

            if channel.crm_enabled and channel.crm_group_id:
                result = await execute_telegram_deletion(channel.crm_group_id, "CRM группа")
                if result:
                    deleted_entities.append(f"CRM группа {channel.crm_group_id}")
                elif any(t['entity_id'] == channel.crm_group_id for t in pending_telegram_deletions):
                    pending_entities.append(f"CRM группа {channel.crm_group_id}")
                else:
                    errors.append(f"Не удалось удалить CRM группу {channel.crm_group_id}")

        # Очистка БД
        if channel.crm_enabled and channel.crm_group_id:
            try:
                import aiosqlite
                from config import config
                async with aiosqlite.connect(config.DATABASE_PATH, timeout=10) as conn:
                    cursor = await conn.execute(
                        "DELETE FROM crm_topic_contacts WHERE group_id = ?",
                        (channel.crm_group_id,)
                    )
                    await conn.commit()
                    if cursor.rowcount > 0:
                        deleted_entities.append(f"{cursor.rowcount} записей из БД")
            except Exception as e:
                errors.append(f"Ошибка очистки БД: {e}")

        if config_manager.delete_channel(channel_id):
            deleted_entities.append("конфигурация канала")
            message = "Канал удален успешно"
            if pending_entities:
                message += " (некоторые Telegram сущности будут удалены позже)"

            return {
                "success": True,
                "message": message,
                "deleted": deleted_entities,
                "pending": pending_entities if pending_entities else None,
                "errors": errors if errors else None
            }
        else:
            raise HTTPException(500, "Не удалось удалить канал")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления канала: {e}")
        raise HTTPException(500, str(e))


@router.get("/{channel_id}/agents")
async def get_channel_agents(channel_id: str):
    """Получить агентов канала"""
    channel = config_manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(404, "Канал не найден")

    return {
        "success": True,
        "agents": [
            {"phone": a.phone, "session_name": a.session_name}
            for a in channel.agents
        ]
    }
