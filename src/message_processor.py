"""
Module for message processing and filtering
"""
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from telethon.tl.types import Message, User, Chat, Channel
from src.config import config

logger = logging.getLogger(__name__)


@dataclass
class ContactInfo:
    """Contact information from message"""
    telegram: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        """For backward compatibility"""
        return {'telegram': self.telegram, 'email': self.email, 'phone': self.phone}

    def has_any(self) -> bool:
        """Check if at least one contact exists"""
        return bool(self.telegram or self.email or self.phone)


@dataclass
class PaymentInfo:
    """Payment information from message"""
    amount: Optional[str] = None
    currency: Optional[str] = None  # 'RUB', 'USD', 'EUR'
    payment_type: Optional[str] = None  # 'hourly', 'monthly', 'per project'
    raw: Optional[str] = None  # Original string

    def to_dict(self) -> Dict[str, Optional[str]]:
        """For backward compatibility"""
        return {
            'amount': self.amount,
            'currency': self.currency,
            'type': self.payment_type,
            'raw': self.raw
        }


@dataclass
class SenderInfo:
    """Sender information from message"""
    id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    full_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        """For backward compatibility"""
        return {
            'id': self.id,
            'username': self.username,
            'first_name': self.first_name,
            'full_name': self.full_name
        }


class MessageProcessor:
    """Class for processing and filtering Telegram messages"""

    def __init__(self):
        self.max_message_age = timedelta(hours=config.MAX_MESSAGE_AGE_HOURS)

    def should_process_message(self, message: Message) -> bool:
        """
        Determines whether the message should be processed

        Args:
            message: Telethon message object

        Returns:
            True if message is suitable for processing
        """
        # Check for text presence
        if not message.text:
            logger.debug(f"Message {message.id} skipped: no text")
            return False

        # Check minimum length (too short messages are unlikely to be job postings)
        if len(message.text.strip()) < 50:
            logger.debug(f"Message {message.id} skipped: too short ({len(message.text)} chars)")
            return False

        # Check message age
        if message.date:
            message_age = datetime.now(message.date.tzinfo) - message.date
            if message_age > self.max_message_age:
                logger.debug(f"Message {message.id} skipped: too old ({message_age})")
                return False

        # Check for service messages
        if message.action:
            logger.debug(f"Message {message.id} skipped: service message")
            return False

        return True
    
    def extract_contact_info(self, text: str) -> Dict[str, Optional[str]]:
        """
        Extracts contact information from text

        Returns:
            Dictionary with found contacts
        """
        contacts = {
            'telegram': None,
            'email': None,
            'phone': None
        }

        # Search for email FIRST (to avoid matching @gmail from emails)
        email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if email_match:
            contacts['email'] = email_match.group(0)

        # Remove emails from text before searching for Telegram usernames
        text_without_emails = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '', text)

        # Search for Telegram username (must NOT be followed by a dot - that would be email domain)
        # Also exclude common email domains
        email_domains = {'gmail', 'mail', 'yandex', 'yahoo', 'outlook', 'hotmail', 'icloud', 'proton', 'rambler'}
        telegram_match = re.search(r'@([a-zA-Z][a-zA-Z0-9_]{4,31})', text_without_emails)
        if telegram_match:
            username = telegram_match.group(1).lower()
            # Skip if it looks like an email domain
            if username not in email_domains:
                contacts['telegram'] = f"@{telegram_match.group(1)}"

        # Search for phone (simplified pattern)
        phone_match = re.search(r'\+?\d[\d\s\-\(\)]{8,}\d', text)
        if phone_match:
            contacts['phone'] = phone_match.group(0)

        return contacts
    
    def extract_payment_info(self, text: str) -> Dict[str, Optional[str]]:
        """
        Extracts payment information from text

        Returns:
            Dictionary with payment information
        """
        payment_info = {
            'amount': None,
            'currency': None,
            'type': None,
            'raw': None
        }

        text_lower = text.lower()

        # Search for salary in rubles
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

        # Search in dollars
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

        # Determine payment type
        if 'проект' in text_lower or 'за проект' in text_lower:
            payment_info['type'] = 'per_project'
        elif 'час' in text_lower or 'в час' in text_lower or '/час' in text_lower:
            payment_info['type'] = 'hourly'
        elif 'день' in text_lower or 'в день' in text_lower or '/день' in text_lower:
            payment_info['type'] = 'daily'
        elif 'месяц' in text_lower or 'в месяц' in text_lower or '/месяц' in text_lower or 'ежемесячно' in text_lower:
            payment_info['type'] = 'monthly'
        elif 'оклад' in text_lower:
            payment_info['type'] = 'salary'

        return payment_info
    
    def extract_keywords(self, text: str) -> List[str]:
        """
        Extracts keywords specific to FB/IG targeting + performance marketing

        Returns:
            List of found keywords
        """
        # NARROW SPECIALIZATION: only Facebook/Instagram targeting + performance
        keywords_db = [
            # Facebook & Instagram
            'facebook', 'fb', 'фейсбук', 'фб',
            'meta', 'мета', 'meta ads',
            'instagram', 'инстаграм', 'инста', 'ig',
            'facebook ads', 'fb ads', 'meta ads',
            'instagram ads', 'ig ads',
            'ads manager', 'менеджер рекламы', 'рекламный кабинет',
            'business manager',

            # Targeting (Russian keywords for matching)
            'таргет', 'таргетолог', 'таргетолога', 'таргетологи',
            'таргетинг', 'таргетированная реклама',
            'таргетолога', 'таргетированной рекламы',

            # Performance Marketing
            'performance', 'перформанс',
            'performance marketing', 'перформанс маркетинг',
            'performance маркетолог',

            # FB/IG Metrics
            'cpa', 'cpc', 'cpm', 'ctr', 'roas', 'roi',
            'конверсия', 'конверсии', 'conversion',
            'cpv', 'cpl', 'cpi',

            # Targeting Processes
            'кампания', 'рекламная кампания', 'campaign',
            'креатив', 'креативы', 'creative',
            'аудитория', 'аудитории', 'audience',
            'сегмент', 'сегментация', 'lookalike',
            'пиксель', 'pixel', 'facebook pixel',
            'ретаргетинг', 'ретаргет', 'retargeting',

            # Lead Generation
            'лиды', 'лид', 'lead', 'leads',
            'лидогенерация', 'лидген', 'lead generation',
            'лид-формы', 'lead forms',

            # Optimization
            'масштабирование', 'scale', 'scaling',
            'оптимизация', 'optimization',
            'тестирование', 'a/b тест', 'сплит-тест',

            # Platforms (FB/IG ecosystem only)
            'whatsapp', 'messenger', 'мессенджер',
        ]

        text_lower = text.lower()
        found_keywords = []

        for keyword in keywords_db:
            # Use word boundaries for exact matching
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text_lower, re.IGNORECASE):
                found_keywords.append(keyword)

        return list(set(found_keywords))  # Remove duplicates
    
    def get_message_link(self, message: Message, chat) -> str:
        """
        Generates a link to the message

        Returns:
            URL link to the message
        """
        try:
            # For public channels and groups
            if hasattr(chat, 'username') and chat.username:
                return f"https://t.me/{chat.username}/{message.id}"

            # For private chats use format with chat_id
            # This doesn't always work, but better than nothing
            chat_id = str(chat.id).replace('-100', '')
            return f"https://t.me/c/{chat_id}/{message.id}"

        except Exception as e:
            logger.error(f"Error generating message link: {e}")
            return f"Chat ID: {chat.id}, Message ID: {message.id}"
    
    def get_sender_info(self, message: Message) -> Dict[str, Optional[str]]:
        """
        Extracts sender information

        Returns:
            Dictionary with sender information
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


# Global processor instance
message_processor = MessageProcessor()

