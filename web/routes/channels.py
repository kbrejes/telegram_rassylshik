"""API для управления каналами"""
import uuid
import logging
from pathlib import Path
from typing import List, Set, Tuple
from fastapi import APIRouter, HTTPException

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.config_manager import ConfigManager, ChannelConfig, FilterConfig, AgentConfig, PromptsConfig
from web.utils import ChannelCreateRequest, ChannelUpdateRequest, get_agent_client, create_new_bot_client, PromptsRequest
from auth import bot_auth_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels", tags=["channels"])

config_manager = ConfigManager()

# Очередь отложенных удалений (импортируется из app.py)
pending_telegram_deletions: List = []


async def _manage_source_subscriptions(
    sources_to_join: List[str],
    sources_to_leave: List[str]
) -> dict:
    """
    Subscribe/unsubscribe bot from source channels.

    Args:
        sources_to_join: List of channel usernames/IDs to join
        sources_to_leave: List of channel usernames/IDs to leave

    Returns:
        dict with joined, left, and errors lists
    """
    from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
    from telethon.errors import (
        ChannelPrivateError, InviteHashExpiredError,
        UserAlreadyParticipantError, UserNotParticipantError
    )

    joined = []
    left = []
    errors = []

    if not sources_to_join and not sources_to_leave:
        return {"joined": joined, "left": left, "errors": errors}

    session_status = await bot_auth_manager.check_session_status(quick_check=True)
    if not session_status.get("authenticated"):
        return {"joined": [], "left": [], "errors": ["Bot not authenticated"]}

    client = await create_new_bot_client()

    if not await client.is_user_authorized():
        await client.disconnect()
        return {"joined": [], "left": [], "errors": ["Bot session invalid"]}

    try:
        # Join new sources
        for source in sources_to_join:
            try:
                # Get entity first to check if accessible
                if source.lstrip('-').isdigit():
                    entity = await client.get_entity(int(source))
                else:
                    entity = await client.get_entity(source)

                try:
                    await client(JoinChannelRequest(entity))
                    joined.append(source)
                    logger.info(f"Bot joined source channel: {source}")
                except UserAlreadyParticipantError:
                    joined.append(f"{source} (already member)")
                except Exception as join_err:
                    if "USER_ALREADY_PARTICIPANT" in str(join_err):
                        joined.append(f"{source} (already member)")
                    else:
                        errors.append(f"{source}: {str(join_err)}")
                        logger.warning(f"Failed to join {source}: {join_err}")

            except ChannelPrivateError:
                errors.append(f"{source}: private channel, need invite link")
            except Exception as e:
                errors.append(f"{source}: {str(e)}")
                logger.warning(f"Failed to access {source}: {e}")

        # Leave removed sources (only if not used by other channels)
        for source in sources_to_leave:
            try:
                if source.lstrip('-').isdigit():
                    entity = await client.get_entity(int(source))
                else:
                    entity = await client.get_entity(source)

                try:
                    await client(LeaveChannelRequest(entity))
                    left.append(source)
                    logger.info(f"Bot left source channel: {source}")
                except UserNotParticipantError:
                    left.append(f"{source} (wasn't member)")
                except Exception as leave_err:
                    if "USER_NOT_PARTICIPANT" in str(leave_err):
                        left.append(f"{source} (wasn't member)")
                    else:
                        # Don't treat leave errors as critical
                        logger.warning(f"Failed to leave {source}: {leave_err}")

            except Exception as e:
                # Don't treat leave errors as critical
                logger.warning(f"Failed to leave {source}: {e}")

    finally:
        await client.disconnect()

    return {"joined": joined, "left": left, "errors": errors}


def _get_sources_used_by_other_channels(exclude_channel_id: str) -> Set[str]:
    """Get all input sources used by channels other than the specified one."""
    all_sources = set()
    for channel in config_manager.channels:
        if channel.id != exclude_channel_id:
            all_sources.update(channel.input_sources)
    return all_sources


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


