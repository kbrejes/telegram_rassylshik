"""
AI Conversation Handler

Handles AI-powered conversations with contacts.
Integrates with bot_multi.py for seamless lead engagement.

Two-level system:
1. StateAnalyzer - determines conversation phase
2. PhasePromptBuilder - builds dynamic system prompts

Self-correcting system:
- Tracks conversation outcomes
- A/B tests prompt variants
- Auto-improves based on results
"""

import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable, List, TYPE_CHECKING

from src.human_behavior import human_behavior
from .llm_client import UnifiedLLMClient
from .memory import ConversationMemory
from .state_analyzer import StateAnalyzer, StateStorage, ConversationState, AnalysisResult
from .phase_prompts import PhasePromptBuilder, ensure_prompts_directory
from .correction_applier import CorrectionApplier
from .edge_cases import edge_case_handler, EdgeCaseResult
from .style_analyzer import style_analyzer

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)


@dataclass
class AIConfig:
    """Configuration for AI conversation handler."""
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"
    persona_file: str = "personas/default.txt"
    mode: str = "auto"  # "auto" | "suggest" | "manual"
    reply_delay_seconds: tuple = (3, 8)  # (min, max) random delay
    context_window_messages: int = 24
    weaviate_host: str = "localhost"
    weaviate_port: int = 8081  # Use 8081 since 8080 is web app
    use_weaviate: bool = True
    knowledge_files: list = field(default_factory=list)

    # State analyzer settings
    use_state_analyzer: bool = True  # Enable two-level phase system
    prompts_dir: str = "prompts"
    states_dir: str = "data/conversation_states"

    # Self-correcting system settings (disabled - replaced by playground testing)
    use_self_correction: bool = False
    enable_contact_learning: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AIConfig":
        """Create from dictionary."""
        delay = data.get("reply_delay_seconds", [3, 8])
        if isinstance(delay, list):
            delay = tuple(delay)

        return cls(
            llm_provider=data.get("llm_provider", "groq"),
            llm_model=data.get("llm_model", "llama-3.3-70b-versatile"),
            persona_file=data.get("persona_file", "personas/default.txt"),
            mode=data.get("mode", "auto"),
            reply_delay_seconds=delay,
            context_window_messages=data.get("context_window_messages", 24),
            weaviate_host=data.get("weaviate_host", "localhost"),
            weaviate_port=data.get("weaviate_port", 8081),
            use_weaviate=data.get("use_weaviate", True),
            knowledge_files=data.get("knowledge_files", []),
            use_state_analyzer=data.get("use_state_analyzer", True),
            prompts_dir=data.get("prompts_dir", "prompts"),
            states_dir=data.get("states_dir", "data/conversation_states"),
            use_self_correction=data.get("use_self_correction", False),
            enable_contact_learning=data.get("enable_contact_learning", False),
        )


