"""
Модуль для обработки и фильтрации сообщений
"""
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from telethon.tl.types import Message, User, Chat, Channel
from config import config

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Класс для обработки и фильтрации сообщений из Telegram"""
    
    def __init__(self):
        self.max_message_age = timedelta(hours=config.MAX_MESSAGE_AGE_HOURS)
    
    def should_process_message(self, message: Message) -> bool:
        """
        Определяет, нужно ли обрабатывать сообщение
        
        Args:
            message: Объект сообщения Telethon
        
        Returns:
            True если сообщение подходит для обработки
        """
        # Проверка на наличие текста
        if not message.text:
            logger.debug(f"Сообщение {message.id} пропущено: нет текста")
            return False
        
        # Проверка на минимальную длину (слишком короткие сообщения вряд ли вакансии)
        if len(message.text.strip()) < 50:
            logger.debug(f"Сообщение {message.id} пропущено: слишком короткое ({len(message.text)} символов)")
            return False
        
        # Проверка возраста сообщения
        if message.date:
            message_age = datetime.now(message.date.tzinfo) - message.date
            if message_age > self.max_message_age:
                logger.debug(f"Сообщение {message.id} пропущено: слишком старое ({message_age})")
                return False
        
        # Проверка на служебные сообщения
        if message.action:
            logger.debug(f"Сообщение {message.id} пропущено: служебное")
            return False
        
        return True
    
    def extract_contact_info(self, text: str) -> Dict[str, Optional[str]]:
        """
        Извлекает контактную информацию из текста
        
        Returns:
            Словарь с найденными контактами
        """
        contacts = {
            'telegram': None,
            'email': None,
            'phone': None
        }
        
        # Поиск Telegram username
        telegram_match = re.search(r'@([a-zA-Z0-9_]{5,32})', text)
        if telegram_match:
            contacts['telegram'] = f"@{telegram_match.group(1)}"
        
        # Поиск email
        email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if email_match:
            contacts['email'] = email_match.group(0)
        
        # Поиск телефона (упрощенный паттерн)
        phone_match = re.search(r'\+?\d[\d\s\-\(\)]{8,}\d', text)
        if phone_match:
            contacts['phone'] = phone_match.group(0)
        
        return contacts
    
    def extract_payment_info(self, text: str) -> Dict[str, Optional[str]]:
        """
        Извлекает информацию об оплате из текста
        
        Returns:
            Словарь с информацией об оплате
        """
        payment_info = {
            'amount': None,
            'currency': None,
            'type': None,
            'raw': None
        }
        
        text_lower = text.lower()
        
        # Поиск зарплаты в рублях
        rub_patterns = [
            r'(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+)[\s\u00A0]*(?:-[\s\u00A0]*(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+))?[\s\u00A0]*(?:₽|руб|рублей|рубля|р\.)',
            r'(?:зп|зарплата|оплата|ставка)[\s:]*(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+)[\s\u00A0]*(?:-[\s\u00A0]*(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+))?[\s\u00A0]*(?:₽|руб|рублей|рубля|р\.)?',
        ]
        
        for pattern in rub_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_from = match.group(1).replace(' ', '').replace('\u00A0', '')
                amount_to = match.group(2).replace(' ', '').replace('\u00A0', '') if match.group(2) else None
                
                if amount_to:
                    payment_info['amount'] = f"{amount_from}-{amount_to}"
                    payment_info['raw'] = f"{amount_from}-{amount_to} ₽"
                else:
                    payment_info['amount'] = amount_from
                    payment_info['raw'] = f"{amount_from} ₽"
                
                payment_info['currency'] = 'RUB'
                break
        
        # Поиск в долларах
        if not payment_info['amount']:
            usd_patterns = [
                r'(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+)[\s\u00A0]*(?:-[\s\u00A0]*(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+))?[\s\u00A0]*\$',
                r'\$[\s\u00A0]*(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+)[\s\u00A0]*(?:-[\s\u00A0]*(\d+[\s\u00A0]*(?:\d+[\s\u00A0]*)*\d+))?',
            ]
            
            for pattern in usd_patterns:
                match = re.search(pattern, text)
                if match:
                    amount_from = match.group(1).replace(' ', '').replace('\u00A0', '')
                    amount_to = match.group(2).replace(' ', '').replace('\u00A0', '') if match.group(2) else None
                    
                    if amount_to:
                        payment_info['amount'] = f"{amount_from}-{amount_to}"
                        payment_info['raw'] = f"${amount_from}-{amount_to}"
                    else:
                        payment_info['amount'] = amount_from
                        payment_info['raw'] = f"${amount_from}"
                    
                    payment_info['currency'] = 'USD'
                    break
        
        # Определение типа оплаты
        if 'проект' in text_lower or 'за проект' in text_lower:
            payment_info['type'] = 'за проект'
        elif 'час' in text_lower or 'в час' in text_lower or '/час' in text_lower:
            payment_info['type'] = 'в час'
        elif 'день' in text_lower or 'в день' in text_lower or '/день' in text_lower:
            payment_info['type'] = 'в день'
        elif 'месяц' in text_lower or 'в месяц' in text_lower or '/месяц' in text_lower or 'ежемесячно' in text_lower:
            payment_info['type'] = 'в месяц'
        elif 'оклад' in text_lower:
            payment_info['type'] = 'оклад'
        
        return payment_info
    
    def extract_keywords(self, text: str) -> List[str]:
        """
        Извлекает ключевые слова специфичные для FB/IG таргет + performance marketing
        
        Returns:
            Список найденных ключевых слов
        """
        # УЗКАЯ СПЕЦИАЛИЗАЦИЯ: только Facebook/Instagram таргет + performance
        keywords_db = [
            # Facebook & Instagram
            'facebook', 'fb', 'фейсбук', 'фб',
            'meta', 'мета', 'meta ads',
            'instagram', 'инстаграм', 'инста', 'ig',
            'facebook ads', 'fb ads', 'meta ads',
            'instagram ads', 'ig ads',
            'ads manager', 'менеджер рекламы', 'рекламный кабинет',
            'business manager',
            
            # Таргетинг
            'таргет', 'таргетолог', 'таргетолога', 'таргетологи',
            'таргетинг', 'таргетированная реклама',
            'таргетолога', 'таргетированной рекламы',
            
            # Performance Marketing
            'performance', 'перформанс',
            'performance marketing', 'перформанс маркетинг',
            'performance маркетолог',
            
            # Метрики FB/IG
            'cpa', 'cpc', 'cpm', 'ctr', 'roas', 'roi',
            'конверсия', 'конверсии', 'conversion',
            'cpv', 'cpl', 'cpi',
            
            # Процессы таргетинга
            'кампания', 'рекламная кампания', 'campaign',
            'креатив', 'креативы', 'creative',
            'аудитория', 'аудитории', 'audience',
            'сегмент', 'сегментация', 'lookalike',
            'пиксель', 'pixel', 'facebook pixel',
            'ретаргетинг', 'ретаргет', 'retargeting',
            
            # Лидогенерация
            'лиды', 'лид', 'lead', 'leads',
            'лидогенерация', 'лидген', 'lead generation',
            'лид-формы', 'lead forms',
            
            # Оптимизация
            'масштабирование', 'scale', 'scaling',
            'оптимизация', 'optimization',
            'тестирование', 'a/b тест', 'сплит-тест',
            
            # Платформы (только FB/IG экосистема)
            'whatsapp', 'messenger', 'мессенджер',
        ]
        
        text_lower = text.lower()
        found_keywords = []
        
        for keyword in keywords_db:
            # Используем word boundaries для точного поиска
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text_lower, re.IGNORECASE):
                found_keywords.append(keyword)
        
        return list(set(found_keywords))  # Убираем дубликаты
    
    def get_message_link(self, message: Message, chat) -> str:
        """
        Формирует ссылку на сообщение
        
        Returns:
            URL ссылка на сообщение
        """
        try:
            # Для публичных каналов и групп
            if hasattr(chat, 'username') and chat.username:
                return f"https://t.me/{chat.username}/{message.id}"
            
            # Для приватных чатов используем формат с chat_id
            # Это не всегда работает, но лучше чем ничего
            chat_id = str(chat.id).replace('-100', '')
            return f"https://t.me/c/{chat_id}/{message.id}"
        
        except Exception as e:
            logger.error(f"Ошибка при формировании ссылки: {e}")
            return f"Chat ID: {chat.id}, Message ID: {message.id}"
    
    def get_sender_info(self, message: Message) -> Dict[str, Optional[str]]:
        """
        Извлекает информацию об отправителе
        
        Returns:
            Словарь с информацией об отправителе
        """
        sender_info = {
            'id': None,
            'username': None,
            'first_name': None,
            'full_name': None
        }
        
        if not message.sender:
            return sender_info
        
        sender = message.sender
        
        if isinstance(sender, User):
            sender_info['id'] = sender.id
            sender_info['username'] = f"@{sender.username}" if sender.username else None
            sender_info['first_name'] = sender.first_name
            
            full_name_parts = [sender.first_name]
            if sender.last_name:
                full_name_parts.append(sender.last_name)
            sender_info['full_name'] = ' '.join(full_name_parts) if full_name_parts else None
        
        return sender_info
    
    def format_notification(
        self,
        chat_title: str,
        position: Optional[str],
        skills: List[str],
        message_link: str,
        sender_info: Dict,
        contacts: Dict,
        response_text: str,
        ai_reason: Optional[str] = None
    ) -> str:
        """
        Форматирует уведомление для отправки пользователю
        
        Returns:
            Отформатированный текст уведомления
        """
        lines = []
        lines.append("🎯 **Новая релевантная вакансия!**")
        lines.append("")
        lines.append(f"📍 **Чат:** {chat_title}")
        
        if position:
            lines.append(f"💼 **Позиция:** {position}")
        
        if skills:
            lines.append(f"🛠 **Технологии:** {', '.join(skills[:10])}")  # Ограничиваем 10 навыками
        
        lines.append("")
        lines.append(f"🔗 **Ссылка:** {message_link}")
        
        # Информация об отправителе
        if sender_info.get('username') or sender_info.get('full_name'):
            lines.append("")
            lines.append("👤 **Контакт автора:**")
            if sender_info.get('username'):
                lines.append(f"   Telegram: {sender_info['username']}")
            if sender_info.get('full_name'):
                lines.append(f"   Имя: {sender_info['full_name']}")
        
        # Дополнительные контакты из текста
        if any(contacts.values()):
            lines.append("")
            lines.append("📞 **Контакты из объявления:**")
            if contacts.get('telegram'):
                lines.append(f"   Telegram: {contacts['telegram']}")
            if contacts.get('email'):
                lines.append(f"   Email: {contacts['email']}")
            if contacts.get('phone'):
                lines.append(f"   Телефон: {contacts['phone']}")
        
        # AI reasoning (опционально)
        if ai_reason:
            lines.append("")
            lines.append(f"🤖 **AI анализ:** {ai_reason}")
        
        # Предложенный ответ
        lines.append("")
        lines.append("=" * 40)
        lines.append("📝 **ПРЕДЛОЖЕННЫЙ ОТВЕТ:**")
        lines.append("=" * 40)
        lines.append("")
        lines.append(response_text)
        lines.append("")
        lines.append("=" * 40)
        lines.append("")
        lines.append("✅ Если все ок — скопируйте ответ и отправьте в личку автору")
        
        return '\n'.join(lines)


# Глобальный экземпляр процессора
message_processor = MessageProcessor()

