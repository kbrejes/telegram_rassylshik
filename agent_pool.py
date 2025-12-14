"""
Agent Pool Management for handling multiple Telegram agents with load balancing
"""
import asyncio
import time
import logging
from typing import List, Optional, Dict, Union
from agent_account import AgentAccount
from config_manager import AgentConfig

logger = logging.getLogger(__name__)


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
        Инициализация и подключение всех агентов
        
        Returns:
            True если хотя бы один агент подключился успешно
        """
        logger.info(f"Инициализация пула из {len(self.agent_configs)} агентов...")
        
        connected_count = 0
        for i, config in enumerate(self.agent_configs):
            try:
                agent = AgentAccount(
                    session_name=config.session_name,
                    phone=config.phone
                )
                
                if await agent.connect():
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
                logger.warning(f"Попытка {attempt + 1}/{max_retries}: нет доступных агентов")
                if attempt < max_retries - 1:
                    # Ждем немного перед следующей попыткой
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
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
            
            # Если не удалось - пробуем следующего агента
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
        
        logger.error(f"Не удалось отправить сообщение после {max_retries} попыток")
        return False
    
    def get_status(self) -> Dict[str, any]:
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
        """Отключить всех агентов"""
        logger.info("Отключение всех агентов в пуле...")
        
        for agent in self.agents:
            try:
                await agent.disconnect()
            except Exception as e:
                logger.error(f"Ошибка отключения агента {agent.session_name}: {e}")
        
        self.agents.clear()
        self._is_initialized = False
        logger.info("Все агенты отключены")
    
    def __len__(self) -> int:
        """Количество подключенных агентов"""
        return len(self.agents)
    
    def __bool__(self) -> bool:
        """Есть ли подключенные агенты"""
        return len(self.agents) > 0
