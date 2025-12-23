"""
Core modules for the job notification bot.

This package contains the main business logic:
- config: Application configuration
- database: Database operations
- agent_account/agent_pool: Telegram agent management
- message_processor: Message handling
- conversation_manager: CRM conversation tracking
- crm_handler: CRM group message handling
- config_manager: Channel configuration management
- session_config: Telegram session path management
- constants: Shared constants
"""

# Re-export everything for backward compatibility
from src.config import config
from src.database import db, Database
from src.config_models import (
    AgentConfig,
    AIConfig,
    PromptsConfig,
    FilterConfig,
    ChannelConfig,
)
from src.config_manager import ConfigManager, config_manager
from src.constants import SERVICE_MESSAGE_PREFIXES, is_service_message
from src.session_config import (
    get_bot_session_path,
    get_agent_session_path,
    delete_session_file,
    SESSIONS_DIR,
    PROJECT_ROOT,
)

# Lazy imports for heavier modules to avoid circular dependencies
def get_agent_account():
    from src.agent_account import AgentAccount
    return AgentAccount

def get_agent_pool():
    from src.agent_pool import agent_pool, get_or_create_agent
    return agent_pool, get_or_create_agent

def get_message_processor():
    from src.message_processor import message_processor
    return message_processor

def get_conversation_manager():
    from src.conversation_manager import ConversationManager
    return ConversationManager

def get_crm_handler():
    from src.crm_handler import CRMHandler
    return CRMHandler

__all__ = [
    # Config
    'config',
    'ConfigManager',
    'config_manager',
    'AgentConfig',
    'AIConfig',
    'PromptsConfig',
    'FilterConfig',
    'ChannelConfig',
    # Database
    'db',
    'Database',
    # Session
    'get_bot_session_path',
    'get_agent_session_path',
    'delete_session_file',
    'SESSIONS_DIR',
    'PROJECT_ROOT',
    # Constants
    'SERVICE_MESSAGE_PREFIXES',
    'is_service_message',
    # Lazy getters
    'get_agent_account',
    'get_agent_pool',
    'get_message_processor',
    'get_conversation_manager',
    'get_crm_handler',
]
