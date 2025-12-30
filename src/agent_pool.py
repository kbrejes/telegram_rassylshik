"""
Agent Pool Management for handling multiple Telegram agents with load balancing
"""
import asyncio
import logging
import threading
import time
from typing import List, Optional, Dict, Union, Any
from telethon.tl.types import InputPeerUser
from src.agent_account import AgentAccount
from src.config_manager import AgentConfig
from src.connection_status import status_manager
from src.human_behavior import human_behavior
from src.constants import (
    HEALTH_CHECK_INTERVAL_SECONDS,
    DEFAULT_MAX_RETRIES,
)
from utils.retry import calculate_backoff, format_wait_time

logger = logging.getLogger(__name__)


# Глобальный реестр подключенных агентов (session_name -> AgentAccount)
# Это предотвращает "database is locked" когда один агент используется несколькими каналами
_global_agents: Dict[str, AgentAccount] = {}
_global_agents_lock = asyncio.Lock()

# ID потока, в котором работает основной бот
# Агенты можно подключать только из этого потока
_main_thread_id: Optional[int] = None


def set_main_thread():
    """Установить текущий поток как главный (вызывается при старте бота)"""
    global _main_thread_id
    _main_thread_id = threading.current_thread().ident
    logger.info(f"Main bot thread set: {_main_thread_id}")


def is_main_thread() -> bool:
    """Проверить, находимся ли мы в главном потоке бота"""
    return _main_thread_id is None or threading.current_thread().ident == _main_thread_id


async def get_or_create_agent(session_name: str, phone: str, allow_create: bool = True) -> Optional[AgentAccount]:
    """
    Получить агента из глобального реестра или создать нового.
    Это гарантирует что один session файл открывается только одним клиентом.

    ВАЖНО: Создание новых агентов разрешено только из главного потока бота.
    Из веб-интерфейса можно только получить уже подключенных агентов.

    Args:
        session_name: Имя сессии агента
        phone: Номер телефона (нужен для первого входа)
        allow_create: Разрешить создание нового агента (по умолчанию True)
    """
    current_thread = threading.current_thread().ident

    async with _global_agents_lock:
        # Если агент уже подключен - возвращаем его
        if session_name in _global_agents:
            agent = _global_agents[session_name]
            if agent._is_connected:
                logger.debug(f"Agent {session_name} already connected, reusing")
                return agent
            else:
                # Агент был отключен - удаляем из реестра
                del _global_agents[session_name]

        # Проверяем, можем ли мы создавать нового агента
        if not allow_create:
            logger.warning(f"Agent {session_name}: creation not allowed (allow_create=False)")
            return None

        # Проверяем, находимся ли мы в главном потоке
        if not is_main_thread():
            logger.warning(
                f"Агент {session_name}: попытка создания из не-главного потока "
                f"(текущий: {current_thread}, главный: {_main_thread_id}). "
                f"Агенты должны создаваться только в главном потоке бота."
            )
            return None

        # Создаём нового агента
        agent = AgentAccount(session_name=session_name, phone=phone)
        try:
            if await agent.connect():
                _global_agents[session_name] = agent

                # Update status with user info
                user_info = None
                try:
                    me = await agent.client.get_me()
                    user_info = {
                        "id": me.id,
                        "first_name": me.first_name,
                        "last_name": me.last_name,
                        "username": me.username,
                        "phone": me.phone
                    }
                except Exception:
                    pass

                # Check if agent has active flood_wait - preserve that status
                if agent.flood_wait_until and agent.flood_wait_until > time.time():
                    status_manager.update_agent_status(
                        session_name, "flood_wait", phone,
                        flood_wait_until=agent.flood_wait_until,
                        user_info=user_info
                    )
                else:
                    status_manager.update_agent_status(session_name, "connected", phone, user_info=user_info)

                return agent
            else:
                error_msg = agent.last_connect_error or "Failed to connect"
                status_manager.update_agent_status(session_name, "error", phone, error=error_msg)
                return None
        except Exception as e:
            # Если ошибка "database is locked" - возможно другой процесс уже подключил
            if "database is locked" in str(e):
                logger.warning(f"Agent {session_name}: database is locked - already in use")
                status_manager.update_agent_status(session_name, "error", phone, error="Database locked")
            else:
                logger.error(f"Agent {session_name}: connection error: {e}")
                status_manager.update_agent_status(session_name, "error", phone, error=str(e))
            return None


