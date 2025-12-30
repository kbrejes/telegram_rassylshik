"""API для создания Telegram сущностей (каналы, группы)"""
import uuid
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter
from pydantic import BaseModel

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from auth import bot_auth_manager
from src.config_manager import ChannelConfig, FilterConfig, AgentConfig, PromptsConfig
from web.utils import get_or_create_bot_client
from web import config_manager
from src.session_config import get_agent_session_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class CreateChannelRequest(BaseModel):
    """Запрос на создание канала"""
    title: str
    about: str = ""


class CreateCrmGroupRequest(BaseModel):
    """Запрос на создание CRM группы с топиками"""
    title: str
    about: str = ""
    owner_username: str = ""
    channel_id: str = ""


class AddAgentsToCrmRequest(BaseModel):
    """Запрос на добавление агентов в CRM группу"""
    crm_group_id: int
    channel_id: str


@router.post("/create-channel")
async def create_telegram_channel(request: CreateChannelRequest):
    """
    Создаёт приватный канал в Telegram для уведомлений.
    Требует авторизованную сессию бота.
    """
    try:
        from telethon.tl.functions.channels import CreateChannelRequest as TgCreateChannel

        # Проверяем что бот авторизован
        session_status = await bot_auth_manager.check_session_status(quick_check=True)
        if not session_status.get("authenticated"):
            return {
                "success": False,
                "message": "Бот не авторизован. Сначала пройдите авторизацию на странице /auth"
            }

        client, should_disconnect = await get_or_create_bot_client()

        if not await client.is_user_authorized():
            if should_disconnect:
                await client.disconnect()
            return {
                "success": False,
                "message": "Сессия бота недействительна. Пройдите авторизацию заново."
            }

        try:

            result = await client(TgCreateChannel(
                title=request.title,
                about=request.about,
                broadcast=True,
                megagroup=False
            ))

            channel = result.chats[0]
            channel_id = -1000000000000 - channel.id

            logger.info(f"Канал '{request.title}' создан с ID {channel_id}")

            return {
                "success": True,
                "message": f"Канал '{request.title}' успешно создан!",
                "channel_id": channel_id,
                "channel_title": request.title
            }

        finally:
            if should_disconnect:
                await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка создания канала: {e}")
        return {
            "success": False,
            "message": f"Ошибка создания канала: {str(e)}"
        }


