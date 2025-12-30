"""
CRM Handler - логика автоответов, топиков и трансляции сообщений
Вынесено из bot_multi.py для улучшения читаемости
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel

from src.agent_account import AgentAccount
from src.agent_pool import AgentPool
from src.conversation_manager import ConversationManager, FrozenAccountError
from src.connection_status import status_manager
from src.database import db
from src.human_behavior import human_behavior
from src.message_queue import message_queue
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

        # Channel configs for accessing settings like instant_response
        self.channel_configs: Dict[str, ChannelConfig] = {}

        # NEW: Map agent client id -> list of channel IDs
        # This allows finding channel by agent, not by topic
        self.agent_to_channels: Dict[int, List[str]] = {}

        # Трекинг зарегистрированных обработчиков
        self._registered_agent_handlers: Set[int] = set()

    async def setup_agents(self, output_channels: List[ChannelConfig], config_manager):
        """Инициализация CRM агентов и conversation managers для каналов"""
        logger.info("Инициализация CRM агентов...")

        # ATOMIC RELOAD: Build new data structures first, then swap
        # This prevents race condition where messages arrive during reload
        # and find empty conversation_managers dict

        # Store old data for cleanup later
        old_agent_pools = self.agent_pools
        old_ai_handlers = self.ai_handlers

        # Create new containers (DON'T clear old ones yet)
        new_agent_pools: Dict[str, AgentPool] = {}
        new_conversation_managers: Dict[str, ConversationManager] = {}
        new_contact_to_channel: Dict[int, str] = {}
        new_ai_handlers: Dict[str, AIConversationHandler] = {}
        new_channel_configs: Dict[str, ChannelConfig] = {}
        new_agent_to_channels: Dict[int, List[str]] = {}

        # Инициализация AI handler pool (with database for self-correction)
        new_ai_handler_pool = AIHandlerPool(config_manager.llm_providers, database=db)

        crm_enabled_channels = [ch for ch in output_channels if ch.crm_enabled]

        if not crm_enabled_channels:
            logger.info("Нет каналов с включенным CRM")
            # Atomic swap to empty
            self.agent_pools = new_agent_pools
            self.conversation_managers = new_conversation_managers
            self.contact_to_channel = new_contact_to_channel
            self.ai_handlers = new_ai_handlers
            self.channel_configs = new_channel_configs
            self.ai_handler_pool = new_ai_handler_pool
            self.agent_to_channels = new_agent_to_channels
            return

        for channel in crm_enabled_channels:
            await self._setup_channel_crm_atomic(
                channel,
                new_agent_pools,
                new_conversation_managers,
                new_contact_to_channel,
                new_ai_handlers,
                new_channel_configs,
                new_ai_handler_pool,
                new_agent_to_channels
            )

        # ATOMIC SWAP: Replace all data structures at once
        self.agent_pools = new_agent_pools
        self.conversation_managers = new_conversation_managers
        self.contact_to_channel = new_contact_to_channel
        self.agent_to_channels = new_agent_to_channels
        self.ai_handlers = new_ai_handlers
        self.channel_configs = new_channel_configs
        self.ai_handler_pool = new_ai_handler_pool

        logger.info(f"CRM инициализирован для {len(self.agent_pools)} каналов")

        # Setup message queue for retry of failed auto-responses
        self._setup_message_queue()

    async def refresh_crm_entities(self):
        """Refresh CRM group entity cache after agents are added to groups.

        Call this after ensure_agents_in_crm_groups() to make sure
        ConversationManagers can access the CRM groups.
        """
        logger.info("[CRM] Refreshing CRM group entity cache...")

        for channel_id, conv_manager in self.conversation_managers.items():
            agent_pool = self.agent_pools.get(channel_id)
            if not agent_pool:
                continue

            group_id = conv_manager.group_id
            entity_found = False

            # Try ALL agents in the pool, not just primary
            for agent in agent_pool.agents:
                if entity_found:
                    break
                try:
                    entity = await agent.client.get_entity(group_id)
                    logger.info(f"  ✅ {channel_id}: CRM accessible via {agent.session_name} (title: {getattr(entity, 'title', 'N/A')})")
                    status_manager.update_crm_status(channel_id, group_id, True)

                    # Update ConversationManager to use this agent's client
                    conv_manager.client = agent.client
                    entity_found = True
                except Exception as e:
                    logger.debug(f"  Agent {agent.session_name} can't access CRM: {e}")
                    continue

            if not entity_found:
                logger.warning(f"  ❌ {channel_id}: No agent can access CRM group {group_id}")
                status_manager.update_crm_status(channel_id, group_id, False, "No agent has access")

    async def _setup_channel_crm(self, channel: ChannelConfig):
        """Настройка CRM для одного канала (legacy wrapper)"""
        await self._setup_channel_crm_atomic(
            channel,
            self.agent_pools,
            self.conversation_managers,
            self.contact_to_channel,
            self.ai_handlers,
            self.channel_configs,
            self.ai_handler_pool,
            self.agent_to_channels
        )

    async def _setup_channel_crm_atomic(
        self,
        channel: ChannelConfig,
        agent_pools: Dict[str, AgentPool],
        conversation_managers: Dict[str, ConversationManager],
        contact_to_channel: Dict[int, str],
        ai_handlers: Dict[str, AIConversationHandler],
        channel_configs: Dict[str, ChannelConfig],
        ai_handler_pool: AIHandlerPool,
        agent_to_channels: Dict[int, List[str]]
    ):
        """Настройка CRM для одного канала (atomic version - writes to provided containers)"""
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

            agent_pools[channel.id] = agent_pool
            channel_configs[channel.id] = channel

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
                # Update CRM status as accessible
                status_manager.update_crm_status(channel.id, channel.crm_group_id, True)
            except Exception as e:
                logger.warning(f"  Агент не может получить доступ к CRM группе: {e}")
                # Update CRM status as inaccessible
                status_manager.update_crm_status(channel.id, channel.crm_group_id, False, str(e))
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
                contact_to_channel[contact_id] = channel.id
            logger.info(f"  Восстановлено {len(conv_manager._topic_cache)} контактов")

            # Регистрируем обработчики
            conv_manager.register_handlers()

            # Регистрируем обработчик входящих сообщений для агентов
            # AND build agent_to_channels mapping
            for agent in agent_pool.agents:
                agent_id = id(agent.client)

                # Track which channels this agent is linked to
                if agent_id not in agent_to_channels:
                    agent_to_channels[agent_id] = []
                if channel.id not in agent_to_channels[agent_id]:
                    agent_to_channels[agent_id].append(channel.id)

                if agent_id not in self._registered_agent_handlers:
                    self._register_contact_message_handler(agent.client, channel.id)
                    self._registered_agent_handlers.add(agent_id)

            conversation_managers[channel.id] = conv_manager

            # Инициализация AI handler
            if channel.ai_conversation_enabled:
                await self._setup_ai_handler_atomic(channel, ai_handlers, ai_handler_pool)

        except Exception as e:
            logger.error(f"  Ошибка настройки CRM для '{channel.name}': {e}", exc_info=True)

    async def _setup_ai_handler(self, channel: ChannelConfig):
        """Инициализация AI handler для канала (legacy wrapper)"""
        await self._setup_ai_handler_atomic(channel, self.ai_handlers, self.ai_handler_pool)

    async def _setup_ai_handler_atomic(
        self,
        channel: ChannelConfig,
        ai_handlers: Dict[str, AIConversationHandler],
        ai_handler_pool: AIHandlerPool
    ):
        """Инициализация AI handler для канала (atomic version)"""
        try:
            ai_config = AIHandlerConfig.from_dict(channel.ai_config.to_dict())
            start_time = time.time()
            ai_handler = await ai_handler_pool.get_or_create(
                channel_id=channel.id,
                ai_config=ai_config,
            )
            latency_ms = int((time.time() - start_time) * 1000)
            ai_handlers[channel.id] = ai_handler

            # Update LLM status
            provider_name = ai_config.provider if hasattr(ai_config, 'provider') else "groq"
            status_manager.update_llm_status(provider_name, True, latency_ms)

            logger.info(f"  AI handler инициализирован (mode: {ai_config.mode})")
        except Exception as ai_error:
            # Update LLM status as unreachable
            provider_name = "groq"  # Default provider
            if hasattr(channel, 'ai_config') and hasattr(channel.ai_config, 'provider'):
                provider_name = channel.ai_config.provider
            status_manager.update_llm_status(provider_name, False, error=str(ai_error))
            logger.warning(f"  Не удалось инициализировать AI: {ai_error}")

    def _setup_message_queue(self):
        """Setup the message queue for retrying failed auto-responses."""

        async def send_callback(
            contact: str,
            text: str,
            channel_id: str,
            resolved_user_id: Optional[int] = None,
            resolved_access_hash: Optional[int] = None
        ) -> bool:
            """Callback for message queue to send messages."""
            agent_pool = self.agent_pools.get(channel_id)
            if not agent_pool:
                logger.warning(f"[QUEUE] No agent pool for channel {channel_id}")
                return False

            # Check if any agent is available
            available_agent = agent_pool.get_available_agent()
            if not available_agent:
                logger.debug(f"[QUEUE] No available agents for channel {channel_id}")
                return False

            # Always use username/contact string for agents
            # (access_hash from bot is session-specific and won't work for agents)
            target: Any = contact
            logger.info(f"[QUEUE] Using contact {contact} (resolved_id={resolved_user_id})")

            try:
                success = await agent_pool.send_message(
                    target,
                    text,
                    max_retries=len(agent_pool.agents) if agent_pool.agents else 1
                )
                return success
            except Exception as e:
                logger.error(f"[QUEUE] Error sending queued message: {e}")
                return False

        message_queue.set_send_callback(send_callback)
        message_queue.start_retry_task()
        logger.info("[CRM] Message queue initialized for auto-response retries")

    def _register_contact_message_handler(self, agent_client: TelegramClient, channel_id: str):
        """Регистрация обработчика входящих сообщений от контактов

        NEW ARCHITECTURE (2025-12-30):
        - Layer 1: AI response (MUST work independently)
        - Layer 2: CRM mirroring (best-effort, never blocks Layer 1)
        """
        logger.info(f"[HANDLER] Registering contact message handler for channel {channel_id}")

        @agent_client.on(events.NewMessage(incoming=True))
        async def handle_contact_message(event):
            """Handle incoming message from contact - AI-first, CRM-secondary"""
            try:
                message = event.message
                logger.info(f"[HANDLER] Incoming message from chat_id={event.chat_id}")

                # === BASIC FILTERING ===
                # Ignore messages from groups
                chat = await event.get_chat()
                if isinstance(chat, (Chat, Channel)):
                    logger.debug(f"[HANDLER] Ignored: message from group/channel")
                    return

                # Ignore outgoing messages
                if message.out:
                    return

                sender = await message.get_sender()
                if not sender:
                    return

                # Ignore messages from self
                try:
                    me = await agent_client.get_me()
                    if sender.id == me.id:
                        return
                except Exception:
                    pass

                # Ignore service messages
                message_text = message.text or ""
                from src.constants import SERVICE_MESSAGE_PREFIXES
                if any(message_text.startswith(p) for p in SERVICE_MESSAGE_PREFIXES):
                    if message_text.startswith("👤 **") and "\n\n" not in message_text:
                        pass  # Not a service message
                    else:
                        return

                sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip() or sender.username or str(sender.id)
                logger.info(f"[HANDLER] Message from {sender_name} (id={sender.id}): {message_text[:50]}...")

                # === FIND CHANNEL BY AGENT (not by topic) ===
                agent_id = id(agent_client)
                linked_channels = self.agent_to_channels.get(agent_id, [])

                if not linked_channels:
                    logger.warning(f"[HANDLER] Agent {agent_id} not linked to any channels")
                    return

                # Use the first channel with AI enabled, or first channel
                channel_id_found = None
                ai_handler = None
                for ch_id in linked_channels:
                    handler = self.ai_handlers.get(ch_id)
                    if handler:
                        channel_id_found = ch_id
                        ai_handler = handler
                        break

                if not channel_id_found:
                    channel_id_found = linked_channels[0]

                logger.info(f"[HANDLER] Using channel {channel_id_found} (AI={'YES' if ai_handler else 'NO'})")

                # Get channel config and conv_manager (may be None)
                channel_config = self.channel_configs.get(channel_id_found)
                conv_manager = self.conversation_managers.get(channel_id_found)

                # Check if this message was sent by agent (to avoid loops)
                if conv_manager and conv_manager.is_agent_sent_message(message.id):
                    logger.debug(f"[HANDLER] Ignoring agent-sent message {message.id}")
                    return

                # === LAYER 1: AI RESPONSE (core functionality) ===
                if ai_handler and message_text:
                    logger.info(f"[HANDLER] Processing AI response for {sender_name}")
                    await self._handle_ai_response_standalone(
                        agent_client=agent_client,
                        contact_id=sender.id,
                        contact_name=sender_name,
                        message_text=message_text,
                        channel_id=channel_id_found,
                        ai_handler=ai_handler,
                        channel_config=channel_config,
                        conv_manager=conv_manager  # May be None, that's OK
                    )

                # === LAYER 2: CRM MIRRORING (best-effort) ===
                if conv_manager:
                    topic_id = conv_manager.get_topic_id(sender.id)

                    # If no topic exists, try to create one on-demand
                    if not topic_id:
                        logger.info(f"[HANDLER] No topic for {sender_name}, creating on-demand...")
                        try:
                            topic_id = await conv_manager.create_topic(
                                title=sender_name,
                                contact_id=sender.id,
                                vacancy_id=None  # No vacancy context for direct messages
                            )
                            if topic_id:
                                logger.info(f"[HANDLER] Created topic {topic_id} for {sender_name}")
                                self.contact_to_channel[sender.id] = channel_id_found
                        except Exception as e:
                            logger.warning(f"[HANDLER] Failed to create topic: {e}")
                            # Continue without CRM - AI already responded

                    # Mirror message to CRM topic
                    if topic_id:
                        try:
                            relay_text = f"👤 **{sender_name}:**\n\n{message_text}"
                            sent_msg = await agent_client.send_message(
                                entity=conv_manager.group_id,
                                message=relay_text,
                                file=message.media if message.media else None,
                                reply_to=topic_id
                            )
                            if sent_msg and hasattr(sent_msg, 'id'):
                                conv_manager.save_message_to_topic(sent_msg.id, topic_id)
                            logger.debug(f"[HANDLER] Mirrored to CRM topic {topic_id}")
                        except Exception as e:
                            logger.warning(f"[HANDLER] CRM mirror failed: {e}")
                            # Don't crash - AI already responded

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
                message.text, topic_id, ai_handler, channel_id
            )

    async def _handle_ai_response(
        self,
        agent_client: TelegramClient,
        conv_manager: ConversationManager,
        contact_id: int,
        contact_name: str,
        message_text: str,
        topic_id: int,
        ai_handler: AIConversationHandler,
        channel_id: str
    ):
        """Обработка AI ответа на сообщение контакта"""
        # Check if instant response is enabled for this channel
        channel_config = self.channel_configs.get(channel_id)
        instant_response = channel_config.instant_response if channel_config else False

        async def send_to_contact(cid: int, text: str) -> bool:
            try:
                # Show typing indicator before sending (skip if instant_response)
                if not instant_response:
                    await human_behavior.simulate_typing(
                        client=agent_client,
                        contact=cid,
                        message_length=len(text)
                    )
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

    async def _handle_ai_response_standalone(
        self,
        agent_client: TelegramClient,
        contact_id: int,
        contact_name: str,
        message_text: str,
        channel_id: str,
        ai_handler: AIConversationHandler,
        channel_config: Optional[ChannelConfig],
        conv_manager: Optional[ConversationManager]
    ):
        """AI response that works independently of CRM (Layer 1 - core functionality)

        This method:
        - Always processes AI response
        - CRM mirroring is optional and best-effort
        - Never fails due to CRM issues
        """
        instant_response = channel_config.instant_response if channel_config else False

        async def send_to_contact(cid: int, text: str) -> bool:
            try:
                # Show typing indicator before sending (skip if instant_response)
                if not instant_response:
                    await human_behavior.simulate_typing(
                        client=agent_client,
                        contact=cid,
                        message_length=len(text)
                    )
                sent = await agent_client.send_message(cid, text)
                if sent:
                    # Mark as agent-sent (if conv_manager available)
                    if conv_manager:
                        conv_manager.mark_agent_sent_message(sent.id)

                    # Best-effort: mirror AI response to CRM topic
                    if conv_manager:
                        topic_id = conv_manager.get_topic_id(cid)
                        if topic_id:
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
                                logger.warning(f"[AI] CRM mirror failed (non-blocking): {e}")
                return True
            except Exception as e:
                logger.error(f"[AI] Error sending response: {e}")
                return False

        async def suggest_in_topic(cid: int, text: str, name: str):
            """Suggest response in CRM topic (best-effort)"""
            if not conv_manager:
                logger.debug("[AI] No conv_manager, skipping suggestion")
                return

            topic_id = conv_manager.get_topic_id(cid)
            if not topic_id:
                logger.debug(f"[AI] No topic for {cid}, skipping suggestion")
                return

            try:
                suggest_msg = f"💡 **AI предлагает ответ:**\n\n{text}\n\n_Отправьте этот текст или напишите свой ответ_"
                await agent_client.send_message(
                    entity=conv_manager.group_id,
                    message=suggest_msg,
                    reply_to=topic_id
                )
            except Exception as e:
                logger.warning(f"[AI] Failed to suggest in topic: {e}")

        # Process AI response asynchronously
        asyncio.create_task(
            ai_handler.handle_message(
                contact_id=contact_id,
                message=message_text,
                contact_name=contact_name,
                send_callback=send_to_contact,
                suggest_callback=suggest_in_topic,
            )
        )
        logger.info(f"[AI] Started async AI response for {contact_name}")

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
            # Log extracted contacts
            tg_contact = contacts.get('telegram')
            logger.info(f"[CRM] Starting workflow. Contacts: telegram={tg_contact}, email={contacts.get('email')}, phone={contacts.get('phone')}")

            contacted_users: Set[str] = set()

            # Pre-resolve telegram contact to user_id + access_hash using bot client
            # This allows agents to send using InputPeerUser even if they haven't "seen" the user
            resolved_user_id: Optional[int] = None
            resolved_access_hash: Optional[int] = None
            telegram_contact = contacts.get('telegram')
            if telegram_contact:
                try:
                    user = await self.bot.client.get_entity(telegram_contact)
                    if hasattr(user, 'id'):
                        resolved_user_id = user.id
                        # Get access_hash for InputPeerUser construction
                        if hasattr(user, 'access_hash') and user.access_hash:
                            resolved_access_hash = user.access_hash
                            logger.info(
                                f"[CRM] Resolved {telegram_contact} to user_id={resolved_user_id}, "
                                f"access_hash={resolved_access_hash}"
                            )
                        else:
                            logger.info(f"[CRM] Resolved {telegram_contact} to user_id={resolved_user_id} (no access_hash)")
                except Exception as e:
                    logger.warning(f"[CRM] Could not resolve {telegram_contact}: {e}")

            for channel in matching_outputs:
                if not channel.crm_enabled:
                    logger.debug(f"[CRM] Skipping channel '{channel.name}': CRM not enabled")
                    continue

                agent_pool = self.agent_pools.get(channel.id)
                conv_manager = self.conversation_managers.get(channel.id)

                if not agent_pool or not conv_manager:
                    logger.warning(f"[CRM] Skipping channel '{channel.name}': no agent_pool or conv_manager")
                    continue

                logger.info(f"[CRM] Processing channel '{channel.name}'...")

                # Get available agent for topic creation
                available_agent = agent_pool.get_available_agent()
                if not available_agent:
                    logger.warning(f"  Нет доступных агентов для '{channel.name}'")
                    continue

                # Look up vacancy_id for attempt logging
                vacancy_id = await db.get_vacancy_id(message.id, chat.id)

                # Auto-response uses pool's send_message with fallback
                # Pass resolved user info to avoid username resolution issues
                auto_response_sent = await self._send_auto_response(
                    channel, agent_pool, contacts, contacted_users,
                    resolved_user_id, resolved_access_hash,
                    vacancy_id=vacancy_id
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
        agent_pool: AgentPool,
        contacts: Dict[str, Optional[str]],
        contacted_users: Set[str],
        resolved_user_id: Optional[int] = None,
        resolved_access_hash: Optional[int] = None,
        vacancy_id: Optional[int] = None
    ) -> bool:
        """Отправка автоответа контакту с fallback через пул агентов"""
        telegram_contact = contacts.get('telegram')

        # Helper to log attempts
        async def log_attempt(status: str, error_type: str = None, error_message: str = None, agent_session: str = None):
            if vacancy_id:
                try:
                    await db.save_auto_response_attempt(
                        vacancy_id=vacancy_id,
                        contact_username=telegram_contact,
                        contact_user_id=resolved_user_id,
                        agent_session=agent_session,
                        status=status,
                        error_type=error_type,
                        error_message=error_message
                    )
                except Exception as e:
                    logger.warning(f"[AUTO-RESPONSE] Failed to log attempt: {e}")

        if not channel.auto_response_enabled or not channel.auto_response_template:
            logger.debug(f"[AUTO-RESPONSE] Skipped: auto_response not enabled for channel '{channel.name}'")
            return False

        if not telegram_contact:
            logger.info(f"[AUTO-RESPONSE] Skipped: no Telegram contact extracted")
            await log_attempt('skipped', 'no_contact', 'No Telegram contact extracted from vacancy')
            return False

        if telegram_contact.lower() in contacted_users:
            logger.debug(f"[AUTO-RESPONSE] Skipped: {telegram_contact} already contacted")
            await log_attempt('skipped', 'already_contacted', f'{telegram_contact} already contacted in this batch')
            return False

        # Always use username for agents - they need to resolve with their own client
        # (access_hash from bot is session-specific and won't work for agents)
        target: Any = telegram_contact
        logger.info(f"[AUTO-RESPONSE] Using username {telegram_contact} (resolved_id={resolved_user_id})")

        try:
            # Use pool's send_message which has built-in agent rotation/fallback
            success = await agent_pool.send_message(
                target,
                channel.auto_response_template,
                max_retries=len(agent_pool.agents) if agent_pool.agents else 3
            )
            if success:
                contacted_users.add(telegram_contact.lower())
                logger.info(f"[AUTO-RESPONSE] ✅ Successfully sent to {telegram_contact}")
                await log_attempt('success')
                return True
            else:
                # All agents failed - queue for retry with resolved info
                logger.warning(f"[AUTO-RESPONSE] ❌ Failed to send to {telegram_contact} (all agents failed)")
                await log_attempt('failed', 'all_agents_failed', 'All agents failed (likely spam limit or invalid peer)')

                await message_queue.add(
                    contact=telegram_contact,
                    text=channel.auto_response_template,
                    channel_id=channel.id,
                    error="All agents failed (likely spam limit)",
                    resolved_user_id=resolved_user_id,
                    resolved_access_hash=resolved_access_hash
                )
                logger.info(f"[AUTO-RESPONSE] 📥 Queued message for {telegram_contact} for later retry")
                await log_attempt('queued', 'retry_scheduled', 'Added to message queue for later retry')

        except Exception as e:
            error_str = str(e)
            logger.error(f"[AUTO-RESPONSE] ❌ Error sending to {telegram_contact}: {e}")

            # Determine error type
            error_type = 'other'
            if "invalid" in error_str.lower() and "peer" in error_str.lower():
                error_type = 'invalid_peer'
            elif "flood" in error_str.lower():
                error_type = 'flood_wait'
            elif "spam" in error_str.lower():
                error_type = 'spam_limit'

            await log_attempt('failed', error_type, error_str[:500])

            # Queue if it's a rate limit error
            if error_type in ('flood_wait', 'spam_limit'):
                await message_queue.add(
                    contact=telegram_contact,
                    text=channel.auto_response_template,
                    channel_id=channel.id,
                    error=error_str,
                    resolved_user_id=resolved_user_id,
                    resolved_access_hash=resolved_access_hash
                )
                logger.info(f"[AUTO-RESPONSE] 📥 Queued message for {telegram_contact} for later retry")
                await log_attempt('queued', 'retry_scheduled', 'Added to message queue for later retry')

        return False

    async def _create_topic_with_fallback(
        self,
        channel: ChannelConfig,
        conv_manager: ConversationManager,
        title: str,
        contact_id: int,
        vacancy_id: Optional[int],
        primary_agent: AgentAccount
    ) -> Optional[int]:
        """
        Try to create a topic, falling back to other agents if the primary is frozen.
        """
        from pathlib import Path

        # First try with the primary agent (conv_manager's client)
        try:
            topic_id = await conv_manager.create_topic(
                title=title,
                contact_id=contact_id,
                vacancy_id=vacancy_id
            )
            if topic_id:
                return topic_id
        except FrozenAccountError as e:
            agent_name = Path(primary_agent.session_name).stem
            logger.warning(f"Agent {agent_name} is frozen, trying other agents...")
            # Update status to show agent is frozen
            status_manager.update_agent_status(
                session_name=agent_name,
                status="frozen",
                phone=primary_agent.phone if hasattr(primary_agent, 'phone') else "",
                error="Account is frozen for forum operations"
            )

        # Try other agents from the pool
        agent_pool = self.agent_pools.get(channel.id)
        if not agent_pool:
            logger.error("No agent pool found for channel")
            return None

        for agent in agent_pool.agents:
            if agent == primary_agent:
                continue  # Skip the primary agent we already tried

            if not agent.client or not agent.client.is_connected():
                continue

            agent_name = Path(agent.session_name).stem
            logger.info(f"Trying to create topic with agent {agent_name}...")

            try:
                # Create a temporary ConversationManager with this agent's client
                from telethon.tl.functions.messages import CreateForumTopicRequest
                import random

                group_entity = await agent.client.get_entity(conv_manager.group_id)
                result = await agent.client(CreateForumTopicRequest(
                    peer=group_entity,
                    title=title[:128],
                    random_id=random.randint(1, 2**31)
                ))

                topic_id = result.updates[0].id

                # Cache in conv_manager
                conv_manager._topic_cache[contact_id] = topic_id
                conv_manager._reverse_topic_cache[topic_id] = contact_id

                # Save to DB
                await db.save_topic_contact(
                    group_id=conv_manager.group_id,
                    topic_id=topic_id,
                    contact_id=contact_id,
                    contact_name=title,
                    vacancy_id=vacancy_id
                )

                logger.info(f"Topic created successfully with agent {agent_name}")
                return topic_id

            except FrozenAccountError:
                logger.warning(f"Agent {agent_name} is also frozen")
                status_manager.update_agent_status(
                    session_name=agent_name,
                    status="frozen",
                    error="Account is frozen for forum operations"
                )
            except Exception as e:
                if "frozen" in str(e).lower():
                    logger.warning(f"Agent {agent_name} is frozen: {e}")
                    status_manager.update_agent_status(
                        session_name=agent_name,
                        status="frozen",
                        error=str(e)
                    )
                else:
                    logger.error(f"Error creating topic with agent {agent_name}: {e}")

        logger.error("All agents failed to create topic (frozen or error)")
        return None

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
                # Topic title: just full name (first + last)
                full_name = contact_user.first_name or ""
                if contact_user.last_name:
                    full_name += f" {contact_user.last_name}"
                full_name = full_name.strip() or f"User {contact_user.id}"

                # Look up vacancy_id for linking
                vacancy_id = await db.get_vacancy_id(message.id, chat.id)

                # Try to create topic, with fallback to other agents if frozen
                topic_id = await self._create_topic_with_fallback(
                    channel=channel,
                    conv_manager=conv_manager,
                    title=full_name[:128],
                    contact_id=contact_user.id,
                    vacancy_id=vacancy_id,
                    primary_agent=agent
                )

                if topic_id:
                    self.contact_to_channel[contact_user.id] = channel.id
                else:
                    logger.error("Не удалось создать топик (все агенты заморожены или ошибка)")
                    return

            # Привязываем агента к теме
            if topic_id:
                self.topic_to_agent[topic_id] = agent
                # Save agent binding to DB
                try:
                    from pathlib import Path
                    agent_name = Path(agent.session_name).stem if agent else None
                    await db.save_topic_contact(
                        group_id=conv_manager.group_id,
                        topic_id=topic_id,
                        contact_id=contact_user.id,
                        contact_name=full_name[:128],
                        agent_session=agent_name,
                        vacancy_id=vacancy_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to save agent binding: {e}")

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
            agent_message = f"🤖 **Агент:**\n\n{channel.auto_response_template}"
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

        await conv_manager.send_to_topic(topic_id, info_message, link_preview=False)

    async def sync_missed_messages(self, lookback_hours: int = 2):
        """
        Sync messages that were missed while the bot was offline.
        Fetches recent messages from all active conversations and processes unhandled ones.
        """
        from datetime import datetime, timedelta

        logger.info(f"Syncing missed messages (lookback: {lookback_hours}h)...")
        synced_count = 0

        for channel_id, conv_manager in self.conversation_managers.items():
            agent_pool = self.agent_pools.get(channel_id)
            if not agent_pool:
                continue

            # Get an agent to fetch messages
            agent = agent_pool.get_available_agent()
            if not agent or not agent.client:
                continue

            ai_handler = self.ai_handlers.get(channel_id)

            # Get all active contacts from the conversation manager
            contacts = list(conv_manager._topic_cache.keys())

            if not contacts:
                continue

            logger.info(f"  Channel {channel_id}: checking {len(contacts)} active conversations")

            for contact_id in contacts:
                try:
                    topic_id = conv_manager.get_topic_id(contact_id)
                    if not topic_id:
                        continue

                    # Try to get the entity - may fail if agent hasn't seen this user
                    try:
                        entity = await agent.client.get_input_entity(contact_id)
                    except ValueError:
                        # User not in agent's entity cache, skip
                        continue

                    # Fetch recent messages from this contact's chat
                    cutoff_time = datetime.now(tz=None) - timedelta(hours=lookback_hours)

                    async for message in agent.client.iter_messages(
                        entity,
                        limit=20,  # Last 20 messages max
                    ):
                        # Skip if message is too old
                        if message.date and message.date.replace(tzinfo=None) < cutoff_time:
                            break

                        # Skip outgoing messages
                        if message.out:
                            continue

                        # Skip if already processed (agent sent it)
                        if conv_manager.is_agent_sent_message(message.id):
                            continue

                        # Skip if already synced (check database)
                        if await db.is_message_synced(contact_id, message.id):
                            continue

                        # Skip non-text messages for now
                        if not message.text:
                            continue

                        # Skip service messages
                        from src.constants import SERVICE_MESSAGE_PREFIXES
                        if any(message.text.startswith(p) for p in SERVICE_MESSAGE_PREFIXES):
                            continue

                        sender = await message.get_sender()
                        if not sender:
                            continue

                        logger.info(f"    Syncing missed message from {contact_id}: {message.text[:50]}...")

                        # Relay to CRM topic
                        await self._relay_contact_message_to_topic(
                            agent.client, conv_manager, sender, message,
                            topic_id, ai_handler, channel_id
                        )

                        # Mark as synced to avoid duplicate processing
                        await db.mark_message_synced(contact_id, message.id)
                        synced_count += 1

                        # Small delay between messages
                        await asyncio.sleep(0.5)

                except Exception as e:
                    logger.warning(f"    Error syncing messages from {contact_id}: {e}")
                    continue

        logger.info(f"Synced {synced_count} missed messages")
        return synced_count

    async def cleanup(self):
        """Очистка ресурсов CRM"""
        # Stop message queue retry task
        message_queue.stop_retry_task()

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
