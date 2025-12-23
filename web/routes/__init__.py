"""Web routes package"""
from .pages import router as pages_router
from .channels import router as channels_router
from .agents import router as agents_router
from .auth import router as auth_router
from .telegram import router as telegram_router
from .channel_creation import router as channel_creation_router

__all__ = [
    'pages_router',
    'channels_router',
    'agents_router',
    'auth_router',
    'telegram_router',
    'channel_creation_router'
]
