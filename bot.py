"""
Основной модуль Telegram userbot для мониторинга вакансий
"""
import logging
from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
from typing import List, Set
from config import config
from database import db
from message_processor import message_processor
from template_engine import template_engine

logger = logging.getLogger(__name__)


class JobMonitorBot:
    """Класс для мониторинга вакансий в Telegram чатах"""
    
    def __init__(self):
        self.client = TelegramClient(
            config.SESSION_NAME,
            config.API_ID,
            config.API_HASH
        )
        
        self.monitored_channels: Set[int] = set()
        self.is_running = False
    
    async def start(self):
        """Запуск бота"""
        logger.info("Запуск Telegram userbot...")
        
        # Подключение к Telegram
        await self.client.start(phone=config.PHONE)
        
        # Проверка авторизации
        me = await self.client.get_me()
        logger.info(f"Бот авторизован как: {me.first_name} ({me.phone})")
        
        # Загрузка списка каналов для мониторинга
        await self.load_channels()
        
        # Регистрация обработчиков событий
        self.register_handlers()
        
        self.is_running = True
        logger.info("Бот успешно запущен и готов к работе!")
    
    async def load_channels(self):
        """Загружает список каналов из файла"""
        try:
            with open(config.CHANNELS_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                
                # Пропускаем комментарии и пустые строки
                if not line or line.startswith('#'):
                    continue
                
                try:
                    # Если это ID (число), преобразуем в int
                    if line.lstrip('-').isdigit():
                        channel_id = int(line)
                    else:
                        # Иначе это username, получаем entity
                        entity = await self.client.get_entity(line)
                        channel_id = entity.id
                    
                    self.monitored_channels.add(channel_id)
                    logger.info(f"Добавлен канал для мониторинга: {line} (ID: {channel_id})")
                
                except Exception as e:
                    logger.error(f"Ошибка при загрузке канала '{line}': {e}")
            
            if not self.monitored_channels:
                logger.warning(f"Не найдено каналов для мониторинга в {config.CHANNELS_FILE}")
            else:
                logger.info(f"Всего загружено {len(self.monitored_channels)} каналов для мониторинга")
        
        except FileNotFoundError:
            logger.error(f"Файл {config.CHANNELS_FILE} не найден")
        except Exception as e:
            logger.error(f"Ошибка при загрузке каналов: {e}")
    
    def register_handlers(self):
        """Регистрирует обработчики событий"""
        
        @self.client.on(events.NewMessage())
        async def handle_new_message(event):
            """Обработчик новых сообщений"""
            try:
                message = event.message
                chat = await event.get_chat()
                
                # Проверяем, нужно ли мониторить этот чат
                if chat.id not in self.monitored_channels:
                    return
                
                # Игнорируем собственные сообщения
                if message.out:
                    return
                
                await self.process_message(message, chat)
            
            except Exception as e:
                logger.error(f"Ошибка при обработке нового сообщения: {e}", exc_info=True)
        
        logger.info("Обработчики событий зарегистрированы")
    
    async def process_message(self, message, chat):
        """
        Обрабатывает сообщение из отслеживаемого чата (упрощенная версия без AI)
        
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
        
        # Извлечение информации (без AI)
        contacts = message_processor.extract_contact_info(message.text)
        keywords = message_processor.extract_keywords(message.text)
        payment_info = message_processor.extract_payment_info(message.text)
        
        # Простая фильтрация по ключевым словам
        is_relevant = self._check_relevance(message.text, keywords)
        
        # Сохраняем в базу данных
        await db.save_job(
            message_id=message.id,
            chat_id=chat.id,
            chat_title=chat_title,
            message_text=message.text,
            position=None,  # Без AI не определяем позицию
            skills=keywords,
            is_relevant=is_relevant,
            ai_reason="Filtered by keywords" if is_relevant else "No relevant keywords",
            status='relevant' if is_relevant else 'not_relevant'
        )
        
        # Если сообщение подходит - отправляем уведомление
        if is_relevant:
            await self.send_notification_simple(
                message=message,
                chat=chat,
                chat_title=chat_title,
                keywords=keywords,
                contacts=contacts,
                payment_info=payment_info
            )
        else:
            logger.debug(f"Сообщение не содержит релевантных ключевых слов")
    
    def _check_relevance(self, text: str, keywords: List[str]) -> bool:
        """
        Проверка релевантности по маркетинговым ключевым словам
        
        Args:
            text: Текст сообщения
            keywords: Найденные ключевые слова
        
        Returns:
            True если сообщение релевантно (содержит маркетинговые термины)
        """
        text_lower = text.lower()
        
        # Обязательные маркетинговые термины (хотя бы один должен быть)
        required_terms = [
            'маркетолог', 'маркетинг',
            'таргетолог', 'таргет', 'таргетинг',
            'трафик', 'трафик-менеджер',
            'smm', 'performance',
            'фейсбук', 'facebook', 'meta ads',
            'digital', 'диджитал'
        ]
        
        # Проверяем наличие обязательных терминов
        has_required = any(term in text_lower for term in required_terms)
        
        if not has_required:
            logger.debug("Сообщение не содержит маркетинговых терминов")
            return False
        
        # Если нашли маркетинговые ключевые слова из списка
        if keywords:
            logger.info(f"✓ Найдены маркетинговые ключевые слова: {', '.join(keywords[:5])}")
            return True
        
        # Дополнительная проверка на маркеры вакансий + маркетинг
        job_markers = [
            'вакансия', 'vacancy', 'ищем', 'требуется', 'нужен', 
            'hiring', 'looking for', 'работа', 'удаленно', 'remote'
        ]
        
        found_markers = [m for m in job_markers if m in text_lower]
        if found_markers and has_required:
            logger.info(f"✓ Найдены маркеры вакансий + маркетинг: {', '.join(found_markers[:3])}")
            return True
        
        return False
    
    async def send_notification_simple(
        self,
        message,
        chat,
        chat_title: str,
        keywords: List[str],
        contacts: dict,
        payment_info: dict
    ):
        """Отправляет упрощенное уведомление (без AI, без шаблонов)"""
        logger.info("Найдено подходящее сообщение! Отправка уведомления...")
        
        # Получаем информацию об отправителе
        sender_info = message_processor.get_sender_info(message)
        
        # Формируем ссылку на сообщение
        message_link = message_processor.get_message_link(message, chat)
        
        # Форматируем уведомление
        lines = []
        lines.append("🎯 **Новая вакансия!**")
        lines.append("")
        
        # Оплата (если есть) - показываем первой!
        if payment_info.get('raw'):
            payment_line = f"💰 **Оплата:** {payment_info['raw']}"
            if payment_info.get('type'):
                payment_line += f" ({payment_info['type']})"
            lines.append(payment_line)
            lines.append("")
        
        # Чат
        lines.append(f"📍 **Чат:** {chat_title}")
        
        # Ключевые навыки (кратко)
        if keywords:
            lines.append(f"🛠 **Навыки:** {', '.join(keywords[:5])}")
        
        # Ссылка на сообщение
        lines.append("")
        lines.append(f"🔗 **Перейти:** {message_link}")
        
        # Контакты
        contacts_list = []
        
        # Контакт автора из метаданных
        if sender_info.get('username'):
            contacts_list.append(f"👤 Автор: {sender_info['username']}")
        elif sender_info.get('full_name'):
            contacts_list.append(f"👤 Автор: {sender_info['full_name']}")
        
        # Контакты из текста
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
        
        # Текст сообщения
        lines.append("")
        lines.append("─" * 40)
        preview = message.text[:800] + "..." if len(message.text) > 800 else message.text
        lines.append(preview)
        lines.append("─" * 40)
        
        notification_text = '\n'.join(lines)
        
        try:
            # Определяем куда отправлять уведомление
            if config.NOTIFICATION_CHANNEL_ID != 0:
                # Если указан ID канала - отправляем в канал
                target = config.NOTIFICATION_CHANNEL_ID
                target_name = f"канал {config.NOTIFICATION_CHANNEL_ID}"
            elif config.NOTIFICATION_USER_ID != 0:
                # Иначе отправляем пользователю
                target = config.NOTIFICATION_USER_ID
                target_name = f"пользователю {config.NOTIFICATION_USER_ID}"
            else:
                # По умолчанию - себе в Избранное
                target = "me"
                target_name = "в 'Избранное'"
            
            await self.client.send_message(
                target,
                notification_text
            )
            
            logger.info(f"Уведомление отправлено {target_name}")
        
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления: {e}", exc_info=True)
    
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
        
        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("Получен сигнал остановки")
        finally:
            await self.stop()
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Остановка бота...")
        self.is_running = False
        
        if self.client.is_connected():
            await self.client.disconnect()
        
        logger.info("Бот остановлен")


# Глобальный экземпляр бота
bot = JobMonitorBot()

