"""API для полного создания канала с автоматизацией"""
import uuid
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter
from pydantic import BaseModel

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from auth import bot_auth_manager
from src.config_manager import ConfigManager, ChannelConfig, FilterConfig, AgentConfig, PromptsConfig
from web.utils import get_or_create_bot_client
from src.session_config import get_agent_session_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels", tags=["channels-create"])
config_manager = ConfigManager()


class PromptsRequest(BaseModel):
    """Промпты для AI"""
    base_context: str = ""
    discovery: str = ""
    engagement: str = ""
    call_ready: str = ""
    call_pending: str = ""
    call_declined: str = ""


class FullChannelCreateRequest(BaseModel):
    """Полное создание канала с автоматизацией"""
    name: str
    input_sources: List[str]
    agents: List[str]  # List of session names
    auto_response_template: str = ""
    include_keywords: List[str] = []
    exclude_keywords: List[str] = []
    prompts: PromptsRequest = None


@router.post("/create-full")
async def create_channel_full(data: FullChannelCreateRequest):
    """
    Создать канал с полной автоматизацией:
    1. Создаёт Telegram канал для уведомлений
    2. Создаёт CRM группу с топиками
    3. Добавляет агентов в CRM
    4. Сохраняет конфигурацию
    """
    logger.info(f"Создание полного канала: name={data.name}")

    try:
        from telethon.tl.functions.channels import CreateChannelRequest as TgCreateChannel
        from telethon.tl.functions.channels import ToggleForumRequest
        from telethon.tl.functions.messages import ExportChatInviteRequest

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

            # 3. Добавляем агентов в CRM (ОБЯЗАТЕЛЬНО - иначе откат)
            logger.info(f"Добавление {len(data.agents)} агентов в CRM группу...")

            if not data.agents:
                # Нет агентов - удаляем созданное и возвращаем ошибку
                logger.error("Не указаны агенты для CRM")
                from telethon.tl.functions.channels import DeleteChannelRequest
                try:
                    await client(DeleteChannelRequest(crm_group))
                    await client(DeleteChannelRequest(notification_channel))
                except Exception:
                    pass
                return {
                    "success": False,
                    "message": "Необходимо выбрать хотя бы одного агента для CRM"
                }

            # Создаём invite link для агентов
            from telethon.tl.functions.messages import ExportChatInviteRequest as ExportInvite
            try:
                agent_invite = await client(ExportInvite(
                    peer=crm_group,
                    expire_date=None,
                    usage_limit=len(data.agents) + 5,
                    title="Agent invite"
                ))
                agent_invite_link = agent_invite.link
                logger.info(f"  Создана invite ссылка для агентов")
            except Exception as e:
                logger.error(f"  Не удалось создать invite link: {e}")
                from telethon.tl.functions.channels import DeleteChannelRequest
                try:
                    await client(DeleteChannelRequest(crm_group))
                    await client(DeleteChannelRequest(notification_channel))
                except Exception:
                    pass
                return {
                    "success": False,
                    "message": f"Не удалось создать приглашение в CRM группу: {e}"
                }

            agents_invited = []
            agents_errors = []

            for agent_session in data.agents:
                logger.info(f"  Попытка добавить агента: {agent_session}")
                agent_client = None
                should_disconnect = False
                try:
                    # ВАЖНО: Создаём ОТДЕЛЬНЫЙ временный клиент для веб-интерфейса
                    # Нельзя использовать агентов из agent_pool - они подключены в другом event loop (бота)
                    # См. CLAUDE.md: "Each thread must have its own TelegramClient instance"
                    from web.utils import get_agent_client
                    agent_client, should_disconnect = await get_agent_client(agent_session)

                    if await agent_client.is_user_authorized():
                        agent_me = await agent_client.get_me()
                        logger.info(f"  Агент авторизован: {agent_me.first_name}")

                        # Агент сам вступает в группу через invite link
                        try:
                            from telethon.tl.functions.messages import ImportChatInviteRequest
                            invite_hash = agent_invite_link.split("/")[-1]
                            if invite_hash.startswith("+"):
                                invite_hash = invite_hash[1:]

                            logger.info(f"  Joining with invite hash: {invite_hash}")
                            await agent_client(ImportChatInviteRequest(invite_hash))
                            agent_name = agent_me.username or agent_me.first_name
                            agents_invited.append(f"@{agent_name}")
                            logger.info(f"  ✅ Агент {agent_session} вступил в CRM группу")
                        except Exception as join_err:
                            err_str = str(join_err)
                            # Агент уже в группе - это успех!
                            # Бывает когда бот и агент - один и тот же аккаунт (бот создал группу = агент уже в ней)
                            if "USER_ALREADY_PARTICIPANT" in err_str or "already a participant" in err_str.lower():
                                agents_invited.append(f"@{agent_me.username or agent_me.first_name} (создатель группы)")
                                logger.info(f"  ✅ Агент уже в группе (бот и агент - один аккаунт)")
                            else:
                                agents_errors.append(f"{agent_session}: {err_str}")
                                logger.error(f"  ❌ Ошибка вступления агента: {join_err}")
                    else:
                        logger.warning(f"  Агент не авторизован")
                        agents_errors.append(f"{agent_session}: не авторизован")

                except Exception as e:
                    agents_errors.append(f"{agent_session}: {str(e)}")
                    logger.error(f"  ❌ Не удалось добавить агента {agent_session}: {e}")
                finally:
                    # Всегда отключаем временный клиент
                    if agent_client and should_disconnect:
                        try:
                            await agent_client.disconnect()
                        except Exception:
                            pass

            # Проверяем что хотя бы один агент добавлен
            if not agents_invited:
                logger.error("Ни один агент не был добавлен в CRM группу - откат")
                from telethon.tl.functions.channels import DeleteChannelRequest
                try:
                    await client(DeleteChannelRequest(crm_group))
                    await client(DeleteChannelRequest(notification_channel))
                    logger.info("Созданные каналы удалены")
                except Exception as del_err:
                    logger.warning(f"Ошибка удаления каналов: {del_err}")

                error_details = "; ".join(agents_errors) if agents_errors else "Неизвестная ошибка"
                return {
                    "success": False,
                    "message": f"Не удалось добавить агентов в CRM группу. Выберите другого агента. Ошибки: {error_details}"
                }

            # 4. Отправляем инвайт владельцу
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
                telegram_id=notification_channel_id,
                enabled=True,
                input_sources=data.input_sources,
                filters=filters,
                crm_enabled=True,
                crm_group_id=crm_group_id,
                agents=agents_config,
                auto_response_enabled=True,
                auto_response_template=data.auto_response_template or "Здравствуйте! Меня заинтересовала ваша вакансия. Буду рад обсудить детали!",
                prompts=prompts
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
            if should_disconnect:
                await client.disconnect()

    except Exception as e:
        logger.error(f"Ошибка создания полного канала: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}
