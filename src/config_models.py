"""
Модели данных для конфигурации каналов
"""
import os
from dataclasses import dataclass, field
from typing import List


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
    llm_provider: str = "groq"  # "groq" | "ollama" | "openai"
    llm_model: str = "llama-3.3-70b-versatile"
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
            llm_provider=data.get('llm_provider', 'groq'),
            llm_model=data.get('llm_model', 'llama-3.3-70b-versatile'),
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
class JobAnalyzerConfig:
    """Configuration for LLM-based job analyzer."""
    enabled: bool = True
    min_salary_rub: int = 70_000
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"

    def to_dict(self) -> dict:
        return {
            'enabled': self.enabled,
            'min_salary_rub': self.min_salary_rub,
            'llm_provider': self.llm_provider,
            'llm_model': self.llm_model,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'JobAnalyzerConfig':
        return cls(
            enabled=data.get('enabled', True),
            min_salary_rub=data.get('min_salary_rub', 70_000),
            llm_provider=data.get('llm_provider', 'groq'),
            llm_model=data.get('llm_model', 'llama-3.3-70b-versatile'),
        )


@dataclass
class PromptsConfig:
    """Конфигурация промптов для AI"""
    base_context: str = ""
    discovery: str = ""
    engagement: str = ""
    call_ready: str = ""
    call_pending: str = ""
    call_declined: str = ""

    def to_dict(self) -> dict:
        return {
            'base_context': self.base_context,
            'discovery': self.discovery,
            'engagement': self.engagement,
            'call_ready': self.call_ready,
            'call_pending': self.call_pending,
            'call_declined': self.call_declined,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PromptsConfig':
        return cls(
            base_context=data.get('base_context', ''),
            discovery=data.get('discovery', ''),
            engagement=data.get('engagement', ''),
            call_ready=data.get('call_ready', ''),
            call_pending=data.get('call_pending', ''),
            call_declined=data.get('call_declined', ''),
        )

    @classmethod
    def load_defaults(cls) -> 'PromptsConfig':
        """Загрузить дефолтные промпты из файлов"""
        prompts_dir = "prompts"

        def read_file(path: str) -> str:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                return ""

        return cls(
            base_context=read_file(os.path.join(prompts_dir, "base_context.txt")),
            discovery=read_file(os.path.join(prompts_dir, "phases", "discovery.txt")),
            engagement=read_file(os.path.join(prompts_dir, "phases", "engagement.txt")),
            call_ready=read_file(os.path.join(prompts_dir, "phases", "call_ready.txt")),
            call_pending=read_file(os.path.join(prompts_dir, "phases", "call_pending.txt")),
            call_declined=read_file(os.path.join(prompts_dir, "phases", "call_declined.txt")),
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
    instant_response: bool = False  # Skip human-like delays for instant responses

    # AI Conversation (включено по умолчанию)
    ai_conversation_enabled: bool = True
    ai_config: AIConfig = field(default_factory=AIConfig)

    # Промпты для AI (если пустые - используются дефолтные из файлов)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)

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
            'instant_response': self.instant_response,
            'ai_conversation_enabled': self.ai_conversation_enabled,
            'ai_config': self.ai_config.to_dict(),
            'prompts': self.prompts.to_dict(),
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

        # Parse prompts config
        prompts_data = data.get('prompts', {})
        prompts = PromptsConfig.from_dict(prompts_data) if prompts_data else PromptsConfig()

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
            instant_response=data.get('instant_response', False),
            ai_conversation_enabled=data.get('ai_conversation_enabled', True),
            ai_config=ai_config,
            prompts=prompts,
        )
