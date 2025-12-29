"""
AI Conversation Module

Provides intelligent conversation capabilities using LLM with multi-level memory system.
Supports multiple LLM backends: OpenAI, Groq, Gemini, OpenRouter.

Two-level phase system:
1. StateAnalyzer - LLM-based analyzer that determines conversation phase
2. PhasePromptBuilder - builds dynamic system prompts based on phase

Phases:
- discovery: Understanding the request, providing info
- engagement: Deepening interest, showing value
- call_ready: Good moment to offer a call
- call_pending: Call offered, waiting for response
- call_declined: Client declined, work via text
"""

from .llm_client import UnifiedLLMClient, LLMProviderConfig
from .memory import ConversationMemory
from .handler import AIConversationHandler, AIHandlerPool, AIConfig
from .state_analyzer import StateAnalyzer, StateStorage, ConversationState, AnalysisResult
from .phase_prompts import PhasePromptBuilder, ensure_prompts_directory
from .edge_cases import edge_case_handler, EdgeCaseHandler, EdgeCaseResult
from .style_analyzer import style_analyzer, StyleAnalyzer, UserStyle

__all__ = [
    # LLM Client
    "UnifiedLLMClient",
    "LLMProviderConfig",
    # Memory
    "ConversationMemory",
    # Handler
    "AIConversationHandler",
    "AIHandlerPool",
    "AIConfig",
    # State Analyzer
    "StateAnalyzer",
    "StateStorage",
    "ConversationState",
    "AnalysisResult",
    # Phase Prompts
    "PhasePromptBuilder",
    "ensure_prompts_directory",
    # Edge Cases
    "edge_case_handler",
    "EdgeCaseHandler",
    "EdgeCaseResult",
    # Style Analyzer
    "style_analyzer",
    "StyleAnalyzer",
    "UserStyle",
]
