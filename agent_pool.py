"""
Agent Pool Management for handling multiple Telegram agents with load balancing
"""
import asyncio
import logging
from typing import List, Optional, Dict, Union, Any
from agent_account import AgentAccount
from config_manager import AgentConfig
from utils.retry import calculate_backoff, format_wait_time

logger = logging.getLogger(__name__)


# Глобальный реестр подключенных агентов (session_name -> AgentAccount)
# Это предотвращает "database is locked" когда один агент используется несколькими каналами
_global_agents: Dict[str, AgentAccount] = {}
_global_agents_lock = asyncio.Lock()


async def get_or_create_agent(session_name: str, phone: str) -> Optional[AgentAccount]:
    """
    Получить агента из глобального реестра или создать нового.
    Это гарантирует что один session файл открывается только одним клиентом.
    """
    async with _global_agents_lock:
        # Если агент уже подключен - возвращаем его
        if session_name in _global_agents:
            agent = _global_agents[session_name]
            if agent._is_connected:
                logger.debug(f"Агент {session_name} уже подключен, переиспользуем")
                return agent
            else:
                # Агент был отключен - удаляем из реестра
                del _global_agents[session_name]

        # Создаём нового агента
        agent = AgentAccount(session_name=session_name, phone=phone)
        try:
            if await agent.connect():
                _global_agents[session_name] = agent
                return agent
            else:
                return None
        except Exception as e:
            # Если ошибка "database is locked" - возможно другой процесс уже подключил
            if "database is locked" in str(e):
                logger.warning(f"Агент {session_name}: database is locked - уже используется")
            else:
                logger.error(f"Агент {session_name}: ошибка подключения: {e}")
            return None


async def disconnect_all_global_agents():
    """Отключить всех агентов в глобальном реестре"""
    async with _global_agents_lock:
        for session_name, agent in list(_global_agents.items()):
            try:
                await agent.disconnect()
            except Exception as e:
                logger.error(f"Ошибка отключения агента {session_name}: {e}")
        _global_agents.clear()
        logger.info("Все глобальные агенты отключены")


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
        logger.info(f"Инициализация пула из {len(self.agent_configs)} агентов...")

        connected_count = 0
        for i, config in enumerate(self.agent_configs):
            try:
                # Используем глобальный реестр вместо создания нового агента
                agent = await get_or_create_agent(config.session_name, config.phone)

                if agent:
                    if agent not in self.agents:
                        self.agents.append(agent)
                    connected_count += 1
                    logger.info(f"  ✅ Агент {i+1}/{len(self.agent_configs)} подключен: {config.session_name}")
                else:
                    logger.error(f"  ❌ Агент {i+1}/{len(self.agent_configs)} не подключился: {config.session_name}")

            except Exception as e:
                logger.error(f"  ❌ Ошибка подключения агента {config.session_name}: {e}")

        self._is_initialized = True
        logger.info(f"📊 Пул инициализирован: {connected_count}/{len(self.agent_configs)} агентов активны")

        return connected_count > 0
    
    def get_available_agent(self) -> Optional[AgentAccount]:
        """
        Получить доступного агента по принципу least-busy
        
        Returns:
            Агент с наименьшим временем flood wait или None если все заняты
        """
        if not self._is_initialized or not self.agents:
            return None
        
        # Фильтруем доступных агентов
        available_agents = [agent for agent in self.agents if agent.is_available()]
        
        if not available_agents:
            logger.warning("Все агенты недоступны (FloodWait)")
            return None
        
        # Выбираем агента с наименьшим временем ожидания
        best_agent = min(available_agents, key=lambda a: a.flood_wait_until or 0)
        
        logger.debug(f"Выбран агент: {best_agent.session_name}")
        return best_agent
    
    async def send_message(
        self,
        user: Union[str, int],
        text: str,
        max_retries: int = 3
    ) -> bool:
        """
        Отправка сообщения через доступного агента с автоматическим переключением

        Args:
            user: Username (с или без @), user ID, или User объект
            text: Текст сообщения
            max_retries: Максимальное количество попыток с разными агентами

        Returns:
            True если сообщение отправлено успешно
        """
        for attempt in range(max_retries):
            agent = self.get_available_agent()

            if not agent:
                delay = calculate_backoff(attempt, base=1.0, max_delay=30.0)
                logger.warning(
                    f"Попытка {attempt + 1}/{max_retries}: нет доступных агентов, "
                    f"ожидание {delay:.1f}с"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                continue

            try:
                success = await agent.send_message(user, text)
                if success:
                    logger.info(f"Сообщение отправлено через агента {agent.session_name}")
                    return True
                else:
                    logger.warning(f"Агент {agent.session_name} не смог отправить сообщение")

            except Exception as e:
                logger.error(f"Ошибка отправки через агента {agent.session_name}: {e}")

            # Если не удалось - пробуем следующего агента с небольшой задержкой
            if attempt < max_retries - 1:
                await asyncio.sleep(1)

        logger.error(f"Не удалось отправить сообщение после {max_retries} попыток")
        return False

    async def periodic_health_check(self, interval: float = 300.0) -> None:
        """
        Фоновая задача для периодической проверки здоровья агентов

        Args:
            interval: Интервал проверки в секундах (по умолчанию 5 минут)
        """
        logger.info(f"Запуск периодической проверки агентов каждые {format_wait_time(int(interval))}")
        while True:
            await asyncio.sleep(interval)

            if not self._is_initialized:
                continue

            unhealthy_count = 0
            for agent in self.agents:
                if not await agent.health_check():
                    unhealthy_count += 1

            if unhealthy_count > 0:
                logger.warning(f"Health check: {unhealthy_count}/{len(self.agents)} агентов недоступны")
    
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
        logger.info(f"Очистка пула агентов ({len(self.agents)} агентов)")
        # Не отключаем агентов - они в глобальном реестре и могут использоваться другими каналами
        self.agents.clear()
        self._is_initialized = False
    
    def __len__(self) -> int:
        """Количество подключенных агентов"""
        return len(self.agents)
    
    def __bool__(self) -> bool:
        """Есть ли подключенные агенты"""
        return len(self.agents) > 0
