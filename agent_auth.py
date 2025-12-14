"""
Модуль для интерактивной аутентификации агентов через веб-интерфейс
"""
import asyncio
from pathlib import Path
from typing import Optional, Dict
from telethon import TelegramClient, errors
from config import config
import logging

logger = logging.getLogger(__name__)


class AgentAuthManager:
    """Управление процессом аутентификации агентов"""
    
    def __init__(self):
        self.pending_auths: Dict[str, TelegramClient] = {}
        self.phone_code_hash: Dict[str, str] = {}
        self.phones: Dict[str, str] = {}
    
    async def init_auth(self, phone: str, session_name: str) -> dict:
        """
        Инициирует процесс аутентификации
        
        Returns:
            {
                "success": bool,
                "needs_code": bool,
                "phone_code_hash": str,
                "message": str
            }
        """
        try:
            sessions_dir = Path("sessions")
            sessions_dir.mkdir(exist_ok=True)
            session_path = f"sessions/{session_name}"
            
            # Проверяем, существует ли уже сессия
            if Path(f"{session_path}.session").exists():
                # Пробуем подключиться с существующей сессией
                client = TelegramClient(session_path, config.API_ID, config.API_HASH)
                await client.connect()
                
                if await client.is_user_authorized():
                    me = await client.get_me()
                    await client.disconnect()
                    return {
                        "success": True,
                        "needs_code": False,
                        "already_authenticated": True,
                        "user_info": {
                            "name": f"{me.first_name} {me.last_name or ''}".strip(),
                            "username": me.username or "no_username",
                            "id": me.id
                        },
                        "message": "Агент уже авторизован"
                    }
                
                await client.disconnect()
            
            # Создаем нового клиента для аутентификации
            client = TelegramClient(session_path, config.API_ID, config.API_HASH)
            await client.connect()
            
            # Отправляем код
            logger.info(f"Отправка кода на номер {phone}")
            result = await client.send_code_request(phone)
            
            # Сохраняем клиента и phone_code_hash для следующего шага
            self.pending_auths[session_name] = client
            self.phone_code_hash[session_name] = result.phone_code_hash
            self.phones[session_name] = phone
            
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
                "message": f"Слишком много запросов. Подождите {e.seconds} секунд."
            }
        except Exception as e:
            logger.error(f"Ошибка инициализации аутентификации: {e}")
            return {
                "success": False,
                "message": f"Ошибка: {str(e)}"
            }
    
    async def verify_code(self, session_name: str, code: str) -> dict:
        """
        Проверяет код подтверждения и завершает аутентификацию
        
        Returns:
            {
                "success": bool,
                "authenticated": bool,
                "user_info": dict,
                "message": str
            }
        """
        try:
            client = self.pending_auths.get(session_name)
            if not client:
                return {
                    "success": False,
                    "message": "Сессия не найдена. Начните процесс заново."
                }
            
            phone = self.phones.get(session_name)
            phone_code_hash = self.phone_code_hash.get(session_name)
            
            if not phone or not phone_code_hash:
                return {
                    "success": False,
                    "message": "Данные сессии утеряны. Начните процесс заново."
                }
            
            logger.info(f"Проверка кода для сессии {session_name}")
            
            # Подтверждаем код
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            
            # Получаем информацию о пользователе
            me = await client.get_me()
            
            user_info = {
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username or "no_username",
                "id": me.id
            }
            
            logger.info(f"Аутентификация успешна: {user_info['name']} (@{user_info['username']})")
            
            # Отключаемся (сессия уже сохранена)
            await client.disconnect()
            
            # Очищаем временные данные
            del self.pending_auths[session_name]
            del self.phone_code_hash[session_name]
            del self.phones[session_name]
            
            return {
                "success": True,
                "authenticated": True,
                "user_info": user_info,
                "message": "Аутентификация успешна!"
            }
            
        except errors.PhoneCodeInvalidError:
            logger.warning(f"Неверный код для сессии {session_name}")
            return {
                "success": False,
                "message": "Неверный код подтверждения"
            }
        except errors.PhoneCodeExpiredError:
            logger.warning(f"Код истек для сессии {session_name}")
            # Очищаем сессию
            if session_name in self.pending_auths:
                await self.pending_auths[session_name].disconnect()
                del self.pending_auths[session_name]
                if session_name in self.phone_code_hash:
                    del self.phone_code_hash[session_name]
                if session_name in self.phones:
                    del self.phones[session_name]
            
            return {
                "success": False,
                "message": "Код истек. Начните процесс заново."
            }
        except errors.SessionPasswordNeededError:
            # Требуется 2FA пароль - НЕ ОЧИЩАЕМ сессию, ждём пароль
            logger.warning(f"Требуется 2FA пароль для сессии {session_name}")
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
    
    async def verify_password(self, session_name: str, password: str) -> dict:
        """
        Проверяет 2FA пароль и завершает аутентификацию
        
        Returns:
            {
                "success": bool,
                "authenticated": bool,
                "user_info": dict,
                "message": str
            }
        """
        try:
            client = self.pending_auths.get(session_name)
            if not client:
                return {
                    "success": False,
                    "message": "Сессия не найдена. Начните процесс заново."
                }
            
            logger.info(f"Проверка 2FA пароля для сессии {session_name}")
            
            # Вводим пароль
            await client.sign_in(password=password)
            
            # Получаем информацию о пользователе
            me = await client.get_me()
            
            user_info = {
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username or "no_username",
                "id": me.id
            }
            
            logger.info(f"2FA аутентификация успешна: {user_info['name']} (@{user_info['username']})")
            
            # Отключаемся (сессия уже сохранена)
            await client.disconnect()
            
            # Очищаем временные данные
            del self.pending_auths[session_name]
            if session_name in self.phone_code_hash:
                del self.phone_code_hash[session_name]
            if session_name in self.phones:
                del self.phones[session_name]
            
            return {
                "success": True,
                "authenticated": True,
                "user_info": user_info,
                "message": "Аутентификация успешна!"
            }
            
        except errors.PasswordHashInvalidError:
            logger.warning(f"Неверный 2FA пароль для сессии {session_name}")
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
    
    async def check_session_status(self, session_name: str) -> dict:
        """Проверяет статус существующей сессии"""
        try:
            sessions_dir = Path("sessions")
            session_path = f"sessions/{session_name}"
            
            if not Path(f"{session_path}.session").exists():
                return {
                    "exists": False,
                    "authenticated": False
                }
            
            client = TelegramClient(session_path, config.API_ID, config.API_HASH)
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()
                
                return {
                    "exists": True,
                    "authenticated": True,
                    "user_info": {
                        "name": f"{me.first_name} {me.last_name or ''}".strip(),
                        "username": me.username or "no_username",
                        "id": me.id
                    }
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
    
    async def cleanup_pending(self, session_name: str):
        """Очищает незавершенную аутентификацию"""
        if session_name in self.pending_auths:
            try:
                await self.pending_auths[session_name].disconnect()
            except:
                pass
            del self.pending_auths[session_name]
        
        if session_name in self.phone_code_hash:
            del self.phone_code_hash[session_name]
        
        if session_name in self.phones:
            del self.phones[session_name]


# Глобальный экземпляр
agent_auth_manager = AgentAuthManager()

