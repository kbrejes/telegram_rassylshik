"""
Conversation Manager - управление топиками и трансляцией сообщений
Адаптировано из crm_response_bot
"""
import asyncio
import logging
import random
from typing import Optional, Dict
from telethon import TelegramClient, events
from telethon import errors

from database import db

# Forum topics support (requires Telethon 1.37+)
try:
    from telethon.tl.functions.messages import CreateForumTopicRequest
except ImportError:
    try:
        from telethon.tl.functions.channels import CreateForumTopicRequest
    except ImportError:
        CreateForumTopicRequest = None

logger = logging.getLogger(__name__)


class ConversationManager:
    """Управление форум-топиками и трансляцией сообщений"""
    
    def __init__(self, client: TelegramClient, group_id: int, send_contact_message_cb=None, group_monitor_client=None):
        """
        Инициализация
        
        Args:
            client: Telegram client instance (агент) - для создания топиков и отправки сообщений
            group_id: ID группы с форум-топиками
            send_contact_message_cb: Опциональный callback для отправки сообщений контакту
                                    (contact_id, text, media, topic_id) -> None
            group_monitor_client: Telegram client для мониторинга группы (если отличается от client)
                                 Если не задан, используется client
        """
        self.client = client  # Клиент агента для создания топиков
        self.group_monitor_client = group_monitor_client or client  # Клиент для мониторинга группы
        self.group_id = group_id
        self.send_contact_message_cb = send_contact_message_cb
        
        # Кэш: contact_id -> topic_id
        self._topic_cache: Dict[int, int] = {}
        
        # Кэш: topic_id -> contact_id
        self._reverse_topic_cache: Dict[int, int] = {}
        
        # Кэш: message_id -> topic_id (для сообщений, отправленных в топик)
        self._message_to_topic_cache: Dict[int, int] = {}
        
        # Кэш: message_id сообщений, отправленных агентом контакту (чтобы не зеркалировать обратно)
        self._agent_sent_messages: set = set()

        logger.info(f"ConversationManager инициализирован для группы: {group_id}")

    async def load_cache_from_db(self):
        """Загружает кэш topic_id <-> contact_id из базы данных"""
        try:
            mappings = await db.load_all_topic_contacts(self.group_id)
            for topic_id, contact_id in mappings.items():
                self._topic_cache[contact_id] = topic_id
                self._reverse_topic_cache[topic_id] = contact_id
            logger.info(f"Загружено {len(mappings)} маппингов из БД для группы {self.group_id}")
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша из БД: {e}")
    
    async def create_topic(
        self,
        title: str,
        contact_id: int,
        retry_count: int = 0,
        max_retries: int = 3
    ) -> Optional[int]:
        """
        Создание нового топика в форум-группе
        
        Args:
            title: Название топика (обычно имя контакта)
            contact_id: ID контакта в Telegram
            retry_count: Текущая попытка (внутреннее использование)
            max_retries: Максимум попыток
            
        Returns:
            ID топика или None если не удалось создать
        """
        try:
            if CreateForumTopicRequest is None:
                logger.error("CreateForumTopicRequest не доступен. Обновите Telethon: pip install -U telethon")
                return None

            logger.info(f"Создание топика '{title}' для контакта {contact_id}")

            # Сначала получаем entity группы (агент должен знать о ней)
            try:
                group_entity = await self.client.get_entity(self.group_id)
            except ValueError as e:
                logger.error(f"Агент не имеет доступа к группе {self.group_id}. Добавьте агента в CRM группу!")
                return None

            # Создаем топик через Telethon API
            result = await self.client(CreateForumTopicRequest(
                peer=group_entity,
                title=title[:128],  # Ограничение Telegram
                random_id=random.randint(1, 2**31)
            ))
            
            # Извлекаем topic_id из ответа
            topic_id = result.updates[0].id

            # Кэшируем в памяти
            self._topic_cache[contact_id] = topic_id
            self._reverse_topic_cache[topic_id] = contact_id

            # Сохраняем в БД для персистентности
            try:
                await db.save_topic_contact(
                    group_id=self.group_id,
                    topic_id=topic_id,
                    contact_id=contact_id,
                    contact_name=title
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения в БД: {e}")

            return topic_id
            
        except errors.FloodWaitError as e:
            if retry_count < max_retries:
                wait_time = min(e.seconds, 30)  # Макс 30 сек
                logger.warning(f"FloodWait: ждем {wait_time} сек, попытка {retry_count + 1}/{max_retries}")
                await asyncio.sleep(wait_time)
                return await self.create_topic(title, contact_id, retry_count + 1, max_retries)
            else:
                logger.error(f"Не удалось создать топик после {max_retries} попыток")
                return None
                
        except errors.ChatWriteForbiddenError:
            logger.error("Нет прав на создание топиков в группе (ChatWriteForbiddenError)")
            return None
            
        except errors.ChannelPrivateError:
            logger.error("Группа приватная или агент не имеет доступа (ChannelPrivateError)")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка создания топика: {e}", exc_info=True)
            return None
    
    def get_topic_id(self, contact_id: int) -> Optional[int]:
        """Получить ID топика для контакта"""
        return self._topic_cache.get(contact_id)
    
    def get_contact_id(self, topic_id: int) -> Optional[int]:
        """Получить ID контакта по ID топика"""
        return self._reverse_topic_cache.get(topic_id)
    
    async def send_to_topic(self, topic_id: int, text: str, file=None) -> bool:
        """
        Отправка сообщения в топик
        
        Args:
            topic_id: ID топика
            text: Текст сообщения
            file: Опциональный медиа файл
            
        Returns:
            True если отправлено успешно
        """
        try:
            sent_message = await self.client.send_message(
                self.group_id,
                text,
                file=file,
                reply_to=topic_id  # Важно: reply_to для топика
            )
            # Сохраняем связь message_id -> topic_id для последующего определения топика
            if sent_message and hasattr(sent_message, 'id'):
                self._message_to_topic_cache[sent_message.id] = topic_id
                logger.debug(f"Сохранена связь message_id={sent_message.id} -> topic_id={topic_id}")
            logger.info(f"Сообщение отправлено в топик {topic_id}")
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки в топик {topic_id}: {e}")
            return False
    
    def save_message_to_topic(self, message_id: int, topic_id: int):
        """
        Сохранить связь message_id -> topic_id вручную
        
        Args:
            message_id: ID сообщения
            topic_id: ID топика
        """
        self._message_to_topic_cache[message_id] = topic_id
    
    def mark_agent_sent_message(self, message_id: int):
        """
        Пометить сообщение как отправленное агентом контакту (чтобы не зеркалировать обратно)
        
        Args:
            message_id: ID сообщения
        """
        self._agent_sent_messages.add(message_id)
    
    def is_agent_sent_message(self, message_id: int) -> bool:
        """
        Проверить, было ли сообщение отправлено агентом контакту
        
        Args:
            message_id: ID сообщения
            
        Returns:
            True если сообщение было отправлено агентом
        """
        return message_id in self._agent_sent_messages
    
    def register_handlers(self):
        """
        Регистрация обработчиков для двусторонней трансляции сообщений
        """

        # Telethon use positive channel IDs internally, extract from -100XXXXXXXXXX format
        channel_id = self.group_id
        if channel_id < 0:
            # Convert from Bot API format (-100XXXXXXXXXX) to Telethon format
            channel_id_str = str(abs(channel_id))
            if channel_id_str.startswith('100') and len(channel_id_str) > 10:
                channel_id = int(channel_id_str[3:])  # Strip '100' prefix

        logger.info(f"  Регистрация обработчика CRM: group_id={self.group_id}, channel_id={channel_id}")

        @self.group_monitor_client.on(events.NewMessage())
        async def handle_message_from_topic(event):
            """Обработка сообщения из топика → отправить контакту"""
            try:
                message = event.message
                chat_id = message.chat_id

                # Проверяем, что сообщение из нужной группы (поддерживаем оба формата ID)
                is_our_group = (
                    chat_id == self.group_id or  # Bot API format: -100XXXXXXXXXX
                    chat_id == channel_id or      # Telethon format: positive
                    chat_id == -channel_id        # Negative without 100 prefix
                )

                if not is_our_group:
                    return  # Silent skip for other chats

                logger.info(f"📩 CRM: chat_id={chat_id}, out={message.out}, from={message.sender_id}, text='{message.text[:30] if message.text else 'media'}...'")

                # Игнорируем только сообщения от самого бота-монитора
                # (агенты отправляют служебные сообщения через свои клиенты)
                try:
                    me = await self.group_monitor_client.get_me()
                    logger.info(f"  Проверка: sender={message.sender_id}, bot={me.id}")
                    if message.sender_id == me.id:
                        logger.debug(f"  Пропуск: сообщение от бота")
                        return
                except Exception as e:
                    logger.error(f"  Ошибка get_me(): {e}")
                    # Продолжаем обработку если не можем проверить
                
                # Определяем topic_id
                topic_id = None
                
                # Способ 1: через reply_to.reply_to_top_id
                if message.reply_to:
                    topic_id = getattr(message.reply_to, 'reply_to_top_id', None)
                    if not topic_id:
                        # Проверяем, является ли это форум-топиком
                        is_forum_topic = getattr(message.reply_to, 'forum_topic', False)
                        if is_forum_topic:
                            reply_to_msg_id = getattr(message.reply_to, 'reply_to_msg_id', None)
                            if reply_to_msg_id:
                                topic_id = reply_to_msg_id
                
                # Способ 2: прямой атрибут сообщения
                if not topic_id:
                    topic_id = getattr(message, 'reply_to_top_id', None)
                
                # Способ 3: через message_thread_id
                if not topic_id:
                    topic_id = getattr(message, 'message_thread_id', None)
                
                # Способ 4: проверяем кэш message_id -> topic_id
                if not topic_id:
                    topic_id = self._message_to_topic_cache.get(message.id)
                
                # Способ 5: через API
                if not topic_id and not message.reply_to:
                    try:
                        full_message = await self.group_monitor_client.get_messages(
                            self.group_id,
                            ids=message.id
                        )
                        if full_message and hasattr(full_message, 'reply_to') and full_message.reply_to:
                            topic_id = getattr(full_message.reply_to, 'reply_to_top_id', None)
                            if not topic_id:
                                reply_to_msg_id = getattr(full_message.reply_to, 'reply_to_msg_id', None)
                                if reply_to_msg_id:
                                    topic_id = reply_to_msg_id
                    except Exception:
                        pass
                
                if not topic_id:
                    logger.warning(f"  Пропуск: topic_id не определен. reply_to={message.reply_to}")
                    return

                logger.info(f"  topic_id={topic_id}, кэш: {self._reverse_topic_cache}")

                # Находим контакт для этого топика - сначала в памяти, потом в БД
                contact_id = self.get_contact_id(topic_id)
                if not contact_id:
                    # Пробуем загрузить из БД
                    try:
                        contact_data = await db.get_contact_by_topic(self.group_id, topic_id)
                        if contact_data:
                            contact_id = contact_data['contact_id']
                            # Обновляем кэш в памяти
                            self._topic_cache[contact_id] = topic_id
                            self._reverse_topic_cache[topic_id] = contact_id
                            logger.info(f"  Загружен из БД: topic {topic_id} -> contact {contact_id}")
                    except Exception as e:
                        logger.error(f"  Ошибка загрузки из БД: {e}")

                if not contact_id:
                    logger.warning(f"  ⚠️ Контакт для топика {topic_id} не найден ни в кэше, ни в БД")
                    return

                # Отправляем сообщение контакту
                try:
                    message_text = message.text or ""
                    logger.info(f"  📤 Отправка контакту {contact_id} из топика {topic_id}: '{message_text[:50]}...'")

                    # Игнорируем служебные сообщения
                    if message_text.startswith("🤖 **Агент (") or message_text.startswith("📌 **Новый контакт:") or message_text.startswith("📋 **Вакансия из") or message_text.startswith("👤 **"):
                        logger.debug(f"  Пропуск: служебное сообщение")
                        return

                    if not message_text and not message.media:
                        logger.debug(f"  Пропуск: пустое сообщение")
                        return

                    # Если задан callback - используем его
                    if self.send_contact_message_cb:
                        logger.info(f"  Используем callback для отправки")
                        await self.send_contact_message_cb(
                            contact_id=contact_id,
                            text=message_text,
                            media=message.media,
                            topic_id=topic_id
                        )
                        logger.info(f"  ✅ Сообщение отправлено через callback")
                    else:
                        # Fallback: отправляем через текущего клиента (агента)
                        logger.info(f"  Используем агента {type(self.client).__name__} для отправки")
                        if message.media:
                            await self.client.send_message(
                                contact_id,
                                message_text,
                                file=message.media
                            )
                        else:
                            await self.client.send_message(
                                contact_id,
                                message_text
                            )
                        logger.info(f"  ✅ Сообщение отправлено через агента")
                except Exception as e:
                    logger.error(f"  ❌ Ошибка отправки контакту {contact_id}: {e}", exc_info=True)
            
            except Exception as e:
                logger.error(f"Ошибка в handle_message_from_topic: {e}", exc_info=True)
        
        logger.info(f"Обработчики трансляции зарегистрированы для группы {self.group_id}")
    
    async def mirror_contact_message_to_topic(
        self,
        contact_id: int,
        message_text: str,
        topic_id: Optional[int] = None
    ) -> bool:
        """
        Трансляция сообщения от контакта в топик
        
        Args:
            contact_id: ID контакта
            message_text: Текст сообщения
            topic_id: ID топика (если уже известен)
            
        Returns:
            True если отправлено успешно
        """
        try:
            # Если топик не указан, ищем в кэше
            if topic_id is None:
                topic_id = self.get_topic_id(contact_id)
            
            if not topic_id:
                logger.warning(f"Нет топика для контакта {contact_id}")
                return False
            
            # Отправляем в топик
            formatted_text = f"💬 **Сообщение от контакта:**\n\n{message_text}"
            return await self.send_to_topic(topic_id, formatted_text)
            
        except Exception as e:
            logger.error(f"Ошибка трансляции сообщения: {e}")
            return False