@router.post("/create-crm-group")
async def create_telegram_crm_group(request: CreateCrmGroupRequest):
    """
    Создаёт группу с включенными топиками для CRM.
    Требует авторизованную сессию бота.
    """
    logger.info(f"Создание CRM группы: title={request.title}, owner={request.owner_username}")
    try:
        from telethon.tl.functions.channels import CreateChannelRequest as TgCreateChannel
        from telethon.tl.functions.channels import ToggleForumRequest, InviteToChannelRequest
        from telethon.tl.functions.messages import ExportChatInviteRequest
        from telethon import TelegramClient
        from auth.base import TimeoutSQLiteSession
        from src.config import config

        session_status = await bot_auth_manager.check_session_status(quick_check=True)
        if not session_status.get("authenticated"):
            return {
                "success": False,
                "message": "Бот не авторизован. Сначала пройдите авторизацию на странице /auth"
            }

        client, should_disconnect = await get_or_create_bot_client()

        if not await client.is_user_authorized():
            if should_disconnect:
                await client.disconnect()
            return {
                "success": False,
                "message": "Сессия бота недействительна. Пройдите авторизацию заново."
            }

        try:
            # Создаём супергруппу
            result = await client(TgCreateChannel(
                title=request.title,
                about=request.about,
                broadcast=False,
                megagroup=True
            ))

            group = result.chats[0]

            # Включаем топики
            topics_enabled = False
            try:
                await client(ToggleForumRequest(
                    channel=group,
                    enabled=True,
                    tabs=[]
                ))
                topics_enabled = True
            except Exception as e:
                logger.warning(f"Не удалось включить топики: {e}")

            group_id = -1000000000000 - group.id
            invited_users = []
            invite_errors = []

            # Приглашаем владельца
            if request.owner_username:
                try:
                    owner_username = request.owner_username.lstrip('@')
                    owner_entity = await client.get_entity(owner_username)

                    invite = await client(ExportChatInviteRequest(
                        peer=group,
                        expire_date=None,
                        usage_limit=1,
                        title="CRM доступ"
                    ))

                    await client.send_message(
                        owner_entity,
                        f"🔗 **Приглашение в CRM группу**\n\n"
                        f"Группа: **{request.title}**\n"
                        f"Ссылка для вступления: {invite.link}\n\n"
                        f"_Ссылка одноразовая_"
                    )
                    invited_users.append(f"@{owner_username} (ссылка отправлена)")
                    logger.info(f"Инвайт-ссылка отправлена владельцу @{owner_username}")
                except Exception as e:
                    invite_errors.append(f"Владелец @{owner_username}: {str(e)}")
                    logger.warning(f"Не удалось отправить инвайт владельцу: {e}")

            # Приглашаем агентов из канала
            if request.channel_id:
                try:
                    channel = config_manager.get_channel(request.channel_id)
                    if channel and channel.agents:
                        for agent_config in channel.agents:
                            agent_session = agent_config.session_name if hasattr(agent_config, 'session_name') else agent_config.get('session_name')
                            if not agent_session:
                                continue
                            try:
                                agent_session_path = get_agent_session_path(agent_session)
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
                                    invited_users.append(f"@{agent_name} (агент)")
                                    logger.info(f"Агент {agent_session} добавлен в CRM группу")

                                await agent_client.disconnect()
                            except Exception as e:
                                invite_errors.append(f"Агент {agent_session}: {str(e)}")
                                logger.warning(f"Не удалось добавить агента {agent_session}: {e}")
                except Exception as e:
                    logger.warning(f"Ошибка при добавлении агентов: {e}")

            logger.info(f"CRM группа '{request.title}' создана с ID {group_id}")

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
                "invite_errors": invite_errors
            }

        finally:
            if should_disconnect:
                await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка создания CRM группы: {e}")
        return {
            "success": False,
            "message": f"Ошибка создания группы: {str(e)}"
        }


@router.post("/add-agents-to-crm")
async def add_agents_to_crm(request: AddAgentsToCrmRequest):
    """Добавляет агентов канала в существующую CRM группу"""
    logger.info(f"Добавление агентов в CRM группу: group_id={request.crm_group_id}")
    try:
        from telethon.tl.functions.channels import InviteToChannelRequest
        from telethon import TelegramClient
        from auth.base import TimeoutSQLiteSession
        from src.config import config

        session_status = await bot_auth_manager.check_session_status(quick_check=True)
        if not session_status.get("authenticated"):
            return {"success": False, "message": "Бот не авторизован"}

        channel = config_manager.get_channel(request.channel_id)
        if not channel or not channel.agents:
            return {"success": False, "message": "Канал не найден или нет агентов"}

        client, should_disconnect = await get_or_create_bot_client()

        if not await client.is_user_authorized():
            if should_disconnect:
                await client.disconnect()
            return {"success": False, "message": "Сессия бота недействительна"}

        try:
            group = await client.get_entity(request.crm_group_id)
            invited = []
            errors = []

            for agent_config in channel.agents:
                agent_session = agent_config.session_name if hasattr(agent_config, 'session_name') else str(agent_config)
                try:
                    agent_session_path = get_agent_session_path(agent_session)
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
                        except Exception as invite_err:
                            if "USER_ALREADY_PARTICIPANT" in str(invite_err):
                                invited.append(f"@{agent_me.username or agent_me.first_name} (уже в группе)")
                            else:
                                errors.append(f"{agent_session}: {str(invite_err)}")

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
            if should_disconnect:
                await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка добавления агентов: {e}")
        return {"success": False, "message": str(e)}
