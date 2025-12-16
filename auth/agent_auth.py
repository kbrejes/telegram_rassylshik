"""
Модуль для интерактивной аутентификации агентов через веб-интерфейс
"""
from pathlib import Path
from typing import Optional, Dict
from telethon import TelegramClient
from .base import TelegramAuthManager
import logging

logger = logging.getLogger(__name__)


class AgentAuthManager(TelegramAuthManager):
    """Управление процессом аутентификации агентов"""

    def __init__(self):
        super().__init__()
        self._pending_auths: Dict[str, TelegramClient] = {}
        self._phone_code_hash: Dict[str, str] = {}
        self._phones: Dict[str, str] = {}

    def get_session_path(self, identifier: Optional[str] = None) -> str:
        """Возвращает путь к сессии агента"""
        if not identifier:
            raise ValueError("session_name (identifier) is required for agent auth")

        sessions_dir = Path("sessions")
        sessions_dir.mkdir(exist_ok=True)
        return f"sessions/{identifier}"

    def get_pending_client(self, identifier: Optional[str] = None) -> Optional[TelegramClient]:
        """Возвращает pending клиент для агента"""
        if not identifier:
            return None
        return self._pending_auths.get(identifier)

    def set_pending_client(self, client: TelegramClient, identifier: Optional[str] = None) -> None:
        """Сохраняет pending клиент"""
        if identifier:
            self._pending_auths[identifier] = client

    def get_phone_data(self, identifier: Optional[str] = None) -> tuple:
        """Возвращает (phone, phone_code_hash) для агента"""
        if not identifier:
            return None, None
        return self._phones.get(identifier), self._phone_code_hash.get(identifier)

    def set_phone_data(self, phone: str, phone_code_hash: str, identifier: Optional[str] = None) -> None:
        """Сохраняет phone данные"""
        if identifier:
            self._phones[identifier] = phone
            self._phone_code_hash[identifier] = phone_code_hash

    def clear_pending_data(self, identifier: Optional[str] = None) -> None:
        """Очищает pending данные для агента"""
        if identifier:
            self._pending_auths.pop(identifier, None)
            self._phone_code_hash.pop(identifier, None)
            self._phones.pop(identifier, None)

    # Методы-обертки для совместимости с существующим API
    async def init_auth(self, phone: str, session_name: str) -> dict:
        """Инициирует аутентификацию агента"""
        return await super().init_auth(phone, identifier=session_name)

    async def verify_code(self, session_name: str, code: str) -> dict:
        """Проверяет код подтверждения"""
        return await super().verify_code(code, identifier=session_name)

    async def verify_password(self, session_name: str, password: str) -> dict:
        """Проверяет 2FA пароль"""
        return await super().verify_password(password, identifier=session_name)

    async def check_session_status(self, session_name: str) -> dict:
        """Проверяет статус сессии агента"""
        return await super().check_session_status(identifier=session_name)

    async def cleanup_pending(self, session_name: str) -> None:
        """Очищает незавершенную аутентификацию агента"""
        await super().cleanup(identifier=session_name)


# Глобальный экземпляр
agent_auth_manager = AgentAuthManager()
