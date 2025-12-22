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
class AIConfig:
    """Конфигурация AI для разговоров"""
    llm_provider: str = "ollama"  # "ollama" | "lm_studio" | "openai"
    llm_model: str = "qwen2.5:3b"
    persona_file: str = "personas/default.txt"
    mode: str = "auto"  # "auto" | "suggest" | "manual"
    reply_delay_seconds: List[int] = field(default_factory=lambda: [3, 8])
    context_window_messages: int = 12
    weaviate_host: str = "localhost"
    weaviate_port: int = 8080
    use_weaviate: bool = True
    knowledge_files: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'llm_provider': self.llm_provider,
            'llm_model': self.llm_model,
            'persona_file': self.persona_file,
            'mode': self.mode,
            'reply_delay_seconds': self.reply_delay_seconds,
            'context_window_messages': self.context_window_messages,
            'weaviate_host': self.weaviate_host,
            'weaviate_port': self.weaviate_port,
            'use_weaviate': self.use_weaviate,
            'knowledge_files': self.knowledge_files,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AIConfig':
        return cls(
            llm_provider=data.get('llm_provider', 'ollama'),
            llm_model=data.get('llm_model', 'qwen2.5:3b'),
            persona_file=data.get('persona_file', 'personas/default.txt'),
            mode=data.get('mode', 'auto'),
            reply_delay_seconds=data.get('reply_delay_seconds', [3, 8]),
            context_window_messages=data.get('context_window_messages', 12),
            weaviate_host=data.get('weaviate_host', 'localhost'),
            weaviate_port=data.get('weaviate_port', 8080),
            use_weaviate=data.get('use_weaviate', True),
            knowledge_files=data.get('knowledge_files', []),
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

    # AI Conversation
    ai_conversation_enabled: bool = False
    ai_config: AIConfig = field(default_factory=AIConfig)

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
            'ai_conversation_enabled': self.ai_conversation_enabled,
            'ai_config': self.ai_config.to_dict(),
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
        
        # Parse AI config
        ai_config_data = data.get('ai_config', {})
        ai_config = AIConfig.from_dict(ai_config_data) if ai_config_data else AIConfig()

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
            ai_conversation_enabled=data.get('ai_conversation_enabled', False),
            ai_config=ai_config,
        )


class ConfigManager:
    """Менеджер для работы с конфигурацией каналов"""

    # Дефолтные LLM провайдеры
    DEFAULT_LLM_PROVIDERS = {
        "ollama": {
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
            "default_model": "qwen2.5:3b"
        },
        "lm_studio": {
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "lm-studio",
            "default_model": "qwen2.5-vl-7b-instruct"
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "${OPENAI_API_KEY}",
            "default_model": "gpt-4o-mini"
        }
    }

    def __init__(self, config_path: str = 'configs/channels_config.json'):
        """
        Инициализация менеджера конфигурации

        Args:
            config_path: Путь к файлу конфигурации
        """
        self.config_path = Path(config_path)
        self.channels: List[ChannelConfig] = []
        self.llm_providers: Dict[str, dict] = self.DEFAULT_LLM_PROVIDERS.copy()

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

            # Load LLM providers (merge with defaults)
            if 'llm_providers' in data:
                self.llm_providers = {**self.DEFAULT_LLM_PROVIDERS, **data['llm_providers']}

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
                'output_channels': [channel.to_dict() for channel in self.channels],
                'llm_providers': self.llm_providers,
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