@router.get("/prompts/defaults")
async def get_default_prompts():
    """Получить дефолтные промпты из файлов"""
    defaults = PromptsConfig.load_defaults()
    return {
        "success": True,
        "prompts": defaults.to_dict()
    }


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
    config_manager.load()  # Reload config before getting channel
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

        # Промпты: если переданы - используем их, иначе дефолтные
        if data.prompts:
            prompts = PromptsConfig(
                base_context=data.prompts.base_context,
                discovery=data.prompts.discovery,
                engagement=data.prompts.engagement,
                call_ready=data.prompts.call_ready,
                call_pending=data.prompts.call_pending,
                call_declined=data.prompts.call_declined,
            )
        else:
            prompts = PromptsConfig.load_defaults()

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
            auto_response_template=data.auto_response_template,
            instant_response=data.instant_response,
            prompts=prompts
        )

        if config_manager.add_channel(channel):
            agents_added = []
            agents_errors = []
            sources_joined = []
            sources_errors = []

            # Auto-subscribe bot to input sources
            if channel.input_sources:
                try:
                    sub_result = await _manage_source_subscriptions(
                        sources_to_join=channel.input_sources,
                        sources_to_leave=[]
                    )
                    sources_joined = sub_result.get('joined', [])
                    sources_errors = sub_result.get('errors', [])
                except Exception as e:
                    logger.warning(f"Failed to subscribe to sources: {e}")
                    sources_errors.append(str(e))

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
            if sources_joined:
                response["sources_joined"] = sources_joined
            if sources_errors:
                response["sources_errors"] = sources_errors
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
        # Debug: log what we received
        logger.info(f"Updating channel {channel_id}: enabled={data.enabled}")

        config_manager.load()  # Reload config before getting channel
        channel = config_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Канал не найден")

        # Track old sources for subscription management
        old_sources = set(channel.input_sources) if channel.input_sources else set()

        # Debug: log current state
        logger.info(f"  Before update: channel.enabled={channel.enabled}")

        # Обновляем поля
        if data.name is not None:
            channel.name = data.name
        if data.telegram_id is not None:
            channel.telegram_id = data.telegram_id
        if data.input_sources is not None:
            channel.input_sources = data.input_sources
        if data.enabled is not None:
            channel.enabled = data.enabled
            logger.info(f"  Updated enabled to: {data.enabled}")
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
        if data.instant_response is not None:
            channel.instant_response = data.instant_response
        if data.ai_conversation_enabled is not None:
            channel.ai_conversation_enabled = data.ai_conversation_enabled
            logger.info(f"  Updated ai_conversation_enabled to: {data.ai_conversation_enabled}")

        # Обновляем промпты если переданы
        if data.prompts is not None:
            channel.prompts = PromptsConfig(
                base_context=data.prompts.base_context,
                discovery=data.prompts.discovery,
                engagement=data.prompts.engagement,
                call_ready=data.prompts.call_ready,
                call_pending=data.prompts.call_pending,
                call_declined=data.prompts.call_declined,
            )

        # Debug: log final state before save
        logger.info(f"  After update: channel.enabled={channel.enabled}")

        if config_manager.update_channel(channel_id, channel):
            agents_added = []
            agents_errors = []
            sources_joined = []
            sources_left = []
            sources_errors = []

            # Handle source subscription changes
            if data.input_sources is not None:
                new_sources = set(data.input_sources)
                sources_to_join = list(new_sources - old_sources)
                sources_to_leave_candidates = list(old_sources - new_sources)

                # Only leave sources not used by other channels
                sources_used_elsewhere = _get_sources_used_by_other_channels(channel_id)
                sources_to_leave = [s for s in sources_to_leave_candidates if s not in sources_used_elsewhere]

                print(f"[SUBSCRIBE] Source changes: +{len(sources_to_join)} -{len(sources_to_leave)} (old={len(old_sources)}, new={len(new_sources)})", flush=True)

                if sources_to_join or sources_to_leave:
                    print(f"[SUBSCRIBE]   To join: {sources_to_join[:5]}{'...' if len(sources_to_join) > 5 else ''}", flush=True)
                    print(f"[SUBSCRIBE]   To leave: {sources_to_leave[:5]}{'...' if len(sources_to_leave) > 5 else ''}", flush=True)
                    try:
                        sub_result = await _manage_source_subscriptions(
                            sources_to_join=sources_to_join,
                            sources_to_leave=sources_to_leave
                        )
                        print(f"[SUBSCRIBE]   Result: joined={sub_result.get('joined')}, left={sub_result.get('left')}, errors={sub_result.get('errors')}", flush=True)
                        sources_joined = sub_result.get('joined', [])
                        sources_left = sub_result.get('left', [])
                        sources_errors = sub_result.get('errors', [])
                    except Exception as e:
                        logger.warning(f"Failed to manage source subscriptions: {e}")
                        sources_errors.append(str(e))

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
            if sources_joined:
                response["sources_joined"] = sources_joined
            if sources_left:
                response["sources_left"] = sources_left
            if sources_errors:
                response["sources_errors"] = sources_errors
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
        # Перезагружаем конфигурацию чтобы получить актуальные данные
        config_manager.load()
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
                from src.config import config
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

        # Unsubscribe from sources not used by other channels
        if channel.input_sources:
            sources_used_elsewhere = _get_sources_used_by_other_channels(channel_id)
            sources_to_leave = [s for s in channel.input_sources if s not in sources_used_elsewhere]
            if sources_to_leave:
                try:
                    sub_result = await _manage_source_subscriptions(
                        sources_to_join=[],
                        sources_to_leave=sources_to_leave
                    )
                    left = sub_result.get('left', [])
                    if left:
                        deleted_entities.append(f"unsubscribed from {len(left)} sources")
                except Exception as e:
                    logger.warning(f"Failed to unsubscribe from sources: {e}")

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
    config_manager.load()  # Reload config before getting channel
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
