"""
CRM Handler - логика автоответов, топиков и трансляции сообщений
Вынесено из bot_multi.py для улучшения читаемости
"""
import asyncio
import logging
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel

from src.agent_account import AgentAccount
from src.agent_pool import AgentPool
from src.conversation_manager import ConversationManager
from ai_conversation import AIConversationHandler, AIHandlerPool, AIConfig as AIHandlerConfig
from src.config_manager import ChannelConfig

if TYPE_CHECKING:
    from bot_multi import MultiChannelJobMonitorBot

logger = logging.getLogger(__name__)


class CRMHandler:
    """Обработчик CRM функциональности: автоответы, топики, AI"""

    def __init__(self, bot: "MultiChannelJobMonitorBot"):
        self.bot = bot

        # CRM данные
        self.agent_pools: Dict[str, AgentPool] = {}
        self.conversation_managers: Dict[str, ConversationManager] = {}
        self.contact_to_channel: Dict[int, str] = {}
        self.topic_to_agent: Dict[int, AgentAccount] = {}

        # AI
        self.ai_handler_pool: Optional[AIHandlerPool] = None
        self.ai_handlers: Dict[str, AIConversationHandler] = {}

        # Трекинг зарегистрированных обработчиков
        self._registered_agent_handlers: Set[int] = set()

    async def setup_agents(self, output_channels: List[ChannelConfig], config_manager):
        """Инициализация CRM агентов и conversation managers для каналов"""
        logger.info("Инициализация CRM агентов...")

        # Очищаем старые данные при перезагрузке
        # НО НЕ очищаем _registered_agent_handlers
        self.agent_pools.clear()
        self.conversation_managers.clear()
        self.contact_to_channel.clear()
        self.ai_handlers.clear()

        # Инициализация AI handler pool
        self.ai_handler_pool = AIHandlerPool(config_manager.llm_providers)

        crm_enabled_channels = [ch for ch in output_channels if ch.crm_enabled]

        if not crm_enabled_channels:
            logger.info("Нет каналов с включенным CRM")
            return

        for channel in crm_enabled_channels:
            await self._setup_channel_crm(channel)

        logger.info(f"CRM инициализирован для {len(self.agent_pools)} каналов")

    async def _setup_channel_crm(self, channel: ChannelConfig):
        """Настройка CRM для одного канала"""
        try:
            logger.info(f"Настройка CRM для канала '{channel.name}'...")

            # Валидация конфигурации
            if not channel.agents:
                logger.warning(f"  Канал '{channel.name}': нет агентов, CRM пропущен")
                return

            if not channel.crm_group_id:
                logger.warning(f"  Канал '{channel.name}': не указан crm_group_id, CRM пропущен")
                return

            # Создаем пул агентов
            agent_pool = AgentPool(channel.agents)

            # Инициализируем пул
            if not await agent_pool.initialize():
                logger.error(f"  Не удалось инициализировать пул агентов для '{channel.name}'")
                return

            self.agent_pools[channel.id] = agent_pool

            # Получаем первого доступного агента
            primary_agent = agent_pool.get_available_agent()
            if not primary_agent:
                logger.error(f"  Нет доступных агентов для '{channel.name}'")
                return

            # ВАЖНО: Агент должен "узнать" о CRM группе перед использованием
            # Группа могла быть создана веб-интерфейсом через другой клиент
            try:
                await primary_agent.client.get_entity(channel.crm_group_id)
                logger.debug(f"  Агент получил доступ к CRM группе {channel.crm_group_id}")
            except Exception as e:
                logger.warning(f"  Агент не может получить доступ к CRM группе: {e}")
                # Продолжаем - возможно группа станет доступна позже

            # Создаем conversation manager
            conv_manager = ConversationManager(
                client=primary_agent.client,
                group_id=channel.crm_group_id,
                send_contact_message_cb=self._send_message_from_topic_to_contact,
                group_monitor_client=self.bot.client
            )

            # Загружаем кэш из БД
            await conv_manager.load_cache_from_db()

            # Восстанавливаем contact_to_channel маппинг
            for contact_id in conv_manager._topic_cache.keys():
                self.contact_to_channel[contact_id] = channel.id
            logger.info(f"  Восстановлено {len(conv_manager._topic_cache)} контактов")

            # Регистрируем обработчики
            conv_manager.register_handlers()

            # Регистрируем обработчик входящих сообщений для агентов
            for agent in agent_pool.agents:
                agent_id = id(agent.client)
                if agent_id not in self._registered_agent_handlers:
                    self._register_contact_message_handler(agent.client, channel.id)
                    self._registered_agent_handlers.add(agent_id)

            self.conversation_managers[channel.id] = conv_manager

            # Инициализация AI handler
            if channel.ai_conversation_enabled:
                await self._setup_ai_handler(channel)

        except Exception as e:
            logger.error(f"  Ошибка настройки CRM для '{channel.name}': {e}", exc_info=True)

    async def _setup_ai_handler(self, channel: ChannelConfig):
        """Инициализация AI handler для канала"""
        try:
            ai_config = AIHandlerConfig.from_dict(channel.ai_config.to_dict())
            ai_handler = await self.ai_handler_pool.get_or_create(
                channel_id=channel.id,
                ai_config=ai_config,
            )
            self.ai_handlers[channel.id] = ai_handler
            logger.info(f"  AI handler инициализирован (mode: {ai_config.mode})")
        except Exception as ai_error:
            logger.warning(f"  Не удалось инициализировать AI: {ai_error}")

    def _register_contact_message_handler(self, agent_client: TelegramClient, channel_id: str):
        """Регистрация обработчика входящих сообщений от контактов"""

        @agent_client.on(events.NewMessage(incoming=True))
        async def handle_contact_message(event):
            """Трансляция сообщения от контакта в топик"""
            try:
                message = event.message

                # Игнорируем сообщения из групп
                chat = await event.get_chat()
                if isinstance(chat, (Chat, Channel)):
                    return

                # Игнорируем собственные сообщения
                if message.out:
                    return

                sender = await message.get_sender()
                if not sender:
                    return

                # Проверяем что сообщение не от самого агента
                try:
                    me = await agent_client.get_me()
                    if sender.id == me.id:
                        return
                except Exception:
                    pass

                # Игнорируем служебные сообщения
                message_text = message.text or ""
                from src.constants import SERVICE_MESSAGE_PREFIXES
                if any(message_text.startswith(p) for p in SERVICE_MESSAGE_PREFIXES):
                    if message_text.startswith("👤 **") and "\n\n" not in message_text:
                        pass  # Не служебное
                    else:
                        return

                # Ищем канал и conv_manager для этого контакта
                channel_id_found = None
                conv_manager = None

                for ch_id, cm in self.conversation_managers.items():
                    if cm.get_topic_id(sender.id):
                        channel_id_found = ch_id
                        conv_manager = cm
                        self.contact_to_channel[sender.id] = ch_id
                        break

                if not channel_id_found or not conv_manager:
                    return

                # Проверяем, не было ли это сообщение отправлено агентом
                if conv_manager.is_agent_sent_message(message.id):
                    return

                topic_id = conv_manager.get_topic_id(sender.id)
                ai_handler = self.ai_handlers.get(channel_id_found)

                if topic_id:
                    await self._relay_contact_message_to_topic(
                        agent_client, conv_manager, sender, message,
                        topic_id, ai_handler, channel_id_found
                    )

            except Exception as e:
                logger.error(f"Ошибка в handle_contact_message: {e}", exc_info=True)

    async def _relay_contact_message_to_topic(
        self,
        agent_client: TelegramClient,
        conv_manager: ConversationManager,
        sender: User,
        message,
        topic_id: int,
        ai_handler: Optional[AIConversationHandler],
        channel_id: str
    ):
        """Пересылка сообщения от контакта в топик CRM"""
        sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
        if not sender_name and sender.username:
            sender_name = f"@{sender.username}"
        if not sender_name:
            sender_name = f"User {sender.id}"

        relay_text = f"👤 **{sender_name}:**\n\n{message.text or ''}"

        # Отправляем в CRM
        try:
            sent_msg = await agent_client.send_message(
                entity=conv_manager.group_id,
                message=relay_text,
                file=message.media if message.media else None,
                reply_to=topic_id
            )
            if sent_msg and hasattr(sent_msg, 'id'):
                conv_manager.save_message_to_topic(sent_msg.id, topic_id)
        except Exception as e:
            logger.warning(f"Не удалось отправить в CRM топик: {e}")

        # AI ответ
        if ai_handler and message.text:
            await self._handle_ai_response(
                agent_client, conv_manager, sender.id, sender_name,
                message.text, topic_id, ai_handler
            )

    async def _handle_ai_response(
        self,
        agent_client: TelegramClient,
        conv_manager: ConversationManager,
        contact_id: int,
        contact_name: str,
        message_text: str,
        topic_id: int,
        ai_handler: AIConversationHandler
    ):
        """Обработка AI ответа на сообщение контакта"""

        async def send_to_contact(cid: int, text: str) -> bool:
            try:
                sent = await agent_client.send_message(cid, text)
                if sent:
                    conv_manager.mark_agent_sent_message(sent.id)
                    # Зеркалируем в топик
                    try:
                        ai_msg = f"🤖 **AI:**\n\n{text}"
                        topic_sent = await agent_client.send_message(
                            entity=conv_manager.group_id,
                            message=ai_msg,
                            reply_to=topic_id
                        )
                        if topic_sent:
                            conv_manager.save_message_to_topic(topic_sent.id, topic_id)
                    except Exception as e:
                        logger.warning(f"Не удалось зеркалировать AI в CRM: {e}")
                return True
            except Exception as e:
                logger.error(f"Ошибка отправки AI ответа: {e}")
                return False

        async def suggest_in_topic(cid: int, text: str, name: str):
            suggest_msg = f"💡 **AI предлагает ответ:**\n\n{text}\n\n_Отправьте этот текст или напишите свой ответ_"
            await agent_client.send_message(
                entity=conv_manager.group_id,
                message=suggest_msg,
                reply_to=topic_id
            )

        asyncio.create_task(
            ai_handler.handle_message(
                contact_id=contact_id,
                message=message_text,
                contact_name=contact_name,
                send_callback=send_to_contact,
                suggest_callback=suggest_in_topic,
            )
        )

    async def _send_message_from_topic_to_contact(
        self,
        contact_id: int,
        text: str,
        media,
        topic_id: int
    ):
        """Отправка сообщения из темы CRM-группы контакту"""
        try:
            # Ищем канал для контакта
            channel_id = self.contact_to_channel.get(contact_id)
            if not channel_id:
                for ch_id, conv_manager in self.conversation_managers.items():
                    if conv_manager.get_contact_id(topic_id) == contact_id:
                        channel_id = ch_id
                        self.contact_to_channel[contact_id] = channel_id
                        break

                if not channel_id:
                    logger.warning(f"Канал для контакта {contact_id} не найден")
                    return

            # Ищем агента для этой темы
            agent = self.topic_to_agent.get(topic_id)
            if not agent:
                agent_pool = self.agent_pools.get(channel_id)
                if not agent_pool:
                    logger.error(f"Нет пула агентов для канала {channel_id}")
                    return

                agent = agent_pool.get_available_agent()
                if not agent:
                    logger.error(f"Нет доступных агентов для контакта {contact_id}")
                    return

            if not agent.client:
                logger.error(f"У агента {agent.session_name} нет клиента")
                return

            # Записываем в AI контекст
            ai_handler = self.ai_handlers.get(channel_id)
            if ai_handler and text:
                ai_handler.add_operator_message(contact_id, text)

            # Отправляем сообщение
            try:
                from telethon.tl.types import MessageMediaWebPage
                media_file = None
                if media and not isinstance(media, MessageMediaWebPage):
                    media_file = media

                if media_file:
                    sent_message = await agent.client.send_message(
                        contact_id, text or "", file=media_file
                    )
                else:
                    sent_message = await agent.client.send_message(
                        contact_id, text or ""
                    )

                if sent_message and hasattr(sent_message, 'id'):
                    conv_manager = self.conversation_managers.get(channel_id)
                    if conv_manager:
                        conv_manager.mark_agent_sent_message(sent_message.id)

            except Exception as send_error:
                logger.error(f"Ошибка отправки через агента: {send_error}", exc_info=True)
                raise

        except Exception as e:
            logger.error(f"Ошибка в _send_message_from_topic_to_contact: {e}", exc_info=True)

    async def handle_crm_workflow(
        self,
        message,
        chat,
        chat_title: str,
        matching_outputs: List[ChannelConfig],
        contacts: Dict[str, Optional[str]],
        message_processor
    ):
        """Обработка CRM workflow: автоответ + создание топика"""
        try:
            contacted_users: Set[str] = set()

            for channel in matching_outputs:
                if not channel.crm_enabled:
                    continue

                agent_pool = self.agent_pools.get(channel.id)
                conv_manager = self.conversation_managers.get(channel.id)

                if not agent_pool or not conv_manager:
                    continue

                logger.info(f"CRM workflow для канала '{channel.name}'...")

                available_agent = agent_pool.get_available_agent()
                if not available_agent:
                    logger.warning(f"  Нет доступных агентов для '{channel.name}'")
                    continue

                auto_response_sent = await self._send_auto_response(
                    channel, available_agent, contacts, contacted_users
                )

                await self._create_crm_topic(
                    channel, available_agent, conv_manager,
                    contacts, message, chat, chat_title,
                    auto_response_sent, message_processor
                )

        except Exception as e:
            logger.error(f"Ошибка в CRM workflow: {e}", exc_info=True)

    async def _send_auto_response(
        self,
        channel: ChannelConfig,
        agent: AgentAccount,
        contacts: Dict[str, Optional[str]],
        contacted_users: Set[str]
    ) -> bool:
        """Отправка автоответа контакту"""
        if not channel.auto_response_enabled or not channel.auto_response_template:
            return False

        telegram_contact = contacts.get('telegram')
        if not telegram_contact:
            return False

        if telegram_contact.lower() in contacted_users:
            return False

        try:
            success = await agent.send_message(
                telegram_contact,
                channel.auto_response_template
            )
            if success:
                contacted_users.add(telegram_contact.lower())
                return True
        except Exception as e:
            logger.error(f"Ошибка отправки автоответа: {e}")

        return False

    async def _create_crm_topic(
        self,
        channel: ChannelConfig,
        agent: AgentAccount,
        conv_manager: ConversationManager,
        contacts: Dict[str, Optional[str]],
        message,
        chat,
        chat_title: str,
        auto_response_sent: bool,
        message_processor
    ):
        """Создание топика в CRM группе"""
        if not contacts.get('telegram'):
            return

        try:
            # Проверяем, что агент используется из правильного потока
            if not agent.is_valid_loop():
                logger.error(f"Агент вызван из неправильного event loop")
                return

            # Резолвим контакт
            contact_user = await self.bot.client.get_entity(contacts['telegram'])

            if not isinstance(contact_user, User):
                return

            # Резолвим через агента тоже
            try:
                await agent.client.get_entity(contacts['telegram'])
            except Exception:
                pass

            # Проверяем/создаем топик
            topic_id = conv_manager.get_topic_id(contact_user.id)

            if not topic_id:
                sender_name = f"{contact_user.first_name}"
                if contact_user.username:
                    sender_name += f" (@{contact_user.username})"

                topic_title = f"{sender_name} | {chat_title[:80]}"
                topic_id = await conv_manager.create_topic(
                    title=topic_title[:128],
                    contact_id=contact_user.id
                )

                if topic_id:
                    self.contact_to_channel[contact_user.id] = channel.id
                else:
                    logger.error("Не удалось создать топик")
                    return

            # Привязываем агента к теме
            if topic_id:
                self.topic_to_agent[topic_id] = agent

            # Инициализируем AI контекст
            if auto_response_sent and topic_id:
                await self._init_ai_context(
                    channel, contact_user.id, message, chat_title
                )

            # Зеркалируем автоответ
            if auto_response_sent and topic_id:
                await self._mirror_auto_response(
                    agent, conv_manager, channel, topic_id
                )

            # Отправляем инфо в топик
            if topic_id and contact_user:
                await self._send_topic_info(
                    conv_manager, contact_user, chat_title,
                    message, chat, topic_id, message_processor
                )

        except ValueError as e:
            logger.warning(f"Не удалось найти пользователя {contacts['telegram']}: {e}")
        except Exception as e:
            logger.error(f"Ошибка создания топика: {e}", exc_info=True)

    async def _init_ai_context(
        self,
        channel: ChannelConfig,
        contact_id: int,
        message,
        chat_title: str
    ):
        """Инициализация AI контекста для контакта"""
        ai_handler = self.ai_handlers.get(channel.id)
        if not ai_handler:
            return

        try:
            job_info = f"Вакансия из канала: {chat_title}\n\n{message.text[:500]}..."
            await ai_handler.initialize_context(
                contact_id=contact_id,
                initial_message=channel.auto_response_template,
                job_info=job_info,
            )
        except Exception as e:
            logger.warning(f"Ошибка инициализации AI контекста: {e}")

    async def _mirror_auto_response(
        self,
        agent: AgentAccount,
        conv_manager: ConversationManager,
        channel: ChannelConfig,
        topic_id: int
    ):
        """Зеркалирование автоответа в топик"""
        try:
            agent_message = f"🤖 **Агент ({agent.session_name}):**\n\n{channel.auto_response_template}"
            sent_msg = await agent.client.send_message(
                entity=channel.crm_group_id,
                message=agent_message,
                reply_to=topic_id
            )
            if sent_msg and hasattr(sent_msg, 'id'):
                conv_manager.save_message_to_topic(sent_msg.id, topic_id)
        except Exception as e:
            logger.error(f"Ошибка зеркалирования автоответа: {e}")

    async def _send_topic_info(
        self,
        conv_manager: ConversationManager,
        contact_user: User,
        chat_title: str,
        message,
        chat,
        topic_id: int,
        message_processor
    ):
        """Отправка информационного сообщения в топик"""
        sender_info = f"{contact_user.first_name}"
        if contact_user.username:
            sender_info += f" (@{contact_user.username})"

        info_message = f"📌 **Новый контакт: {sender_info}**\n\n"
        info_message += f"📍 **Канал вакансии:** {chat_title}\n"
        info_message += f"🔗 **Ссылка:** {message_processor.get_message_link(message, chat)}"

        await conv_manager.send_to_topic(topic_id, info_message)

    async def cleanup(self):
        """Очистка ресурсов CRM"""
        # Закрываем AI handlers
        if self.ai_handler_pool:
            self.ai_handler_pool.close_all()
        self.ai_handlers.clear()

        # Очищаем пулы агентов
        for channel_id, agent_pool in self.agent_pools.items():
            try:
                await agent_pool.disconnect_all()
            except Exception as e:
                logger.error(f"Ошибка очистки пула агентов для {channel_id}: {e}")

        self.agent_pools.clear()
