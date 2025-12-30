"""
Data models for channel configuration
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class AgentConfig:
    """Agent configuration for CRM"""
    phone: str
    session_name: str

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'phone': self.phone,
            'session_name': self.session_name
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AgentConfig':
        """Create from dictionary"""
        return cls(
            phone=data['phone'],
            session_name=data['session_name']
        )


@dataclass
class AIConfig:
    """Configuration for AI conversation handler.

    This is the canonical AIConfig used throughout the codebase.
    """
    # LLM settings
    llm_provider: str = "groq"  # "groq" | "ollama" | "openai"
    llm_model: str = "llama-3.3-70b-versatile"
    persona_file: str = "personas/default.txt"
    mode: str = "auto"  # "auto" | "suggest" | "manual"
    reply_delay_seconds: List[int] = field(default_factory=lambda: [3, 8])
    context_window_messages: int = 24

    # Weaviate (vector memory)
    weaviate_host: str = "localhost"
    weaviate_port: int = 8080
    use_weaviate: bool = True
    knowledge_files: List[str] = field(default_factory=list)

    # State analyzer settings
    use_state_analyzer: bool = True
    prompts_dir: str = "prompts"
    states_dir: str = "data/conversation_states"

    # Self-correcting system (disabled - replaced by playground testing)
    use_self_correction: bool = False
    enable_contact_learning: bool = False

    def to_dict(self) -> dict:
        return {
            'llm_provider': self.llm_provider,
            'llm_model': self.llm_model,
            'persona_file': self.persona_file,
            'mode': self.mode,
            'reply_delay_seconds': list(self.reply_delay_seconds),
            'context_window_messages': self.context_window_messages,
            'weaviate_host': self.weaviate_host,
            'weaviate_port': self.weaviate_port,
            'use_weaviate': self.use_weaviate,
            'knowledge_files': self.knowledge_files,
            'use_state_analyzer': self.use_state_analyzer,
            'prompts_dir': self.prompts_dir,
            'states_dir': self.states_dir,
            'use_self_correction': self.use_self_correction,
            'enable_contact_learning': self.enable_contact_learning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AIConfig':
        # Handle both list and tuple for reply_delay_seconds
        delay = data.get('reply_delay_seconds', [3, 8])
        if isinstance(delay, tuple):
            delay = list(delay)

        return cls(
            llm_provider=data.get('llm_provider', 'groq'),
            llm_model=data.get('llm_model', 'llama-3.3-70b-versatile'),
            persona_file=data.get('persona_file', 'personas/default.txt'),
            mode=data.get('mode', 'auto'),
            reply_delay_seconds=delay,
            context_window_messages=data.get('context_window_messages', 24),
            weaviate_host=data.get('weaviate_host', 'localhost'),
            weaviate_port=data.get('weaviate_port', 8080),
            use_weaviate=data.get('use_weaviate', True),
            knowledge_files=data.get('knowledge_files', []),
            use_state_analyzer=data.get('use_state_analyzer', True),
            prompts_dir=data.get('prompts_dir', 'prompts'),
            states_dir=data.get('states_dir', 'data/conversation_states'),
            use_self_correction=data.get('use_self_correction', False),
            enable_contact_learning=data.get('enable_contact_learning', False),
        )

    @property
    def reply_delay_tuple(self) -> tuple:
        """Return reply_delay_seconds as tuple for backwards compatibility."""
        return tuple(self.reply_delay_seconds)


@dataclass
class JobAnalyzerConfig:
    """Configuration for LLM-based job analyzer."""
    enabled: bool = True
    min_salary_rub: int = 70_000
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"
    require_tg_contact: bool = False  # Reject vacancies without extractable TG contact

    def to_dict(self) -> dict:
        return {
            'enabled': self.enabled,
            'min_salary_rub': self.min_salary_rub,
            'llm_provider': self.llm_provider,
            'llm_model': self.llm_model,
            'require_tg_contact': self.require_tg_contact,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'JobAnalyzerConfig':
        return cls(
            enabled=data.get('enabled', True),
            min_salary_rub=data.get('min_salary_rub', 70_000),
            llm_provider=data.get('llm_provider', 'groq'),
            llm_model=data.get('llm_model', 'llama-3.3-70b-versatile'),
            require_tg_contact=data.get('require_tg_contact', False),
        )


@dataclass
class PromptsConfig:
    """Prompts configuration for AI"""
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
        """Load default prompts from files"""
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
    """Filter configuration for channel"""
    include_keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    require_all_includes: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'include_keywords': self.include_keywords,
            'exclude_keywords': self.exclude_keywords,
            'require_all_includes': self.require_all_includes
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FilterConfig':
        """Create from dictionary"""
        return cls(
            include_keywords=data.get('include_keywords', []),
            exclude_keywords=data.get('exclude_keywords', []),
            require_all_includes=data.get('require_all_includes', False)
        )


@dataclass
class ChannelConfig:
    """Channel configuration for notifications"""
    id: str
    name: str
    telegram_id: int
    enabled: bool = True
    input_sources: List[str] = field(default_factory=list)
    filters: FilterConfig = field(default_factory=FilterConfig)

    # CRM functionality
    crm_enabled: bool = False
    crm_group_id: int = 0  # Group ID for forum topics
    agents: List[AgentConfig] = field(default_factory=list)  # List of agents for auto-responses
    auto_response_enabled: bool = False
    auto_response_template: str = "Hello! Interested in your vacancy. Could you tell me more?"
    instant_response: bool = False  # Skip human-like delays for instant responses

    # AI Conversation (enabled by default)
    ai_conversation_enabled: bool = True
    ai_config: AIConfig = field(default_factory=AIConfig)

    # AI prompts (if empty - defaults from files are used)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)

    def to_dict(self) -> dict:
        """Convert to dictionary"""
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
        """Create from dictionary"""
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
            auto_response_template=data.get('auto_response_template', 'Hello! Interested in your vacancy. Could you tell me more?'),
            instant_response=data.get('instant_response', False),
            ai_conversation_enabled=data.get('ai_conversation_enabled', True),
            ai_config=ai_config,
            prompts=prompts,
        )
