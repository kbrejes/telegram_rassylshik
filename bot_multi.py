"""
Telegram userbot для мониторинга вакансий с поддержкой множественных каналов
+ CRM функциональность (автоответы и трансляция в топики)
"""
import asyncio
import logging
import os
from pathlib import Path
from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
from typing import List, Set, Dict, Optional
from config import config
from database import db
from message_processor import message_processor
from config_manager import ConfigManager, ChannelConfig
from agent_account import AgentAccount
from agent_pool import AgentPool
from conversation_manager import ConversationManager

logger = logging.getLogger(__name__)


class ChannelNameLogFilter(logging.Filter):
    """Фильтр для замены ID каналов на их имена в логах"""
    
    def __init__(self, channel_map: Dict[int, str]):
        super().__init__()
        self.channel_map = channel_map
        self.unknown_channels = set()
    
    def filter(self, record):
        """Заменяет ID каналов на имена в сообщениях логов"""
        try:
            if record.args:
                try:
                    formatted_message = record.msg % record.args
                except:
                    return True
            else:
                formatted_message = str(record.msg)
            
            import re
            pattern = r'channel (\d+)'
            
            def replace_channel_id(match):
                channel_id = int(match.group(1))
                
                if channel_id in self.channel_map:
                    return f'"{self.channel_map[channel_id]}" (ID: {channel_id})'
                
                if channel_id not in self.unknown_channels:
                    self.unknown_channels.add(channel_id)
                
                return f'[Unknown Channel] (ID: {channel_id})'
            
            formatted_message = re.sub(pattern, replace_channel_id, formatted_message)
            record.msg = formatted_message
            record.args = ()
            
        except Exception:
            pass
        
        return True


