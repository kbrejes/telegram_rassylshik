"""
Модуль для интерактивной аутентификации основного бота через веб-интерфейс
"""
from typing import Optional
from telethon import TelegramClient
from .base import TelegramAuthManager
from session_config import get_bot_session_path
import logging

logger = logging.getLogger(__name__)


class BotAuthManager(TelegramAuthManager):
    """Управление процессом аутентификации основного бота"""

    def __init__(self):
        super().__init__()
        self._pending_client: Optional[TelegramClient] = None
        self._phone_code_hash: Optional[str] = None
        self._phone: Optional[str] = None

    def get_session_path(self, identifier: Optional[str] = None) -> str:
        """Возвращает абсолютный путь к сессии основного бота"""
        return get_bot_session_path()

    def get_pending_client(self, identifier: Optional[str] = None) -> Optional[TelegramClient]:
        """Возвращает pending клиент"""
        return self._pending_client

    def set_pending_client(self, client: TelegramClient, identifier: Optional[str] = None) -> None:
        """Сохраняет pending клиент"""
        self._pending_client = client

    def get_phone_data(self, identifier: Optional[str] = None) -> tuple:
        """Возвращает (phone, phone_code_hash)"""
        return self._phone, self._phone_code_hash

    def set_phone_data(self, phone: str, phone_code_hash: str, identifier: Optional[str] = None) -> None:
        """Сохраняет phone данные"""
        self._phone = phone
        self._phone_code_hash = phone_code_hash

    def clear_pending_data(self, identifier: Optional[str] = None) -> None:
        """Очищает pending данные"""
        self._pending_client = None
        self._phone_code_hash = None
        self._phone = None

    # Методы-обертки для совместимости с существующим API
    async def init_auth(self, phone: str) -> dict:
        """Инициирует аутентификацию основного бота"""
        return await super().init_auth(phone, identifier=None)

    async def verify_code(self, code: str) -> dict:
        """Проверяет код подтверждения"""
        return await super().verify_code(code, identifier=None)

    async def verify_password(self, password: str) -> dict:
        """Проверяет 2FA пароль"""
        return await super().verify_password(password, identifier=None)

    async def check_session_status(self, quick_check: bool = False) -> dict:
        """Проверяет статус сессии"""
        return await super().check_session_status(identifier=None, quick_check=quick_check)

    async def cleanup(self) -> None:
        """Очищает незавершенную аутентификацию"""
        await super().cleanup(identifier=None)

    async def force_reset_session(self) -> dict:
        """Принудительно сбрасывает сессию бота"""
        return await super().force_reset_session(identifier=None)


# Глобальный экземпляр
bot_auth_manager = BotAuthManager()
