"""
Базовый класс для аутентификации Telegram клиентов
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict
from telethon import TelegramClient, errors
from telethon.sessions import SQLiteSession
from src.config import config
import logging
import sqlite3
import os

logger = logging.getLogger(__name__)


def delete_session_file(session_path: str) -> bool:
    """Safely delete session file(s)"""
    deleted = False
    for ext in ['.session', '.session-journal']:
        file_path = f"{session_path}{ext}"
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Удален файл сессии: {file_path}")
                deleted = True
            except Exception as e:
                logger.error(f"Не удалось удалить {file_path}: {e}")
    return deleted


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
        """
        Проверяет существующую сессию и возвращает user_info если авторизована.
        Если сессия невалидна - удаляет её и возвращает None для создания новой.
        """
        session_file = Path(f"{session_path}.session")
        if not session_file.exists():
            return None

        client = None
        try:
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

            # Сессия существует но не авторизована - удаляем
            await client.disconnect()
            logger.warning(f"Сессия {session_path} существует но не авторизована, удаляем")
            delete_session_file(session_path)
            return None

        except errors.AuthKeyDuplicatedError:
            logger.error(f"AuthKeyDuplicatedError для {session_path} - сессия используется с другого IP")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            delete_session_file(session_path)
            return {
                "success": False,
                "session_conflict": True,
                "message": "Сессия использовалась с другого устройства. Файл сессии удалён. Попробуйте авторизоваться заново."
            }

        except (errors.AuthKeyUnregisteredError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
            logger.error(f"Сессия {session_path} недействительна (аккаунт деактивирован или ключ отозван)")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            delete_session_file(session_path)
            return {
                "success": False,
                "session_invalid": True,
                "message": "Сессия недействительна (аккаунт деактивирован или сессия отозвана). Авторизуйтесь заново."
            }

        except Exception as e:
            error_str = str(e).lower()
            if client:
                try:
                    await client.disconnect()
                except:
                    pass

            # Если база данных повреждена или сессия невалидна - удаляем
            if any(x in error_str for x in ['database', 'corrupt', 'malformed', 'invalid', 'auth']):
                logger.error(f"Сессия {session_path} повреждена: {e}")
                delete_session_file(session_path)
                return {
                    "success": False,
                    "session_corrupted": True,
                    "message": f"Файл сессии повреждён и был удалён. Авторизуйтесь заново."
                }

            # Если database is locked - сессия используется ботом
            if "database is locked" in error_str:
                logger.info(f"Сессия {session_path} заблокирована (используется ботом)")
                return {
                    "success": True,
                    "needs_code": False,
                    "already_authenticated": True,
                    "locked_by_bot": True,
                    "message": "Сессия уже используется ботом"
                }

            logger.error(f"Ошибка проверки сессии {session_path}: {e}")
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
            existing = await self._check_existing_session(session_path, include_phone=True)
            if existing:
                # Если это ошибка сессии - возвращаем её, пользователь должен попробовать снова
                if existing.get("session_conflict") or existing.get("session_invalid") or existing.get("session_corrupted"):
                    return existing
                # Если уже авторизован - возвращаем успех
                if existing.get("already_authenticated"):
                    return existing

            # Очищаем pending данные перед новой попыткой
            self.clear_pending_data(identifier)

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

        except errors.AuthKeyDuplicatedError:
            session_path = self.get_session_path(identifier)
            logger.error(f"AuthKeyDuplicatedError при инициализации {session_path}")
            delete_session_file(session_path)
            return {
                "success": False,
                "session_conflict": True,
                "message": "Сессия использовалась с другого устройства. Попробуйте ещё раз."
            }

        except errors.PhoneNumberInvalidError:
            return {
                "success": False,
                "message": "Неверный формат номера телефона. Используйте международный формат: +1234567890"
            }

        except errors.PhoneNumberBannedError:
            return {
                "success": False,
                "message": "Этот номер телефона заблокирован в Telegram."
            }

        except Exception as e:
            error_str = str(e)
            logger.error(f"Ошибка инициализации аутентификации: {e}")

            # Если ошибка связана с сессией - удаляем файл
            if any(x in error_str.lower() for x in ['auth', 'session', 'key']):
                session_path = self.get_session_path(identifier)
                delete_session_file(session_path)
                return {
                    "success": False,
                    "message": f"Ошибка сессии: {error_str}. Файл сессии удалён, попробуйте ещё раз."
                }

            return {
                "success": False,
                "message": f"Ошибка: {error_str}"
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
            user_info = self._format_user_info(me, include_phone=True)

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
            user_info = self._format_user_info(me, include_phone=True)

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

    async def check_session_status(self, identifier: Optional[str] = None, quick_check: bool = False) -> dict:
        """
        Проверяет статус существующей сессии

        Args:
            identifier: Идентификатор сессии
            quick_check: Если True, только проверяет существование файла без открытия соединения
                        (используется когда бот уже работает, чтобы избежать database is locked)
        """
        try:
            session_path = self.get_session_path(identifier)
            session_file = Path(f"{session_path}.session")

            if not session_file.exists():
                return {
                    "exists": False,
                    "authenticated": False
                }

            # Быстрая проверка - только существование файла
            # Используется когда бот уже работает и держит сессию открытой
            if quick_check:
                return {
                    "exists": True,
                    "authenticated": True,  # Предполагаем что если файл есть, бот авторизован
                    "quick_check": True
                }

            client = self._create_client(session_path)
            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()

                return {
                    "exists": True,
                    "authenticated": True,
                    "user_info": self._format_user_info(me, include_phone=True)
                }

            await client.disconnect()
            return {
                "exists": True,
                "authenticated": False
            }

        except Exception as e:
            # Если database is locked, значит бот уже работает с этой сессией
            if "database is locked" in str(e):
                logger.info(f"Сессия заблокирована (бот работает): {session_path}")
                return {
                    "exists": True,
                    "authenticated": True,
                    "locked_by_bot": True
                }
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
            except Exception:
                pass
        self.clear_pending_data(identifier)

    async def force_reset_session(self, identifier: Optional[str] = None) -> dict:
        """
        Принудительно сбрасывает сессию - удаляет файл и очищает все данные.
        Используйте когда авторизация застряла или сессия невалидна.
        """
        try:
            # Очищаем pending данные
            await self.cleanup(identifier)

            # Удаляем файл сессии
            session_path = self.get_session_path(identifier)
            deleted = delete_session_file(session_path)

            if deleted:
                return {
                    "success": True,
                    "message": "Сессия сброшена. Теперь можете авторизоваться заново."
                }
            else:
                return {
                    "success": True,
                    "message": "Файл сессии не найден (уже удалён). Можете авторизоваться."
                }

        except Exception as e:
            logger.error(f"Ошибка сброса сессии: {e}")
            return {
                "success": False,
                "message": f"Ошибка сброса: {str(e)}"
            }