class AIConversationHandler:
    """
    Handles AI-powered conversations with contacts.

    Two-level system:
    1. StateAnalyzer analyzes conversation and determines phase
    2. PhasePromptBuilder builds dynamic system prompt based on phase
    3. LLM generates response with phase-appropriate instructions

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
        database: Optional["Database"] = None,
    ):
        """
        Initialize AI handler.

        Args:
            config: AI configuration
            providers_config: LLM providers config from channels_config.json
            channel_id: Channel ID for logging/tracking
            database: Database instance for self-correction (optional)
        """
        self.config = config
        self.channel_id = channel_id
        self.providers_config = providers_config or {}
        self.database = database

        self.llm: Optional[UnifiedLLMClient] = None
        self.memory: Optional[ConversationMemory] = None

        # State analyzer components
        self.state_analyzer: Optional[StateAnalyzer] = None
        self.prompt_builder: Optional[PhasePromptBuilder] = None
        self.state_storage: Optional[StateStorage] = None

        # Self-correction components
        self.correction_applier: Optional[CorrectionApplier] = None

        # Track experiment assignments per contact
        self._contact_experiments: Dict[int, Dict[str, Any]] = {}

        self._initialized = False
        self._message_counts: Dict[int, int] = {}  # contact_id -> message count

    async def initialize(self):
        """Initialize LLM client, memory system, and state analyzer."""
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

            # Initialize state analyzer if enabled
            if self.config.use_state_analyzer:
                # Ensure prompts directory exists
                ensure_prompts_directory(self.config.prompts_dir)

                # Create components
                self.state_storage = StateStorage(self.config.states_dir)
                self.state_analyzer = StateAnalyzer(
                    llm_client=self.llm,
                    storage=self.state_storage,
                )
                self.prompt_builder = PhasePromptBuilder(self.config.prompts_dir)

                logger.info(f"[AI] State analyzer enabled for channel {self.channel_id}")

            # Initialize self-correction system
            if self.config.use_self_correction and self.database:
                self.correction_applier = CorrectionApplier(
                    db=self.database,
                    llm=self.llm
                )
                logger.info(f"[AI] Self-correction enabled for channel {self.channel_id}")

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
            # Generate response using appropriate method
            if self.config.use_state_analyzer and self.state_analyzer:
                response = await self._generate_with_state_analyzer(contact_id, message)
            else:
                # Fallback to old method
                response = await self.memory.generate_response(
                    contact_id=contact_id,
                    user_message=message,
                    include_knowledge=True,
                )

            if not response:
                logger.warning(f"[AI] Empty response for {contact_id}")
                return None

            logger.info(f"[AI] Generated response for {contact_id}: {response[:100]}...")

            # Handle based on mode
            if self.config.mode == "auto":
                # Add human-like delay before responding
                await human_behavior.simulate_delay(
                    message_length=len(message),
                    contact_id=contact_id
                )

                # Send directly to contact (typing is handled in agent_pool)
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

    async def _generate_with_state_analyzer(
        self,
        contact_id: int,
        message: str,
    ) -> Optional[str]:
        """
        Generate response using two-level state analyzer system.

        0. Check edge cases (probes, bot tests, gibberish)
        1. Analyze user's texting style
        2. Analyze conversation to determine phase
        3. Get A/B variant if experiment is active
        4. Build phase-specific system prompt with style mirroring
        5. Add contact-type-specific additions
        6. Generate response
        7. Update state and track outcome
        """
        # 0. Check edge cases FIRST (before LLM call)
        edge_result = edge_case_handler.analyze(contact_id, message)

        if edge_result.is_probe or edge_result.is_bot_test or edge_result.is_gibberish:
            logger.info(
                f"[AI] Edge case for {contact_id}: "
                f"probe={edge_result.is_probe}, bot_test={edge_result.is_bot_test}, "
                f"gibberish={edge_result.is_gibberish}"
            )

            # Always store user message in memory
            self.memory.add_message(contact_id, "user", message)

            if edge_result.hardcoded_response:
                # Return hardcoded response without LLM
                self.memory.add_message(contact_id, "assistant", edge_result.hardcoded_response)
                return edge_result.hardcoded_response

            if not edge_result.should_respond:
                # Don't respond at all
                return None

        # 1. Analyze user's texting style (for style mirroring)
        style_analyzer.analyze_message(contact_id, message)

        # Get working memory
        working_memory = self.memory.get_working_memory(contact_id)

        # 2. Analyze conversation state
        analysis = await self.state_analyzer.analyze(
            contact_id=contact_id,
            messages=working_memory,
            last_message=message,
        )

        logger.info(f"[AI] Phase for {contact_id}: {analysis.phase} (conf={analysis.confidence:.2f})")

        # 2. Get current state
        state = self.state_analyzer.get_state(contact_id)

        # 3. Check for A/B experiment variant
        variant_info = None
        if self.correction_applier:
            variant_info = await self.correction_applier.get_variant_for_contact(
                contact_id=contact_id,
                prompt_type="phase",
                prompt_name=analysis.phase
            )
            if variant_info.get("experiment_id"):
                self._contact_experiments[contact_id] = variant_info
                logger.debug(
                    f"[AI] Contact {contact_id} assigned to variant "
                    f"{variant_info['variant']} in experiment {variant_info['experiment_id']}"
                )

        # 4. Build phase-specific system prompt with style mirroring
        # Use variant content if available, otherwise use default
        if variant_info and variant_info.get("content"):
            system_prompt = variant_info["content"]
        else:
            system_prompt = self.prompt_builder.build_system_prompt(
                phase=analysis.phase,
                analysis=analysis,
                state=state,
                include_founders=analysis.mention_founders,
                contact_id=contact_id,  # For style mirroring
            )

        # 5. Add contact-type-specific prompt additions
        if self.correction_applier and self.config.enable_contact_learning:
            contact_additions = await self.correction_applier.get_contact_type_additions(
                working_memory
            )
            if contact_additions:
                system_prompt += contact_additions

        # 6. Build messages for LLM
        messages = [{"role": "system", "content": system_prompt}]

        # Add working memory (last N messages based on config)
        context_limit = max(self.config.context_window_messages, 12)
        messages.extend(working_memory[-context_limit:])

        # Add knowledge context if available
        knowledge = self.memory.semantic_recall(message)
        if knowledge:
            messages.append({
                "role": "user",
                "content": f"Релевантная информация:\n\n{knowledge}\n\nИспользуй если поможет ответить."
            })

        # Add current message
        messages.append({"role": "user", "content": message})

        # 7. Generate response
        response = await self.llm.achat(messages)

        # 8. Update memory
        self.memory.add_message(contact_id, "user", message)
        self.memory.add_message(contact_id, "assistant", response)

        # 9. Update state after response (detect if call was offered)
        self.state_analyzer.update_state_after_response(contact_id, response)

        # 10. Track outcome for self-correction
        if self.correction_applier:
            await self._track_outcome_if_terminal(contact_id, working_memory, state)

        return response

    async def _track_outcome_if_terminal(
        self,
        contact_id: int,
        messages: List[Dict[str, str]],
        state: ConversationState
    ):
        """Track outcome if conversation reached a terminal state."""
        # Get experiment info for this contact
        exp_info = self._contact_experiments.get(contact_id, {})

        outcome = await self.correction_applier.record_conversation_outcome(
            contact_id=contact_id,
            channel_id=self.channel_id,
            messages=messages,
            state=state,
            prompt_version_id=exp_info.get("version_id"),
            experiment_id=exp_info.get("experiment_id"),
            variant=exp_info.get("variant")
        )

        if outcome.outcome != "ongoing":
            logger.info(
                f"[AI] Recorded terminal outcome '{outcome.outcome}' "
                f"for contact {contact_id}"
            )

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

        # Initialize state if using state analyzer
        if self.state_analyzer:
            state = self.state_analyzer.get_state(contact_id)
            state.update_interaction()
            self.state_storage.save(state)

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

    def get_state(self, contact_id: int) -> Optional[ConversationState]:
        """Get conversation state for contact."""
        if self.state_analyzer:
            return self.state_analyzer.get_state(contact_id)
        return None

    def reset_state(self, contact_id: int):
        """Reset conversation state for contact."""
        if self.state_analyzer:
            self.state_analyzer.reset_state(contact_id)
        if self.memory:
            self.memory.clear_working_memory(contact_id)
        logger.info(f"[AI] Reset state for contact {contact_id}")

    def get_stats(self) -> Dict[str, Any]:
        """Get handler statistics."""
        stats = {
            "channel_id": self.channel_id,
            "mode": self.config.mode,
            "provider": self.config.llm_provider,
            "model": self.config.llm_model,
            "initialized": self._initialized,
            "active_conversations": len(self._message_counts),
            "total_messages": sum(self._message_counts.values()),
            "state_analyzer_enabled": self.config.use_state_analyzer,
            "self_correction_enabled": self.correction_applier is not None,
        }

        # Add state analyzer stats if enabled
        if self.state_analyzer and self.state_storage:
            stats["states_cached"] = len(self.state_storage._cache)

        # Add experiments being tracked
        stats["contacts_in_experiments"] = len(self._contact_experiments)

        return stats

    async def get_optimization_stats(self) -> Optional[Dict[str, Any]]:
        """Get self-correction optimization statistics."""
        if not self.correction_applier:
            return None
        return await self.correction_applier.get_optimization_stats()

    async def run_optimization_cycle(self):
        """Run a self-correction optimization cycle manually."""
        if not self.correction_applier:
            logger.warning("[AI] Self-correction not enabled, cannot run optimization")
            return None
        return await self.correction_applier.run_optimization_cycle()

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

    def __init__(
        self,
        providers_config: Optional[Dict[str, Any]] = None,
        database: Optional["Database"] = None
    ):
        """
        Initialize handler pool.

        Args:
            providers_config: LLM providers configuration
            database: Database instance for self-correction
        """
        self.providers_config = providers_config or {}
        self.database = database
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
                database=self.database,
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