class MultiChannelJobMonitorBot:
    """Класс для мониторинга вакансий с поддержкой множественных output каналов"""
    
    def __init__(self):
        self.client = TelegramClient(
            config.SESSION_NAME,
            config.API_ID,
            config.API_HASH
        )
        
        self.monitored_sources: Set[int] = set()  # ID источников для мониторинга
        self.channel_names: Dict[int, str] = {}  # ID -> название
        
        # Config manager для работы с output каналами
        self.config_manager = ConfigManager()
        self.output_channels: List[ChannelConfig] = []
        
        # CRM функциональность
        self.agent_pools: Dict[str, AgentPool] = {}  # channel_id -> AgentPool
        self.conversation_managers: Dict[str, ConversationManager] = {}  # channel_id -> ConversationManager
        self.contact_to_channel: Dict[int, str] = {}  # contact_id -> channel_id (для маршрутизации)
        # Привязка topic_id -> агент, через которого ведется переписка
        self.topic_to_agent: Dict[int, AgentAccount] = {}
        
        # Для отслеживания изменений конфигурации
        self.config_file_path = Path("configs/channels_config.json")
        self.last_config_mtime = None
        
        self.is_running = False
    
    async def start(self):
        """Запуск бота"""
        logger.info("Запуск Multi-Channel Telegram userbot...")
        
        # Подключение к Telegram
        await self.client.start(phone=config.PHONE)
        
        # Проверка авторизации
        me = await self.client.get_me()
        logger.info(f"Бот авторизован как: {me.first_name} ({me.phone})")
        
        # Подключение к базе данных
        await db.connect()
        
        # Загрузка конфигурации output каналов
        await self.load_output_channels()
        
        # Загрузка всех уникальных input источников
        await self.load_input_sources()
        
        # Инициализация CRM агентов и conversation managers
        await self.setup_crm_agents()
        
        # Настройка фильтра логов
        self._setup_log_filter()
        
        # Регистрация обработчиков событий
        self.register_handlers()
        
        # Сохраняем время модификации конфига при старте
        if self.config_file_path.exists():
            self.last_config_mtime = os.path.getmtime(self.config_file_path)
    
    async def load_output_channels(self):
        """Загружает конфигурацию output каналов из ConfigManager"""
        try:
            self.output_channels = self.config_manager.load()
            
            enabled_channels = [ch for ch in self.output_channels if ch.enabled]
            
            if not enabled_channels:
                logger.warning("Нет активных output каналов в конфигурации")
            else:
                logger.info(f"Загружено {len(enabled_channels)} активных output каналов:")
                for ch in enabled_channels:
                    logger.info(f"  - {ch.name} (ID: {ch.telegram_id})")
        
        except Exception as e:
            logger.error(f"Ошибка загрузки output каналов: {e}")
            self.output_channels = []
    
    async def load_input_sources(self):
        """Загружает все уникальные input источники из output каналов"""
        try:
            # Собираем все уникальные источники
            all_sources = self.config_manager.get_all_input_sources()
            
            if not all_sources:
                logger.warning("Не найдено источников для мониторинга")
                return
            
            logger.info(f"Загрузка {len(all_sources)} input источников...")
            
            for source in all_sources:
                try:
                    # Если это ID (число), преобразуем в int
                    if source.lstrip('-').isdigit():
                        channel_id = int(source)
                        entity = await self.client.get_entity(channel_id)
                    else:
                        # Иначе это username, получаем entity
                        entity = await self.client.get_entity(source)
                        channel_id = entity.id
                    
                    # Получаем название канала
                    channel_title = self._get_chat_title(entity)
                    
                    self.monitored_sources.add(channel_id)
                    self.channel_names[channel_id] = channel_title
                
                except Exception as e:
                    logger.error(f"  ✗ Ошибка загрузки источника '{source}': {e}")
            
            logger.info(f"Всего загружено {len(self.monitored_sources)} источников для мониторинга")
        
        except Exception as e:
            logger.error(f"Ошибка при загрузке input источников: {e}")
    
    def _setup_log_filter(self):
        """Настраивает фильтр для замены ID каналов на имена в логах"""
        telethon_logger = logging.getLogger('telethon.client.updates')
        log_filter = ChannelNameLogFilter(self.channel_names)
        telethon_logger.addFilter(log_filter)
        
        root_telethon = logging.getLogger('telethon')
        root_telethon.addFilter(log_filter)
    
    async def setup_crm_agents(self):
        """Инициализация CRM агентов и conversation managers для каналов"""
        logger.info("🤖 Инициализация CRM агентов...")
        
        crm_enabled_channels = [ch for ch in self.output_channels if ch.crm_enabled]
        
        if not crm_enabled_channels:
            logger.info("Нет каналов с включенным CRM")
            return
        
        for channel in crm_enabled_channels:
            try:
                logger.info(f"Настройка CRM для канала '{channel.name}'...")
                
                # Валидация конфигурации
                if not channel.agents:
                    # Backward compatibility: check old format
                    if channel.agent_phone and channel.agent_session_name:
                        from config_manager import AgentConfig
                        channel.agents = [AgentConfig(
                            phone=channel.agent_phone,
                            session_name=channel.agent_session_name
                        )]
                    else:
                        logger.warning(f"  ⚠️ Канал '{channel.name}': нет агентов, CRM пропущен")
                        continue
                
                if not channel.crm_group_id:
                    logger.warning(f"  ⚠️ Канал '{channel.name}': не указан crm_group_id, CRM пропущен")
                    continue
                
                # Создаем пул агентов
                agent_pool = AgentPool(channel.agents)
                
                # Инициализируем пул
                if await agent_pool.initialize():
                    self.agent_pools[channel.id] = agent_pool
                    
                    # Получаем первого доступного агента для conversation manager
                    primary_agent = agent_pool.get_available_agent()
                    if primary_agent:
                        # Создаем conversation manager с callback для отправки сообщений через закрепленного агента
                        # ВАЖНО: group_monitor_client - основной клиент бота для мониторинга группы
                        # client - клиент агента для создания топиков
                        logger.debug(f"  Создание ConversationManager для группы {channel.crm_group_id} с callback")
                        conv_manager = ConversationManager(
                            client=primary_agent.client,  # Клиент агента для создания топиков
                            group_id=channel.crm_group_id,
                            send_contact_message_cb=self._send_message_from_topic_to_contact,
                            group_monitor_client=self.client  # Основной клиент бота для мониторинга группы
                        )
                        logger.debug(f"  ConversationManager создан, callback: {'задан' if conv_manager.send_contact_message_cb else 'не задан'}")
                        logger.debug(f"  group_monitor_client: {type(conv_manager.group_monitor_client).__name__}, client: {type(conv_manager.client).__name__}")
                        
                        # Регистрируем обработчики трансляции
                        conv_manager.register_handlers()
                        
                        # Регистрируем обработчик входящих сообщений от контактов для всех агентов
                        for agent in agent_pool.agents:
                            self._register_contact_message_handler(agent.client, conv_manager, channel.id)
                        
                        self.conversation_managers[channel.id] = conv_manager
                    else:
                        logger.error(f"  ❌ Нет доступных агентов для conversation manager '{channel.name}'")
                else:
                    logger.error(f"  ❌ Не удалось инициализировать пул агентов для '{channel.name}'")
            
            except Exception as e:
                logger.error(f"  ❌ Ошибка настройки CRM для '{channel.name}': {e}", exc_info=True)
        
        logger.info(f"CRM инициализирован для {len(self.agent_pools)} каналов")
    
    def _register_contact_message_handler(
        self,
        agent_client: TelegramClient,
        conv_manager: ConversationManager,
        channel_id: str
    ):
        """
        Регистрация обработчика входящих сообщений от контактов к агенту
        
        Args:
            agent_client: Telegram client агента
            conv_manager: ConversationManager для этого канала
            channel_id: ID output канала
        """
        
        @agent_client.on(events.NewMessage(incoming=True))
        async def handle_contact_message(event):
            """Трансляция сообщения от контакта в топик"""
            try:
                message = event.message
                
                # Игнорируем сообщения из групп (только личные диалоги)
                chat = await event.get_chat()
                if isinstance(chat, (Chat, Channel)):
                    return
                
                # Игнорируем собственные сообщения
                if message.out:
                    return
                
                # Проверяем, не было ли это сообщение отправлено агентом контакту
                if conv_manager.is_agent_sent_message(message.id):
                    return
                
                # Получаем ID отправителя
                sender = await message.get_sender()
                if not sender:
                    return
                
                # Проверяем, что сообщение не от самого агента
                try:
                    me = await agent_client.get_me()
                    if sender.id == me.id:
                        return
                except Exception:
                    pass
                
                # Игнорируем служебные сообщения
                message_text = message.text or ""
                if message_text.startswith("🤖 **Агент (") or message_text.startswith("📌 **Новый контакт:") or message_text.startswith("📋 **Вакансия из"):
                    return
                
                # Игнорируем сообщения с подписью "👤 **"
                if message_text.startswith("👤 **") and "\n\n" in message_text:
                    return
                
                # Проверяем есть ли топик для этого контакта
                topic_id = conv_manager.get_topic_id(sender.id)
                
                if topic_id:
                    # Отправляем сообщение от контакта в топик с подписью автора
                    # (forward_messages не поддерживает reply_to для топиков)
                    sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
                    if not sender_name and sender.username:
                        sender_name = f"@{sender.username}"
                    if not sender_name:
                        sender_name = f"User {sender.id}"
                    
                    # Формируем текст с подписью автора
                    message_text = f"👤 **{sender_name}:**\n\n{message.text or ''}"
                    
                    sent_msg = await agent_client.send_message(
                        entity=conv_manager.group_id,
                        message=message_text,
                        file=message.media if message.media else None,
                        reply_to=topic_id
                    )
                    # Сохраняем связь message_id -> topic_id
                    if sent_msg and hasattr(sent_msg, 'id'):
                        conv_manager.save_message_to_topic(sent_msg.id, topic_id)
            
            except Exception as e:
                logger.error(f"Ошибка в handle_contact_message: {e}", exc_info=True)
        
        logger.debug(f"Обработчик входящих сообщений зарегистрирован для канала {channel_id}")
    
    async def _send_message_from_topic_to_contact(
        self,
        contact_id: int,
        text: str,
        media,
        topic_id: int
    ):
        """
        Отправка сообщения из темы CRM-группы контакту через закрепленного за темой агента.
        
        Args:
            contact_id: ID контакта
            text: Текст сообщения
            media: Медиа файл (если есть)
            topic_id: ID топика
        """
        try:
            # Пытаемся найти канал, к которому привязан контакт
            channel_id = self.contact_to_channel.get(contact_id)
            if not channel_id:
                return

            # Ищем агента, закрепленного за этой темой
            agent = self.topic_to_agent.get(topic_id)
            if not agent:
                # Фоллбек: берем доступного агента из пула канала
                agent_pool = self.agent_pools.get(channel_id)
                if not agent_pool:
                    logger.error(f"Нет пула агентов для канала {channel_id}")
                    return
                
                agent = agent_pool.get_available_agent()
                if not agent:
                    logger.error(f"Нет доступных агентов для отправки сообщения контакту {contact_id}")
                    return

            if not agent.client:
                logger.error(f"У агента {agent.session_name} нет активного клиента")
                return

            # Отправляем сообщение контакту от имени выбранного агента
            try:
                # Проверяем тип медиа - MessageMediaWebPage нельзя использовать как file
                media_file = None
                if media:
                    from telethon.tl.types import MessageMediaWebPage
                    if not isinstance(media, MessageMediaWebPage):
                        media_file = media
                
                if media_file:
                    sent_message = await agent.client.send_message(
                        contact_id,
                        text or "",
                        file=media_file
                    )
                else:
                    sent_message = await agent.client.send_message(
                        contact_id,
                        text or ""
                    )
                
                # Помечаем сообщение как отправленное агентом, чтобы не зеркалировать обратно
                if sent_message and hasattr(sent_message, 'id'):
                    conv_manager = self.conversation_managers.get(channel_id)
                    if conv_manager:
                        conv_manager.mark_agent_sent_message(sent_message.id)

            except Exception as send_error:
                logger.error(f"Ошибка при отправке сообщения через агента {agent.session_name}: {send_error}", exc_info=True)
                raise

        except Exception as e:
            logger.error(f"Ошибка в _send_message_from_topic_to_contact: {e}", exc_info=True)
    
    def register_handlers(self):
        """Регистрирует обработчики событий"""
        
        @self.client.on(events.NewMessage())
        async def handle_new_message(event):
            """Обработчик новых сообщений"""
            try:
                message = event.message
                chat = await event.get_chat()
                
                # Проверяем, нужно ли мониторить этот чат
                if chat.id not in self.monitored_sources:
                    return
                
                # Игнорируем собственные сообщения
                if message.out:
                    return
                
                await self.process_message(message, chat)
            
            except Exception as e:
                logger.error(f"Ошибка при обработке нового сообщения: {e}", exc_info=True)
        
        logger.info("Обработчики событий зарегистрированы")
    
    async def watch_config_changes(self):
        """Фоновая задача для отслеживания изменений конфигурации"""
        logger.info("Запущен мониторинг изменений конфигурации (проверка каждые 30 сек)")
        
        while True:
            try:
                await asyncio.sleep(30)  # Проверка каждые 30 секунд
                
                if not self.config_file_path.exists():
                    continue
                
                # Получаем время модификации файла
                current_mtime = os.path.getmtime(self.config_file_path)
                
                # Если файл изменился
                if self.last_config_mtime and current_mtime != self.last_config_mtime:
                    logger.info("Обнаружены изменения в конфигурации! Перезагрузка...")
                    
                    # Перезагружаем конфигурацию
                    await self.reload_configuration()
                    
                    logger.info("Конфигурация перезагружена успешно")
                
                self.last_config_mtime = current_mtime
                
            except Exception as e:
                logger.error(f"Ошибка при проверке конфигурации: {e}")
    
    async def reload_configuration(self):
        """Перезагрузка конфигурации без перезапуска бота"""
        try:
            # Загружаем output каналы
            await self.load_output_channels()
            
            # Получаем новый список источников
            new_sources = self.config_manager.get_all_input_sources()
            new_sources_str = {str(s) for s in new_sources}
            
            # Добавляем новые источники (которых еще нет)
            for source in new_sources:
                source_str = str(source)
                
                # Проверяем есть ли уже этот источник
                already_monitored = False
                
                if source.lstrip('-').isdigit():
                    # Это ID
                    source_id = int(source)
                    if source_id in self.monitored_sources:
                        already_monitored = True
                else:
                    # Это username - проверяем по имени
                    for monitored_id in self.monitored_sources:
                        if self.channel_names.get(monitored_id, '').lower() == source.lower():
                            already_monitored = True
                            break
                
                if not already_monitored:
                    try:
                        # Загружаем entity для нового источника
                        if source.lstrip('-').isdigit():
                            channel_id = int(source)
                            entity = await self.client.get_entity(channel_id)
                        else:
                            entity = await self.client.get_entity(source)
                            channel_id = entity.id
                        
                        channel_title = self._get_chat_title(entity)
                        self.monitored_sources.add(channel_id)
                        self.channel_names[channel_id] = channel_title
                        
                        logger.info(f"  ➕ Добавлен новый источник: {source} → {channel_title}")
                    
                    except Exception as e:
                        logger.error(f"  ✗ Ошибка загрузки нового источника '{source}': {e}")
            
            # Удаляем источники, которых больше нет в конфигурации
            sources_to_remove = []
            
            for monitored_id in list(self.monitored_sources):
                # Проверяем есть ли этот ID в новой конфигурации
                found = False
                
                # Проверка по ID
                if str(monitored_id) in new_sources_str or str(-monitored_id) in new_sources_str:
                    found = True
                else:
                    # Проверка по username
                    for source in new_sources:
                        if not source.lstrip('-').isdigit():
                            try:
                                entity = await self.client.get_entity(source)
                                if entity.id == monitored_id:
                                    found = True
                                    break
                            except:
                                pass
                
                if not found:
                    sources_to_remove.append(monitored_id)
            
            for source_id in sources_to_remove:
                channel_name = self.channel_names.get(source_id, str(source_id))
                self.monitored_sources.remove(source_id)
                if source_id in self.channel_names:
                    del self.channel_names[source_id]
                logger.info(f"  ➖ Удален источник: {channel_name}")
            
            logger.info(f"Мониторится: {len(self.monitored_sources)} источников, {len(self.output_channels)} output каналов")
        
        except Exception as e:
            logger.error(f"Ошибка перезагрузки конфигурации: {e}", exc_info=True)
    
    async def process_message(self, message, chat):
        """
        Обрабатывает сообщение из отслеживаемого чата для всех output каналов
        
        Args:
            message: Объект сообщения Telethon
            chat: Объект чата
        """
        # Получаем название чата
        chat_title = self._get_chat_title(chat)
        
        logger.info(f"Получено сообщение {message.id} из чата '{chat_title}'")
        
        # Первичная фильтрация
        if not message_processor.should_process_message(message):
            return
        
        # Проверка на дубликат
        is_duplicate = await db.check_duplicate(message.id, chat.id)
        if is_duplicate:
            logger.debug(f"Сообщение {message.id} уже обрабатывалось ранее")
            return
        
        # Извлечение информации
        contacts = message_processor.extract_contact_info(message.text)
        keywords = message_processor.extract_keywords(message.text)
        payment_info = message_processor.extract_payment_info(message.text)
        
        # Определяем в какие output каналы нужно отправить это сообщение
        matching_outputs = self._find_matching_outputs(chat, message.text, keywords)
        
        if not matching_outputs:
            logger.debug("Сообщение не подходит ни под один output канал")
            # Сохраняем как нерелевантное
            await db.save_job(
                message_id=message.id,
                chat_id=chat.id,
                chat_title=chat_title,
                message_text=message.text,
                position=None,
                skills=keywords,
                is_relevant=False,
                ai_reason="No matching output channels",
                status='not_relevant'
            )
            return
        
        # Сохраняем в базу данных
        await db.save_job(
            message_id=message.id,
            chat_id=chat.id,
            chat_title=chat_title,
            message_text=message.text,
            position=None,
            skills=keywords,
            is_relevant=True,
            ai_reason=f"Matches {len(matching_outputs)} output channels",
            status='relevant'
        )
        
        # Отправляем уведомления во все matching output каналы
        await self.send_notifications(
            message=message,
            chat=chat,
            chat_title=chat_title,
            keywords=keywords,
            contacts=contacts,
            payment_info=payment_info,
            output_channels=matching_outputs
        )
        
        # CRM workflow: автоответ + создание топика
        await self.handle_crm_workflow(
            message=message,
            chat=chat,
            chat_title=chat_title,
            matching_outputs=matching_outputs,
            contacts=contacts
        )
    
    async def handle_crm_workflow(
        self,
        message,
        chat,
        chat_title: str,
        matching_outputs: List[ChannelConfig],
        contacts: Dict[str, Optional[str]]
    ):
        """
        Обработка CRM workflow: автоответ + создание топика
        
        Args:
            message: Объект сообщения
            chat: Объект чата источника
            chat_title: Название чата
            matching_outputs: Список matching output каналов
            contacts: Словарь с извлеченными контактами (telegram, email, phone)
        """
        try:
            # Проходим по всем matching каналам с включенным CRM
            for channel in matching_outputs:
                if not channel.crm_enabled:
                    continue
                
                # Проверяем что для этого канала есть пул агентов и conv_manager
                agent_pool = self.agent_pools.get(channel.id)
                conv_manager = self.conversation_managers.get(channel.id)
                
                if not agent_pool or not conv_manager:
                    logger.debug(f"CRM не настроен для канала '{channel.name}'")
                    continue
                
                logger.info(f"🤖 CRM workflow для канала '{channel.name}'...")

                # Выбираем агента, который будет вести переписку по этому контакту/теме
                available_agent = agent_pool.get_available_agent()
                if not available_agent:
                    logger.warning(f"  ⚠️ Нет доступных агентов для CRM канала '{channel.name}'")
                    continue
                
                auto_response_sent = False
                
                # 1. Отправить автоответ (если включено)
                if channel.auto_response_enabled and channel.auto_response_template:
                    try:
                        # Проверяем есть ли telegram контакт в объявлении
                        if contacts.get('telegram'):
                            # Отправляем автоответ конкретным агентом
                            success = await available_agent.send_message(
                                contacts['telegram'],  # Передаем @username, не ID
                                channel.auto_response_template
                            )
                            
                            if success:
                                auto_response_sent = True
                            else:
                                logger.warning(f"  ⚠️ Не удалось отправить автоответ через агента {available_agent.session_name}: {contacts['telegram']}")
                    
                    except Exception as e:
                        logger.error(f"  ❌ Ошибка отправки автоответа: {e}")
                
                # 2. Создать топик в CRM группе
                topic_id: Optional[int] = None
                contact_user: Optional[User] = None
                
                try:
                    # Проверяем есть ли telegram контакт
                    if not contacts.get('telegram'):
                        continue
                    
                    # Резолвим username в User entity через ОСНОВНОГО бота
                    try:
                        contact_user = await self.client.get_entity(contacts['telegram'])
                        
                        if not isinstance(contact_user, User):
                            continue
                        
                        # ВАЖНО: Агент тоже должен знать о контакте для дальнейшей трансляции
                        # Резолвим через выбранного агента, чтобы добавить в его кэш
                        try:
                            await available_agent.client.get_entity(contacts['telegram'])
                        except Exception as e:
                            logger.debug(f"  ⚠️ Агент {available_agent.session_name} не смог резолвить {contacts['telegram']}: {e}")
                        
                        # Проверяем есть ли уже топик для этого контакта
                        existing_topic = conv_manager.get_topic_id(contact_user.id)
                        
                        if existing_topic:
                            topic_id = existing_topic
                        else:
                            # Создаем новый топик
                            sender_name = f"{contact_user.first_name}"
                            if contact_user.username:
                                sender_name += f" (@{contact_user.username})"
                            
                            topic_title = f"{sender_name} | {chat_title[:80]}"
                            topic_id = await conv_manager.create_topic(
                                title=topic_title[:128],
                                contact_id=contact_user.id
                            )
                        
                            if topic_id:
                                # Сохраняем маршрутизацию: contact -> channel
                                self.contact_to_channel[contact_user.id] = channel.id
                            else:
                                logger.error(f"  ❌ Не удалось создать топик")
                                continue
                        
                        # Привязываем выбранного агента к этой теме
                        if topic_id:
                            self.topic_to_agent[topic_id] = available_agent
                        
                        # 3. Зеркалируем автоответ агента в тему (если был отправлен)
                        if auto_response_sent and topic_id:
                            try:
                                agent_name = available_agent.session_name
                                # Отправляем автоответ в тему с подписью агента
                                agent_message = f"🤖 **Агент ({agent_name}):**\n\n{channel.auto_response_template}"
                                sent_msg = await available_agent.client.send_message(
                                    entity=channel.crm_group_id,
                                    message=agent_message,
                                    reply_to=topic_id
                                )
                                # Сохраняем связь message_id -> topic_id
                                if sent_msg and hasattr(sent_msg, 'id'):
                                    conv_manager.save_message_to_topic(sent_msg.id, topic_id)
                            except Exception as e:
                                logger.error(f"  ❌ Ошибка зеркалирования автоответа в топик: {e}")
                        
                        # 4. Отправляем информацию и (опционально) исходное объявление в топик
                        if topic_id and contact_user:
                            sender_info = f"{contact_user.first_name}"
                            if contact_user.username:
                                sender_info += f" (@{contact_user.username})"
                            
                            # Информационное сообщение
                            info_message = f"📌 **Новый контакт: {sender_info}**\n\n"
                            info_message += f"📍 **Канал вакансии:** {chat_title}\n"
                            info_message += f"🔗 **Ссылка:** {message_processor.get_message_link(message, chat)}"
                            
                            await conv_manager.send_to_topic(topic_id, info_message)
                            
                            # Опционально: пересылаем исходное объявление с вакансией в топик
                            if channel.mirror_job_post_to_topic:
                                # Используем send_message с подписью и reply_to для гарантированного попадания в топик
                                # (forward_messages не поддерживает reply_to для топиков)
                                vacancy_text = f"📋 **Вакансия из {chat_title}:**\n\n{message.text or ''}"
                                
                                # Проверяем тип медиа - MessageMediaWebPage нельзя использовать как file
                                media_file = None
                                if message.media:
                                    from telethon.tl.types import MessageMediaWebPage
                                    if not isinstance(message.media, MessageMediaWebPage):
                                        media_file = message.media
                                
                                sent_msg = await self.client.send_message(
                                    entity=channel.crm_group_id,
                                    message=vacancy_text,
                                    file=media_file,
                                    reply_to=topic_id
                                )
                                # Сохраняем связь message_id -> topic_id
                                if sent_msg and hasattr(sent_msg, 'id'):
                                    conv_manager.save_message_to_topic(sent_msg.id, topic_id)
                    
                    except ValueError as e:
                        logger.warning(f"  ⚠️ Не удалось найти пользователя {contacts['telegram']}: {e}")
                
                except Exception as e:
                    logger.error(f"  ❌ Ошибка создания топика: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"Ошибка в CRM workflow: {e}", exc_info=True)
    
    def _find_matching_outputs(
        self, 
        chat, 
        text: str, 
        keywords: List[str]
    ) -> List[ChannelConfig]:
        """
        Находит output каналы, которым подходит данное сообщение
        
        Args:
            chat: Объект чата источника
            text: Текст сообщения
            keywords: Найденные ключевые слова
        
        Returns:
            Список подходящих output каналов
        """
        matching = []
        text_lower = text.lower()
        
        # Получаем все output каналы, которые мониторят этот источник
        source_id = str(chat.id)
        potential_outputs = self.config_manager.get_output_channels_for_source(source_id)
        
        # Если нет по ID, пробуем по username
        if not potential_outputs and hasattr(chat, 'username') and chat.username:
            potential_outputs = self.config_manager.get_output_channels_for_source(f"@{chat.username}")
        
        # Проверяем фильтры для каждого output канала
        for output in potential_outputs:
            if self._check_filters(text_lower, keywords, output.filters):
                matching.append(output)
        
        return matching
    
    def _check_filters(self, text_lower: str, keywords: List[str], filters) -> bool:
        """
        Проверка фильтров для канала
        
        Args:
            text_lower: Текст сообщения в нижнем регистре
            keywords: Найденные ключевые слова
            filters: Объект FilterConfig
        
        Returns:
            True если сообщение проходит фильтры
        """
        # Проверка включающих ключевых слов
        if filters.include_keywords:
            include_lower = [kw.lower() for kw in filters.include_keywords]
            
            if filters.require_all_includes:
                # Требуются ВСЕ ключевые слова
                if not all(kw in text_lower for kw in include_lower):
                    return False
            else:
                # Требуется ХОТЯ БЫ одно ключевое слово
                if not any(kw in text_lower for kw in include_lower):
                    return False
        
        # Проверка исключающих ключевых слов
        if filters.exclude_keywords:
            exclude_lower = [kw.lower() for kw in filters.exclude_keywords]
            
            # Если есть хотя бы одно исключающее слово - отклоняем
            if any(kw in text_lower for kw in exclude_lower):
                logger.debug(f"Сообщение содержит исключающие слова: {[kw for kw in exclude_lower if kw in text_lower]}")
                return False
        
        return True
    
    async def send_notifications(
        self,
        message,
        chat,
        chat_title: str,
        keywords: List[str],
        contacts: dict,
        payment_info: dict,
        output_channels: List[ChannelConfig]
    ):
        """Отправляет уведомления во все подходящие output каналы"""
        logger.info(f"Отправка уведомлений в {len(output_channels)} output каналов...")
        
        # Получаем информацию об отправителе
        sender_info = message_processor.get_sender_info(message)
        
        # Формируем ссылку на сообщение
        message_link = message_processor.get_message_link(message, chat)
        
        # Форматируем уведомление
        lines = []
        lines.append("🎯 **Новая вакансия!**")
        lines.append("")
        lines.append(f"📍 **Чат:** {chat_title}")
        
        if keywords:
            lines.append(f"🛠 **Навыки:** {', '.join(keywords[:5])}")
        
        lines.append("")
        lines.append(f"🔗 **Перейти:** {message_link}")
        
        # Контакты
        contacts_list = []
        
        if sender_info.get('username'):
            contacts_list.append(f"✉️ {sender_info['username']}")
        elif sender_info.get('full_name'):
            contacts_list.append(f"👤 {sender_info['full_name']}")
        
        if contacts.get('telegram') and contacts['telegram'] != sender_info.get('username'):
            contacts_list.append(f"✉️ {contacts['telegram']}")
        if contacts.get('email'):
            contacts_list.append(f"📧 {contacts['email']}")
        if contacts.get('phone'):
            contacts_list.append(f"📞 {contacts['phone']}")
        
        if contacts_list:
            lines.append("")
            lines.append("**Контакты:**")
            for contact in contacts_list:
                lines.append(f"   {contact}")
        
        notification_text = '\n'.join(lines)
        
        # Отправляем во все output каналы
        success_count = 0
        for output in output_channels:
            try:
                # Получаем entity канала чтобы Telethon знал о нём
                try:
                    entity = await self.client.get_entity(output.telegram_id)
                    entity_title = self._get_chat_title(entity)
                    logger.info(f"  📤 Отправка в '{output.name}' → Telegram: '{entity_title}' (ID: {output.telegram_id})")
                except Exception as entity_error:
                    logger.error(f"  ✗ Не удалось получить entity для '{output.name}' (ID: {output.telegram_id}): {entity_error}")
                    logger.info(f"  💡 Убедитесь что бот имеет доступ к этому каналу/группе")
                    continue
                
                # Отправляем сообщение
                sent_message = await self.client.send_message(
                    entity,
                    notification_text
                )
                success_count += 1
            
            except Exception as e:
                logger.error(f"  ✗ Ошибка отправки в '{output.name}': {e}")
        
        if success_count > 0:
            logger.info(f"Успешно отправлено {success_count}/{len(output_channels)} уведомлений")
    
    def _get_chat_title(self, chat) -> str:
        """Получает название чата"""
        if isinstance(chat, User):
            return f"{chat.first_name} {chat.last_name or ''}".strip()
        elif isinstance(chat, (Chat, Channel)):
            return chat.title or f"Chat {chat.id}"
        else:
            return f"Unknown chat {chat.id}"
    
    async def run(self):
        """Основной цикл работы бота"""
        logger.info("Бот начал мониторинг сообщений...")
        logger.info("Нажмите Ctrl+C для остановки")
        
        # Запускаем фоновую задачу мониторинга конфигурации
        config_watcher = asyncio.create_task(self.watch_config_changes())
        
        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("Получен сигнал остановки")
        finally:
            config_watcher.cancel()  # Останавливаем watcher
            await self.stop()
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Остановка бота...")
        self.is_running = False
        
        # Отключаем CRM пулы агентов
        for channel_id, agent_pool in self.agent_pools.items():
            try:
                await agent_pool.disconnect_all()
            except Exception as e:
                logger.error(f"Ошибка отключения пула агентов для канала {channel_id}: {e}")
        
        self.agent_pools.clear()
        
        # Закрываем соединение с БД
        await db.close()
        
        if self.client.is_connected():
            await self.client.disconnect()
        
        logger.info("Бот остановлен")


# Глобальный экземпляр бота
bot = MultiChannelJobMonitorBot()

