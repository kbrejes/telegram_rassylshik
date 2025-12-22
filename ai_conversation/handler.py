"""
AI Conversation Handler

Handles AI-powered conversations with contacts.
Integrates with bot_multi.py for seamless lead engagement.
"""

import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable

from .llm_client import UnifiedLLMClient
from .memory import ConversationMemory

logger = logging.getLogger(__name__)


@dataclass
class AIConfig:
    """Configuration for AI conversation handler."""
    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5:3b"
    persona_file: str = "personas/default.txt"
    mode: str = "auto"  # "auto" | "suggest" | "manual"
    reply_delay_seconds: tuple = (3, 8)  # (min, max) random delay
    context_window_messages: int = 12
    weaviate_host: str = "localhost"
    weaviate_port: int = 8080
    use_weaviate: bool = True
    knowledge_files: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AIConfig":
        """Create from dictionary."""
        delay = data.get("reply_delay_seconds", [3, 8])
        if isinstance(delay, list):
            delay = tuple(delay)

        return cls(
            llm_provider=data.get("llm_provider", "ollama"),
            llm_model=data.get("llm_model", "qwen2.5:3b"),
            persona_file=data.get("persona_file", "personas/default.txt"),
            mode=data.get("mode", "auto"),
            reply_delay_seconds=delay,
            context_window_messages=data.get("context_window_messages", 12),
            weaviate_host=data.get("weaviate_host", "localhost"),
            weaviate_port=data.get("weaviate_port", 8080),
            use_weaviate=data.get("use_weaviate", True),
            knowledge_files=data.get("knowledge_files", []),
        )


