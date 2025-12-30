"""
CRM Module

Handles CRM functionality: auto-responses, topics, and message relay.
Split from crm_handler.py for better maintainability.
"""

from .handler import CRMHandler
from .topic_utils import (
    create_topic_with_fallback,
    send_topic_info,
    mirror_auto_response,
    init_ai_context,
)

__all__ = [
    "CRMHandler",
    "create_topic_with_fallback",
    "send_topic_info",
    "mirror_auto_response",
    "init_ai_context",
]
