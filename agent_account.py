"""
Agent account management for Telegram user accounts
Адаптировано из crm_response_bot для job_notification_bot
"""
import asyncio
import logging
from typing import Optional, Union
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.tl.types import User
from config import config
from utils.retry import FloodWaitTracker, format_wait_time

logger = logging.getLogger(__name__)


class AgentAccount:
    """Представляет Telegram аккаунт агента для автоответов"""
    
    def __init__(
        self,
        session_name: str,
        phone: Optional[str] = None
    ):
        """
        Инициализация агента
        
        Args:
            session_name: Имя файла сессии
            phone: Номер телефона (нужен для первого входа)
        """
        # Используем директорию sessions/ если существует
        sessions_dir = Path("sessions")
        if sessions_dir.exists():
            self.session_name = f"sessions/{session_name}"
        else:
            self.session_name = session_name
        
        self.phone = phone
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._flood_tracker = FloodWaitTracker()
    
    async def connect(self) -> bool:
        """
        Подключение к Telegram
        
        Returns:
            True если подключение успешно
        """
        try:
            self.client = TelegramClient(
                self.session_name,
                config.API_ID,
                config.API_HASH
            )
            
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                if not self.phone:
                    logger.error(f"Агент {self.session_name}: Требуется номер телефона для первого входа")
                    return False
                
                logger.info(f"Агент {self.session_name}: Начинается аутентификация...")
                await self.client.send_code_request(self.phone)
                logger.info(f"Агент {self.session_name}: Код отправлен на {self.phone}")
                
                # Запросит код в терминале
                await self.client.start(phone=self.phone)
            
            self._is_connected = True
            me = await self.client.get_me()
            username = f"@{me.username}" if me.username else "без username"
            logger.info(f"Агент {self.session_name} подключен: {me.first_name} ({username})")

            # Важно: запросить updates чтобы получать сообщения
            try:
                await self.client.catch_up()
                logger.debug(f"Агент {self.session_name}: catch_up выполнен")
            except Exception as e:
                logger.warning(f"Агент {self.session_name}: catch_up ошибка: {e}")

            return True
            
        except Exception as e:
            logger.error(f"Агент {self.session_name}: Ошибка подключения: {e}")
            self._is_connected = False
            return False
    
    async def disconnect(self) -> None:
        """Отключение от Telegram"""
        if self.client:
            await self.client.disconnect()
            self._is_connected = False
            logger.info(f"Агент {self.session_name} отключен")
    
    async def send_message(
        self,
        user: Union[str, int, User],
        text: str
    ) -> bool:
        """
        Отправка сообщения пользователю
        
        Args:
            user: Username (с или без @), user ID, или User объект
            text: Текст сообщения
            
        Returns:
            True если сообщение отправлено успешно
        """
        if not self._is_connected or not self.client:
            logger.error(f"Агент {self.session_name}: Не подключен")
            return False
        
        if not self.is_available():
            logger.warning(f"Агент {self.session_name}: Недоступен (FloodWait)")
            return False
        
        try:
            # Нормализуем username
            if isinstance(user, str) and not user.startswith('@'):
                user = f"@{user}"
            
            await self.client.send_message(user, text)
            logger.info(f"Агент {self.session_name}: Сообщение отправлено {user}")
            return True
            
        except errors.FloodWaitError as e:
            logger.warning(f"Агент {self.session_name}: FloodWait {e.seconds} секунд")
            self.handle_flood_wait(e.seconds)
            return False
            
        except errors.UserIsBlockedError:
            logger.error(f"Агент {self.session_name}: Пользователь {user} заблокировал аккаунт")
            return False
            
        except errors.UserPrivacyRestrictedError:
            logger.error(f"Агент {self.session_name}: Нельзя написать {user} из-за настроек приватности")
            return False
            
        except Exception as e:
            logger.error(f"Агент {self.session_name}: Ошибка отправки {user}: {e}")
            return False
    
    @property
    def flood_wait_until(self) -> Optional[float]:
        """Время до которого действует FloodWait (для совместимости с AgentPool)"""
        return self._flood_tracker.flood_wait_until

    def is_available(self) -> bool:
        """
        Проверка доступности агента для отправки сообщений

        Returns:
            True если агент не в FloodWait и подключен
        """
        if not self._is_connected:
            return False
        return not self._flood_tracker.is_blocked

    def handle_flood_wait(self, seconds: int) -> None:
        """
        Обработка FloodWait ошибки

        Args:
            seconds: Количество секунд ожидания
        """
        self._flood_tracker.set_flood_wait(seconds)
        logger.warning(
            f"Агент {self.session_name}: Недоступен {format_wait_time(seconds)}"
        )
    
    async def get_me(self):
        """Получить информацию о текущем пользователе"""
        if not self._is_connected or not self.client:
            return None
        return await self.client.get_me()

    async def health_check(self) -> bool:
        """
        Проверяет, что сессия агента валидна и работает

        Returns:
            True если агент подключен и авторизован
        """
        if not self._is_connected or not self.client:
            return False
        try:
            await self.client.get_me()
            return True
        except Exception as e:
            logger.warning(f"Агент {self.session_name}: health check failed: {e}")
            self._is_connected = False
            return False

    def get_remaining_flood_wait(self) -> int:
        """Возвращает оставшееся время FloodWait в секундах"""
        return self._flood_tracker.remaining_seconds