class AIConversationHandler:
    """
    Handles AI-powered conversations with contacts.

    Modes:
    - "auto": AI automatically responds to contact
    - "suggest": AI suggests response in CRM topic, operator confirms
    - "manual": No AI, only relay (existing behavior)

    Usage:
        handler = AIConversationHandler(config, providers_config)
        await handler.initialize()

        # When contact sends message
        response = await handler.handle_message(
            contact_id=123,
            message="Hello!",
            send_callback=async_send_func,
            suggest_callback=async_suggest_func,
        )
    """

    def __init__(
        self,
        config: AIConfig,
        providers_config: Optional[Dict[str, Any]] = None,
        channel_id: str = "",
    ):
        """
        Initialize AI handler.

        Args:
            config: AI configuration
            providers_config: LLM providers config from channels_config.json
            channel_id: Channel ID for logging/tracking
        """
        self.config = config
        self.channel_id = channel_id
        self.providers_config = providers_config or {}

        self.llm: Optional[UnifiedLLMClient] = None
        self.memory: Optional[ConversationMemory] = None

        self._initialized = False
        self._message_counts: Dict[int, int] = {}  # contact_id -> message count

    async def initialize(self):
        """Initialize LLM client and memory system."""
        if self._initialized:
            return

        try:
            # Create LLM client
            self.llm = UnifiedLLMClient.from_config(
                providers_config=self.providers_config,
                provider_name=self.config.llm_provider,
                model=self.config.llm_model,
            )

            # Create memory system
            self.memory = ConversationMemory(
                llm_client=self.llm,
                persona_path=self.config.persona_file,
                weaviate_host=self.config.weaviate_host,
                weaviate_port=self.config.weaviate_port,
                short_term_limit=self.config.context_window_messages,
                use_weaviate=self.config.use_weaviate,
            )

            # Load knowledge files
            for file_path in self.config.knowledge_files:
                self.memory.load_knowledge_file(file_path)

            self._initialized = True
            logger.info(f"[AI] Handler initialized for channel {self.channel_id}")

        except Exception as e:
            logger.error(f"[AI] Initialization failed: {e}")
            raise

    async def handle_message(
        self,
        contact_id: int,
        message: str,
        contact_name: str = "",
        send_callback: Optional[Callable[[int, str], Awaitable[bool]]] = None,
        suggest_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
    ) -> Optional[str]:
        """
        Handle incoming message from contact.

        Args:
            contact_id: Telegram user ID
            message: Message text
            contact_name: Contact display name
            send_callback: Async function to send message to contact
            suggest_callback: Async function to suggest message in topic

        Returns:
            AI response text (or None if manual mode)
        """
        if not self._initialized:
            await self.initialize()

        if self.config.mode == "manual":
            # Just store in memory, no AI response
            self.memory.add_message(contact_id, "user", message)
            return None

        # Track message count for episodic memory
        self._message_counts[contact_id] = self._message_counts.get(contact_id, 0) + 1

        try:
            # Generate AI response
            response = await self.memory.generate_response(
                contact_id=contact_id,
                user_message=message,
                include_knowledge=True,
            )

            logger.info(f"[AI] Generated response for {contact_id}: {response[:100]}...")

            # Handle based on mode
            if self.config.mode == "auto":
                # Add natural delay
                delay = random.uniform(*self.config.reply_delay_seconds)
                await asyncio.sleep(delay)

                # Send directly to contact
                if send_callback:
                    success = await send_callback(contact_id, response)
                    if success:
                        logger.info(f"[AI] Sent auto-response to {contact_id}")
                    else:
                        logger.warning(f"[AI] Failed to send to {contact_id}")

            elif self.config.mode == "suggest":
                # Suggest in CRM topic
                if suggest_callback:
                    await suggest_callback(contact_id, response, contact_name)
                    logger.info(f"[AI] Suggested response for {contact_id}")

            # Periodically save to episodic memory (every 10 messages)
            if self._message_counts[contact_id] % 10 == 0:
                asyncio.create_task(
                    self.memory.add_episodic_memory(contact_id, self.channel_id)
                )

            return response

        except Exception as e:
            logger.error(f"[AI] Error handling message: {e}")
            return None

    def add_operator_message(self, contact_id: int, message: str):
        """
        Record operator message (from CRM topic).

        Used to keep AI context aware of human intervention.
        """
        if self.memory:
            self.memory.add_message(contact_id, "assistant", message)
            logger.debug(f"[AI] Recorded operator message for {contact_id}")

    async def initialize_context(
        self,
        contact_id: int,
        initial_message: str,
        job_info: str = "",
    ):
        """
        Initialize conversation context with job info.

        Called when first contact is made (auto-response sent).

        Args:
            contact_id: Contact ID
            initial_message: The auto-response that was sent
            job_info: Original job posting info for context
        """
        if not self._initialized:
            await self.initialize()

        # Add job info as context
        if job_info:
            self.memory.add_message(
                contact_id, "system",
                f"Lead from job posting:\n{job_info}"
            )

        # Add the initial auto-response
        self.memory.add_message(contact_id, "assistant", initial_message)

        logger.info(f"[AI] Initialized context for contact {contact_id}")

    async def finalize_conversation(self, contact_id: int):
        """Finalize and save conversation to episodic memory."""
        if self.memory:
            await self.memory.finalize_session(contact_id, self.channel_id)
            logger.info(f"[AI] Finalized conversation for {contact_id}")

    def set_mode(self, mode: str):
        """Change operation mode."""
        if mode in ("auto", "suggest", "manual"):
            self.config.mode = mode
            logger.info(f"[AI] Mode changed to {mode}")
        else:
            logger.warning(f"[AI] Invalid mode: {mode}")

    def get_stats(self) -> Dict[str, Any]:
        """Get handler statistics."""
        return {
            "channel_id": self.channel_id,
            "mode": self.config.mode,
            "provider": self.config.llm_provider,
            "model": self.config.llm_model,
            "initialized": self._initialized,
            "active_conversations": len(self._message_counts),
            "total_messages": sum(self._message_counts.values()),
        }

    def close(self):
        """Cleanup resources."""
        if self.memory:
            self.memory.close()
        logger.info(f"[AI] Handler closed for channel {self.channel_id}")


class AIHandlerPool:
    """
    Pool of AI handlers for multiple channels.

    Manages AI handlers lifecycle and provides easy access.
    """

    def __init__(self, providers_config: Optional[Dict[str, Any]] = None):
        """
        Initialize handler pool.

        Args:
            providers_config: LLM providers configuration
        """
        self.providers_config = providers_config or {}
        self.handlers: Dict[str, AIConversationHandler] = {}

    async def get_or_create(
        self,
        channel_id: str,
        ai_config: AIConfig,
    ) -> AIConversationHandler:
        """
        Get existing handler or create new one.

        Args:
            channel_id: Channel ID
            ai_config: AI configuration

        Returns:
            AI handler for channel
        """
        if channel_id not in self.handlers:
            handler = AIConversationHandler(
                config=ai_config,
                providers_config=self.providers_config,
                channel_id=channel_id,
            )
            await handler.initialize()
            self.handlers[channel_id] = handler

        return self.handlers[channel_id]

    def get(self, channel_id: str) -> Optional[AIConversationHandler]:
        """Get handler if exists."""
        return self.handlers.get(channel_id)

    def remove(self, channel_id: str):
        """Remove and cleanup handler."""
        if channel_id in self.handlers:
            self.handlers[channel_id].close()
            del self.handlers[channel_id]

    def close_all(self):
        """Close all handlers."""
        for handler in self.handlers.values():
            handler.close()
        self.handlers.clear()
