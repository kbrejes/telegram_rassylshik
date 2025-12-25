"""
Agent account management for Telegram user accounts
Адаптировано из crm_response_bot для job_notification_bot
"""
import asyncio
import logging
import time
from typing import Optional, Union
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.tl.types import User
from src.config import config
from utils.retry import FloodWaitTracker, format_wait_time
from src.session_config import get_agent_session_path, delete_session_file
from auth.base import TimeoutSQLiteSession
from src.connection_status import status_manager

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
            session_name: Имя файла сессии (без пути, только имя)
            phone: Номер телефона (нужен для первого входа)
        """
        # Используем абсолютный путь из session_config
        self.session_name = get_agent_session_path(session_name)
        self.phone = phone
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._flood_tracker = FloodWaitTracker()
        # Храним event loop в котором был подключен клиент
        self._connected_loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def connect(self) -> bool:
        """
        Подключение к Telegram

        Returns:
            True если подключение успешно
        """
        try:
            # Используем TimeoutSQLiteSession для избежания "database is locked"
            session = TimeoutSQLiteSession(self.session_name)
            self.client = TelegramClient(
                session,
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
            # Сохраняем event loop в котором подключились
            self._connected_loop = asyncio.get_running_loop()
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

        except errors.AuthKeyDuplicatedError:
            # Сессия используется с другого IP - нужно пересоздать
            logger.error(f"Агент {self.session_name}: AuthKeyDuplicatedError - сессия повреждена, удаляем")
            delete_session_file(self.session_name)
            self._is_connected = False
            return False

        except Exception as e:
            error_str = str(e).lower()
            if "database is locked" in error_str:
                logger.warning(f"Агент {self.session_name}: Сессия заблокирована другим процессом")
            else:
                logger.error(f"Агент {self.session_name}: Ошибка подключения: {e}")
            self._is_connected = False
            return False
    
    async def disconnect(self) -> None:
        """Отключение от Telegram"""
        if self.client:
            await self.client.disconnect()
            self._is_connected = False
            self._connected_loop = None
            logger.info(f"Агент {self.session_name} отключен")

    def is_valid_loop(self) -> bool:
        """
        Проверяет, что мы в том же event loop где был подключен клиент.

        ВАЖНО: Не пытается переподключиться! Переподключение из другого потока
        сломает агента для основного потока бота.

        Returns:
            True если текущий loop совпадает с loop подключения
        """
        if not self._is_connected or not self.client:
            return False

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return False

        if self._connected_loop is current_loop:
            return True

        # Loop изменился - это означает вызов из неправильного потока
        logger.error(
            f"Агент {self.session_name}: Попытка использования из неправильного event loop! "
            f"Агенты из agent_pool можно использовать только из потока бота."
        )
        return False

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

        # Проверяем что мы в правильном event loop
        if not self.is_valid_loop():
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
        flood_wait_until = time.time() + seconds

        # Update status with flood wait info
        # Extract just the session name from full path for status update
        session_name_only = Path(self.session_name).name
        status_manager.update_agent_status(
            session_name_only,
            "flood_wait",
            self.phone or "",
            flood_wait_until=flood_wait_until
        )

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

