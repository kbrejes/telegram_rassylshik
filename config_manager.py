"""
Менеджер конфигурации для управления несколькими каналами уведомлений
"""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Конфигурация агента для CRM"""
    phone: str
    session_name: str
    
    def to_dict(self) -> dict:
        """Конвертация в словарь"""
        return {
            'phone': self.phone,
            'session_name': self.session_name
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AgentConfig':
        """Создание из словаря"""
        return cls(
            phone=data['phone'],
            session_name=data['session_name']
        )


@dataclass
class FilterConfig:
    """Конфигурация фильтров для канала"""
    include_keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    require_all_includes: bool = False
    
    def to_dict(self) -> dict:
        """Конвертация в словарь"""
        return {
            'include_keywords': self.include_keywords,
            'exclude_keywords': self.exclude_keywords,
            'require_all_includes': self.require_all_includes
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'FilterConfig':
        """Создание из словаря"""
        return cls(
            include_keywords=data.get('include_keywords', []),
            exclude_keywords=data.get('exclude_keywords', []),
            require_all_includes=data.get('require_all_includes', False)
        )


@dataclass
class ChannelConfig:
    """Конфигурация канала для уведомлений"""
    id: str
    name: str
    telegram_id: int
    enabled: bool = True
    input_sources: List[str] = field(default_factory=list)
    filters: FilterConfig = field(default_factory=FilterConfig)
    
    # CRM функциональность
    crm_enabled: bool = False
    crm_group_id: int = 0  # ID группы для форум-топиков
    agents: List[AgentConfig] = field(default_factory=list)  # Список агентов для автоответов
    auto_response_enabled: bool = False
    auto_response_template: str = "Здравствуйте! Заинтересовала ваша вакансия. Расскажите подробнее?"
    # Опциональная пересылка исходного объявления в тему CRM
    mirror_job_post_to_topic: bool = False  # По умолчанию пересылаем вакансию в топик
    
    # Backward compatibility fields (deprecated)
    agent_phone: str = ""
    agent_session_name: str = ""
    
    def to_dict(self) -> dict:
        """Конвертация в словарь"""
        return {
            'id': self.id,
            'name': self.name,
            'telegram_id': self.telegram_id,
            'enabled': self.enabled,
            'input_sources': self.input_sources,
            'filters': self.filters.to_dict(),
            'crm_enabled': self.crm_enabled,
            'crm_group_id': self.crm_group_id,
            'agents': [agent.to_dict() for agent in self.agents],
            'auto_response_enabled': self.auto_response_enabled,
            'auto_response_template': self.auto_response_template,
             # Поведение CRM-темы
            'mirror_job_post_to_topic': self.mirror_job_post_to_topic,
            # Backward compatibility
            'agent_phone': self.agent_phone,
            'agent_session_name': self.agent_session_name
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ChannelConfig':
        """Создание из словаря"""
        filters_data = data.get('filters', {})
        
        # Parse agents list
        agents = []
        agents_data = data.get('agents', [])
        for agent_data in agents_data:
            agents.append(AgentConfig.from_dict(agent_data))
        
        # Backward compatibility: convert old single agent format to new list format
        if not agents and data.get('agent_phone') and data.get('agent_session_name'):
            agents.append(AgentConfig(
                phone=data['agent_phone'],
                session_name=data['agent_session_name']
            ))
        
        return cls(
            id=data['id'],
            name=data['name'],
            telegram_id=data['telegram_id'],
            enabled=data.get('enabled', True),
            input_sources=data.get('input_sources', []),
            filters=FilterConfig.from_dict(filters_data),
            crm_enabled=data.get('crm_enabled', False),
            crm_group_id=data.get('crm_group_id', 0),
            agents=agents,
            auto_response_enabled=data.get('auto_response_enabled', False),
            auto_response_template=data.get('auto_response_template', 'Здравствуйте! Заинтересовала ваша вакансия. Расскажите подробнее?'),
            mirror_job_post_to_topic=data.get('mirror_job_post_to_topic', False),
            # Backward compatibility
            agent_phone=data.get('agent_phone', ''),
            agent_session_name=data.get('agent_session_name', '')
        )


class ConfigManager:
    """Менеджер для работы с конфигурацией каналов"""
    
    def __init__(self, config_path: str = 'configs/channels_config.json'):
        """
        Инициализация менеджера конфигурации
        
        Args:
            config_path: Путь к файлу конфигурации
        """
        self.config_path = Path(config_path)
        self.channels: List[ChannelConfig] = []
        
        # Создаем директорию если не существует
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
    
    def load(self) -> List[ChannelConfig]:
        """
        Загрузить конфигурацию из файла
        
        Returns:
            Список конфигураций каналов
        """
        if not self.config_path.exists():
            logger.warning(f"Файл конфигурации {self.config_path} не найден. Создаем пустую конфигурацию.")
            self.channels = []
            return self.channels
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.channels = []
            for channel_data in data.get('output_channels', []):
                try:
                    channel = ChannelConfig.from_dict(channel_data)
                    self.channels.append(channel)
                except Exception as e:
                    logger.error(f"Ошибка загрузки канала {channel_data.get('id')}: {e}")
            
            logger.info(f"Загружено {len(self.channels)} каналов из конфигурации")
            return self.channels
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON конфигурации: {e}")
            self.channels = []
            return self.channels
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            self.channels = []
            return self.channels
    
    def save(self, channels: Optional[List[ChannelConfig]] = None) -> bool:
        """
        Сохранить конфигурацию в файл
        
        Args:
            channels: Список каналов для сохранения (если None, используется self.channels)
        
        Returns:
            True если успешно сохранено
        """
        if channels is not None:
            self.channels = channels
        
        try:
            data = {
                'output_channels': [channel.to_dict() for channel in self.channels]
            }
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Конфигурация сохранена: {len(self.channels)} каналов")
            return True
        
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")
            return False
    
    def get_channel(self, channel_id: str) -> Optional[ChannelConfig]:
        """
        Получить конфигурацию канала по ID
        
        Args:
            channel_id: ID канала
        
        Returns:
            ChannelConfig или None если не найден
        """
        for channel in self.channels:
            if channel.id == channel_id:
                return channel
        return None
    
    def add_channel(self, channel: ChannelConfig) -> bool:
        """
        Добавить новый канал
        
        Args:
            channel: Конфигурация канала
        
        Returns:
            True если успешно добавлен
        """
        # Проверяем уникальность ID
        if self.get_channel(channel.id):
            logger.error(f"Канал с ID {channel.id} уже существует")
            return False
        
        self.channels.append(channel)
        return self.save()
    
    def update_channel(self, channel_id: str, updated_channel: ChannelConfig) -> bool:
        """
        Обновить существующий канал
        
        Args:
            channel_id: ID канала для обновления
            updated_channel: Новая конфигурация
        
        Returns:
            True если успешно обновлен
        """
        for i, channel in enumerate(self.channels):
            if channel.id == channel_id:
                self.channels[i] = updated_channel
                return self.save()
        
        logger.error(f"Канал с ID {channel_id} не найден")
        return False
    
    def delete_channel(self, channel_id: str) -> bool:
        """
        Удалить канал
        
        Args:
            channel_id: ID канала для удаления
        
        Returns:
            True если успешно удален
        """
        for i, channel in enumerate(self.channels):
            if channel.id == channel_id:
                self.channels.pop(i)
                return self.save()
        
        logger.error(f"Канал с ID {channel_id} не найден")
        return False
    
    def get_all_input_sources(self) -> set:
        """
        Получить все уникальные input источники из всех каналов
        
        Returns:
            Set всех источников
        """
        sources = set()
        for channel in self.channels:
            if channel.enabled:
                sources.update(channel.input_sources)
        return sources
    
    def get_output_channels_for_source(self, source: str) -> List[ChannelConfig]:
        """
        Получить все output каналы, которые мониторят данный источник
        
        Args:
            source: Имя источника (@channel или ID)
        
        Returns:
            Список конфигураций каналов
        """
        result = []
        for channel in self.channels:
            if channel.enabled and source in channel.input_sources:
                result.append(channel)
        return result
    
    def validate(self) -> List[str]:
        """
        Валидация конфигурации
        
        Returns:
            Список ошибок (пустой если все ок)
        """
        errors = []
        
        # Проверка уникальности ID
        ids = [ch.id for ch in self.channels]
        if len(ids) != len(set(ids)):
            errors.append("Найдены дублирующиеся ID каналов")
        
        # Проверка каждого канала
        for channel in self.channels:
            if not channel.name:
                errors.append(f"Канал {channel.id}: отсутствует название")
            
            if channel.telegram_id == 0:
                errors.append(f"Канал {channel.id}: не указан telegram_id")
            
            if not channel.input_sources:
                errors.append(f"Канал {channel.id}: нет input источников")
            
            # CRM validation
            if channel.crm_enabled:
                if not channel.crm_group_id:
                    errors.append(f"Канал {channel.id}: CRM включен, но не указан crm_group_id")
                
                if not channel.agents:
                    errors.append(f"Канал {channel.id}: CRM включен, но нет агентов")
                else:
                    for i, agent in enumerate(channel.agents):
                        if not agent.phone:
                            errors.append(f"Канал {channel.id}, агент {i+1}: не указан телефон")
                        if not agent.session_name:
                            errors.append(f"Канал {channel.id}, агент {i+1}: не указано имя сессии")
            
            # include_keywords теперь необязательны - если нет, будут приходить все сообщения
        
        return errors


# Глобальный экземпляр менеджера
config_manager = ConfigManager()

