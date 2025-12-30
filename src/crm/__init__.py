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

__all__ = [
    "create_topic_with_fallback",
    "send_topic_info",
    "mirror_auto_response",
    "init_ai_context",
]
