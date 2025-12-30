"""
CRM Module

Helper functions for CRM functionality.
Main CRMHandler class remains in src/crm_handler.py for now.
"""

from .topic_utils import (
    create_topic_with_fallback,
    send_topic_info,
    mirror_auto_response,
    init_ai_context,
)
from .auto_responder import send_auto_response
from .ai_integration import handle_ai_response

__all__ = [
    "create_topic_with_fallback",
    "send_topic_info",
    "mirror_auto_response",
    "init_ai_context",
    "send_auto_response",
    "handle_ai_response",
]
