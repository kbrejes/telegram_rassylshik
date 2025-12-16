"""
Модуль аутентификации для Telegram клиентов
"""
from .base import TelegramAuthManager
from .bot_auth import BotAuthManager, bot_auth_manager
from .agent_auth import AgentAuthManager, agent_auth_manager

__all__ = [
    'TelegramAuthManager',
    'BotAuthManager',
    'bot_auth_manager',
    'AgentAuthManager',
    'agent_auth_manager'
]
