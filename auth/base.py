"""
Базовый класс для аутентификации Telegram клиентов
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict
from telethon import TelegramClient, errors
from telethon.sessions import SQLiteSession
from config import config
import logging
import sqlite3

logger = logging.getLogger(__name__)


class TimeoutSQLiteSession(SQLiteSession):
    """SQLite сессия с таймаутом для избежания database is locked"""

    def _connect(self):
        # Добавляем таймаут 30 секунд для ожидания разблокировки БД
        self._conn = sqlite3.connect(
            self.filename,
            timeout=30.0,
            check_same_thread=False
        )
        self._conn.isolation_level = None
        self._conn.execute('pragma journal_mode=wal')  # WAL режим для конкурентного доступа


class TelegramAuthManager(ABC):
    """Базовый класс для управления аутентификацией Telegram"""

    def __init__(self):
        self.api_id = config.API_ID
        self.api_hash = config.API_HASH

    @abstractmethod
    def get_session_path(self, identifier: Optional[str] = None) -> str:
        """Возвращает путь к файлу сессии"""
        pass

    @abstractmethod
    def get_pending_client(self, identifier: Optional[str] = None) -> Optional[TelegramClient]:
        """Возвращает pending клиент для данного идентификатора"""
        pass

    @abstractmethod
    def set_pending_client(self, client: TelegramClient, identifier: Optional[str] = None) -> None:
        """Сохраняет pending клиент"""
        pass

    @abstractmethod
    def get_phone_data(self, identifier: Optional[str] = None) -> tuple:
        """Возвращает (phone, phone_code_hash) для идентификатора"""
        pass

    @abstractmethod
    def set_phone_data(self, phone: str, phone_code_hash: str, identifier: Optional[str] = None) -> None:
        """Сохраняет phone данные"""
        pass

    @abstractmethod
    def clear_pending_data(self, identifier: Optional[str] = None) -> None:
        """Очищает pending данные"""
        pass

    def _format_user_info(self, me, include_phone: bool = False) -> dict:
        """Форматирует информацию о пользователе"""
        user_info = {
            "name": f"{me.first_name} {me.last_name or ''}".strip(),
            "username": me.username or "no_username",
            "id": me.id
        }
        if include_phone and hasattr(me, 'phone'):
            user_info["phone"] = me.phone
        return user_info

    def _create_client(self, session_path: str) -> TelegramClient:
        """Создает клиент с таймаутом для SQLite"""
        session = TimeoutSQLiteSession(session_path)
        return TelegramClient(session, self.api_id, self.api_hash)

    async def _check_existing_session(self, session_path: str, include_phone: bool = False) -> Optional[dict]:
        """Проверяет существующую сессию и возвращает user_info если авторизована"""
        if not Path(f"{session_path}.session").exists():
            return None

        client = self._create_client(session_path)
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            return {
                "success": True,
                "needs_code": False,
                "already_authenticated": True,
                "user_info": self._format_user_info(me, include_phone),
                "message": "Уже авторизован"
            }

        await client.disconnect()
        return None

    async def init_auth(self, phone: str, identifier: Optional[str] = None) -> dict:
        """
        Инициирует процесс аутентификации

        Args:
            phone: Номер телефона
            identifier: Идентификатор сессии (для агентов - session_name)

        Returns:
            dict с результатом
        """
        try:
            session_path = self.get_session_path(identifier)

            # Проверяем существующую сессию
            existing = await self._check_existing_session(session_path, include_phone=identifier is None)
            if existing:
                return existing

            # Создаем нового клиента для аутентификации
            client = self._create_client(session_path)
            await client.connect()

            # Отправляем код
            logger.info(f"Отправка кода на номер {phone}")
            result = await client.send_code_request(phone)

            # Сохраняем данные
            self.set_pending_client(client, identifier)
            self.set_phone_data(phone, result.phone_code_hash, identifier)

            logger.info(f"Код успешно отправлен на {phone}")

            return {
                "success": True,
                "needs_code": True,
                "phone_code_hash": result.phone_code_hash,
                "message": f"Код отправлен на {phone}"
            }

        except errors.FloodWaitError as e:
            logger.error(f"FloodWait: необходимо подождать {e.seconds} секунд")
            return {
                "success": False,
                "flood_wait": e.seconds,
                "message": f"Слишком много запросов. Подождите {e.seconds} секунд (~{e.seconds // 3600}ч {(e.seconds % 3600) // 60}м)."
            }
        except Exception as e:
            logger.error(f"Ошибка инициализации аутентификации: {e}")
            return {
                "success": False,
                "message": f"Ошибка: {str(e)}"
            }

    async def verify_code(self, code: str, identifier: Optional[str] = None) -> dict:
        """
        Проверяет код подтверждения

        Args:
            code: Код подтверждения
            identifier: Идентификатор сессии

        Returns:
            dict с результатом
        """
        try:
            client = self.get_pending_client(identifier)
            if not client:
                return {
                    "success": False,
                    "message": "Сессия не найдена. Начните процесс заново."
                }

            phone, phone_code_hash = self.get_phone_data(identifier)
            if not phone or not phone_code_hash:
                return {
                    "success": False,
                    "message": "Данные сессии утеряны. Начните процесс заново."
                }

            logger.info(f"Проверка кода")

            # Подтверждаем код
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)

            # Получаем информацию о пользователе
            me = await client.get_me()
            user_info = self._format_user_info(me, include_phone=identifier is None)

            logger.info(f"Аутентификация успешна: {user_info['name']} (@{user_info['username']})")

            # Отключаемся (сессия уже сохранена)
            await client.disconnect()

            # Очищаем временные данные
            self.clear_pending_data(identifier)

            return {
                "success": True,
                "authenticated": True,
                "user_info": user_info,
                "message": "Аутентификация успешна!"
            }

        except errors.PhoneCodeInvalidError:
            logger.warning("Неверный код")
            return {
                "success": False,
                "message": "Неверный код подтверждения"
            }
        except errors.PhoneCodeExpiredError:
            logger.warning("Код истек")
            await self.cleanup(identifier)
            return {
                "success": False,
                "message": "Код истек. Начните процесс заново."
            }
        except errors.SessionPasswordNeededError:
            logger.warning("Требуется 2FA пароль")
            return {
                "success": True,
                "needs_password": True,
                "authenticated": False,
                "message": "Требуется пароль двухфакторной аутентификации"
            }
        except Exception as e:
            logger.error(f"Ошибка проверки кода: {e}")
            return {
                "success": False,
                "message": f"Ошибка: {str(e)}"
            }

    async def verify_password(self, password: str, identifier: Optional[str] = None) -> dict:
        """
        Проверяет 2FA пароль

        Args:
            password: 2FA пароль
            identifier: Идентификатор сессии

        Returns:
            dict с результатом
        """
        try:
            client = self.get_pending_client(identifier)
            if not client:
                return {
                    "success": False,
                    "message": "Сессия не найдена. Начните процесс заново."
                }

            logger.info("Проверка 2FA пароля")

            # Вводим пароль
            await client.sign_in(password=password)

            # Получаем информацию о пользователе
            me = await client.get_me()
            user_info = self._format_user_info(me, include_phone=identifier is None)

            logger.info(f"2FA аутентификация успешна: {user_info['name']} (@{user_info['username']})")

            # Отключаемся (сессия уже сохранена)
            await client.disconnect()

            # Очищаем временные данные
            self.clear_pending_data(identifier)

            return {
                "success": True,
                "authenticated": True,
                "user_info": user_info,
                "message": "Аутентификация успешна!"
            }

        except errors.PasswordHashInvalidError:
            logger.warning("Неверный 2FA пароль")
            return {
                "success": False,
                "message": "Неверный пароль"
            }
        except Exception as e:
            logger.error(f"Ошибка проверки 2FA пароля: {e}")
            return {
                "success": False,
                "message": f"Ошибка: {str(e)}"
            }

    async def check_session_status(self, identifier: Optional[str] = None) -> dict:
        """Проверяет статус существующей сессии"""
        try:
            session_path = self.get_session_path(identifier)

            if not Path(f"{session_path}.session").exists():
                return {
                    "exists": False,
                    "authenticated": False
                }

            client = self._create_client(session_path)
            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()

                return {
                    "exists": True,
                    "authenticated": True,
                    "user_info": self._format_user_info(me, include_phone=identifier is None)
                }

            await client.disconnect()
            return {
                "exists": True,
                "authenticated": False
            }

        except Exception as e:
            logger.error(f"Ошибка проверки сессии: {e}")
            return {
                "exists": False,
                "authenticated": False,
                "error": str(e)
            }

    async def cleanup(self, identifier: Optional[str] = None):
        """Очищает незавершенную аутентификацию"""
        client = self.get_pending_client(identifier)
        if client:
            try:
                await client.disconnect()
            except:
                pass
        self.clear_pending_data(identifier)
