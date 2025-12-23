"""
Менеджер конфигурации для управления несколькими каналами уведомлений
"""
import json
from typing import List, Optional, Dict
from pathlib import Path
import logging

from src.config_models import (
    AgentConfig,
    AIConfig,
    PromptsConfig,
    FilterConfig,
    ChannelConfig,
)

logger = logging.getLogger(__name__)

# Re-export models for backward compatibility
__all__ = [
    'AgentConfig',
    'AIConfig',
    'PromptsConfig',
    'FilterConfig',
    'ChannelConfig',
    'ConfigManager',
    'config_manager',
]


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
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "${GROQ_API_KEY}",
            "default_model": "llama-3.3-70b-versatile"
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