async def get_existing_agent(session_name: str) -> Optional[AgentAccount]:
    """
    Получить только уже подключенного агента (без создания нового).
    Безопасно для вызова из любого потока.
    """
    async with _global_agents_lock:
        if session_name in _global_agents:
            agent = _global_agents[session_name]
            if agent._is_connected:
                return agent
        return None


async def disconnect_all_global_agents() -> int:
    """Отключить всех агентов в глобальном реестре"""
    count = 0
    async with _global_agents_lock:
        for session_name, agent in list(_global_agents.items()):
            try:
                await agent.disconnect()
                status_manager.update_agent_status(session_name, "disconnected")
                count += 1
            except Exception as e:
                logger.error(f"Error disconnecting agent {session_name}: {e}")
                status_manager.update_agent_status(session_name, "error", error=str(e))
        _global_agents.clear()
        logger.info("All global agents disconnected")
    return count


class AgentPool:
    """Пул агентов с балансировкой нагрузки по принципу least-busy"""
    
    def __init__(self, agent_configs: List[AgentConfig]):
        """
        Инициализация пула агентов
        
        Args:
            agent_configs: Список конфигураций агентов
        """
        self.agent_configs = agent_configs
        self.agents: List[AgentAccount] = []
        self._is_initialized = False
        
    async def initialize(self) -> bool:
        """
        Инициализация и подключение всех агентов.
        Использует глобальный реестр для предотвращения "database is locked".

        Returns:
            True если хотя бы один агент подключился успешно
        """
        logger.info(f"Initializing pool with {len(self.agent_configs)} agents...")

        connected_count = 0
        for i, config in enumerate(self.agent_configs):
            try:
                # Используем глобальный реестр вместо создания нового агента
                agent = await get_or_create_agent(config.session_name, config.phone)

                if agent:
                    if agent not in self.agents:
                        self.agents.append(agent)
                    connected_count += 1
                    logger.info(f"  ✅ Agent {i+1}/{len(self.agent_configs)} connected: {config.session_name}")
                else:
                    logger.error(f"  ❌ Agent {i+1}/{len(self.agent_configs)} failed to connect: {config.session_name}")

            except Exception as e:
                logger.error(f"  ❌ Error connecting agent {config.session_name}: {e}")

        self._is_initialized = True
        logger.info(f"Pool initialized: {connected_count}/{len(self.agent_configs)} agents active")

        return connected_count > 0
    
    def get_available_agent(self, exclude: Optional[List[AgentAccount]] = None) -> Optional[AgentAccount]:
        """
        Получить доступного агента по принципу least-busy

        Args:
            exclude: Список агентов для исключения (уже попробованные)

        Returns:
            Агент с наименьшим временем flood wait или None если все заняты
        """
        if not self._is_initialized or not self.agents:
            return None

        exclude_set = set(exclude) if exclude else set()

        # Фильтруем доступных агентов (исключая уже попробованных)
        available_agents = [
            agent for agent in self.agents
            if agent.is_available() and agent not in exclude_set
        ]

        if not available_agents:
            if exclude_set:
                logger.warning(f"All agents unavailable or already tried ({len(exclude_set)} excluded)")
            else:
                logger.warning("All agents unavailable (FloodWait)")
            return None

        # Выбираем агента с наименьшим временем ожидания
        best_agent = min(available_agents, key=lambda a: a.flood_wait_until or 0)

        logger.debug(f"Selected agent: {best_agent.session_name}")
        return best_agent
    
    async def send_message(
        self,
        user: Union[str, int, InputPeerUser, Any],
        text: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        contact_id: Optional[int] = None,
        simulate_human: bool = True
    ) -> bool:
        """
        Отправка сообщения через доступного агента с автоматическим переключением.

        Args:
            user: Username (с или без @), user ID, User object, or InputPeerUser
            text: Текст сообщения
            max_retries: Максимальное количество попыток с разными агентами
            contact_id: ID контакта для отслеживания поведения (опционально)
            simulate_human: Симулировать человеческое поведение (задержки, typing)

        Returns:
            True если сообщение отправлено успешно
        """
        tried_agents: List[AgentAccount] = []

        for attempt in range(max_retries):
            agent = self.get_available_agent(exclude=tried_agents)

            if not agent:
                delay = calculate_backoff(attempt, base=1.0, max_delay=30.0)
                logger.warning(
                    f"Попытка {attempt + 1}/{max_retries}: нет доступных агентов, "
                    f"ожидание {delay:.1f}с"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                continue

            tried_agents.append(agent)

            try:
                # Simulate human behavior (typing indicator) before first attempt only
                if simulate_human and attempt == 0 and agent.client:
                    await human_behavior.simulate_typing(
                        client=agent.client,
                        contact=user,
                        message_length=len(text)
                    )

                success = await agent.send_message(user, text)
                if success:
                    logger.info(f"Message sent via agent {agent.session_name}")
                    return True
                else:
                    logger.warning(f"Agent {agent.session_name} failed to send, trying next")

            except Exception as e:
                logger.error(f"Error sending via agent {agent.session_name}: {e}")

            # Небольшая задержка перед следующей попыткой
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)

        logger.error(f"Failed to send message after trying {len(tried_agents)} agents")
        return False

    async def periodic_health_check(self, interval: float = HEALTH_CHECK_INTERVAL_SECONDS) -> None:
        """
        Фоновая задача для периодической проверки здоровья агентов
        с автоматическим переподключением.

        Args:
            interval: Интервал проверки в секундах (по умолчанию 5 минут)
        """
        logger.info(f"Starting periodic agent health check every {format_wait_time(int(interval))}")
        while True:
            await asyncio.sleep(interval)

            if not self._is_initialized:
                continue

            unhealthy_count = 0
            reconnected_count = 0

            for i, agent in enumerate(self.agents):
                if not await agent.health_check():
                    unhealthy_count += 1
                    logger.warning(f"Agent {agent.session_name} unavailable, attempting reconnection...")

                    # Попытка переподключения
                    try:
                        # Сначала отключаем
                        try:
                            await agent.disconnect()
                        except Exception:
                            pass

                        # Пробуем подключиться заново
                        if await agent.connect():
                            reconnected_count += 1
                            logger.info(f"Agent {agent.session_name} reconnected successfully")
                        else:
                            logger.error(f"Agent {agent.session_name} failed to reconnect")
                    except Exception as e:
                        logger.error(f"Error reconnecting agent {agent.session_name}: {e}")

            if unhealthy_count > 0:
                logger.warning(
                    f"Health check: {unhealthy_count}/{len(self.agents)} недоступны, "
                    f"{reconnected_count} переподключены"
                )
    
    def get_status(self) -> Dict[str, Any]:
        """
        Получить статус пула агентов
        
        Returns:
            Словарь со статистикой пула
        """
        if not self._is_initialized:
            return {
                'initialized': False,
                'total_agents': len(self.agent_configs),
                'connected_agents': 0,
                'available_agents': 0
            }
        
        available_count = len([agent for agent in self.agents if agent.is_available()])
        
        agents_status = []
        for agent in self.agents:
            status = {
                'session_name': agent.session_name,
                'connected': agent._is_connected,
                'available': agent.is_available(),
                'flood_wait_until': agent.flood_wait_until
            }
            agents_status.append(status)
        
        return {
            'initialized': True,
            'total_agents': len(self.agent_configs),
            'connected_agents': len(self.agents),
            'available_agents': available_count,
            'agents': agents_status
        }
    
    async def disconnect_all(self):
        """
        Очистить локальный пул агентов.
        НЕ отключает агентов т.к. они могут использоваться другими каналами.
        Для полного отключения используйте disconnect_all_global_agents().
        """
        logger.info(f"Clearing agent pool ({len(self.agents)} agents)")
        # Не отключаем агентов - они в глобальном реестре и могут использоваться другими каналами
        self.agents.clear()
        self._is_initialized = False
    
    def __len__(self) -> int:
        """Количество подключенных агентов"""
        return len(self.agents)
    
    def __bool__(self) -> bool:
        """Есть ли подключенные агенты"""
        return len(self.agents) > 0
