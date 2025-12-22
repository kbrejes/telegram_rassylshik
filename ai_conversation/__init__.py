"""
AI Conversation Module

Provides intelligent conversation capabilities using LLM with multi-level memory system.
Supports multiple LLM backends: Ollama, LM Studio, OpenAI.
"""

from .llm_client import UnifiedLLMClient, LLMProviderConfig
from .memory import ConversationMemory
from .handler import AIConversationHandler, AIHandlerPool, AIConfig

__all__ = [
    "UnifiedLLMClient",
    "LLMProviderConfig",
    "ConversationMemory",
    "AIConversationHandler",
    "AIHandlerPool",
    "AIConfig",
]
