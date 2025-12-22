"""
Conversation State Analyzer

Two-level system:
1. StateAnalyzer - LLM-based analyzer that determines conversation phase
2. ConversationState - persistent state tracking

Phases:
- discovery: Understanding the request, providing info
- engagement: Deepening interest, showing value
- call_ready: Good moment to offer a call
- call_pending: Call offered, waiting for response
- call_declined: Client declined, work via text
"""

import os
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from .llm_client import UnifiedLLMClient

logger = logging.getLogger(__name__)


# =============================================================================
# CONVERSATION STATE
# =============================================================================

@dataclass
class ConversationState:
    """
    Persistent conversation state for a contact.

    Tracks facts about the conversation - no rules, just data.
    """
    contact_id: int
    current_phase: str = "discovery"
    messages_in_phase: int = 0
    total_messages: int = 0
    call_offered: bool = False
    call_declined: bool = False
    call_scheduled: bool = False
    last_interaction: Optional[str] = None  # ISO format datetime
    created_at: Optional[str] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()

    def update_interaction(self):
        """Update last interaction time."""
        self.last_interaction = datetime.now().isoformat()
        self.total_messages += 1
        self.messages_in_phase += 1

    def set_phase(self, new_phase: str):
        """Change phase and reset counter."""
        if new_phase != self.current_phase:
            logger.info(f"[STATE] Phase change: {self.current_phase} -> {new_phase}")
            self.current_phase = new_phase
            self.messages_in_phase = 0

    def mark_call_offered(self):
        """Mark that a call was offered."""
        self.call_offered = True
        self.set_phase("call_pending")

    def mark_call_declined(self):
        """Mark that client declined the call."""
        self.call_declined = True
        self.set_phase("call_declined")

    def mark_call_scheduled(self):
        """Mark that a call was scheduled."""
        self.call_scheduled = True

    def hours_since_last_interaction(self) -> Optional[float]:
        """Get hours since last interaction."""
        if not self.last_interaction:
            return None
        try:
            last = datetime.fromisoformat(self.last_interaction)
            delta = datetime.now() - last
            return delta.total_seconds() / 3600
        except:
            return None

    def to_context(self) -> str:
        """Generate context string for LLM."""
        hours = self.hours_since_last_interaction()
        time_info = ""
        if hours is not None:
            if hours < 1:
                time_info = "Последнее сообщение: только что"
            elif hours < 24:
                time_info = f"Последнее сообщение: {int(hours)} ч. назад"
            else:
                days = int(hours / 24)
                time_info = f"Последнее сообщение: {days} дн. назад"

        return f"""ТЕКУЩЕЕ СОСТОЯНИЕ РАЗГОВОРА:
- Текущая фаза: {self.current_phase}
- Всего сообщений: {self.total_messages}
- Сообщений в текущей фазе: {self.messages_in_phase}
- Созвон предлагали: {"да" if self.call_offered else "нет"}
- Отказался от созвона: {"да" if self.call_declined else "нет"}
- Созвон назначен: {"да" if self.call_scheduled else "нет"}
{time_info}"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        """Create from dictionary."""
        return cls(**data)


# =============================================================================
# STATE STORAGE
# =============================================================================

class StateStorage:
    """
    Persistent storage for conversation states.

    Stores states as JSON files in data/conversation_states/
    """

    def __init__(self, storage_dir: str = "data/conversation_states"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[int, ConversationState] = {}

    def _get_path(self, contact_id: int) -> Path:
        return self.storage_dir / f"{contact_id}.json"

    def load(self, contact_id: int) -> ConversationState:
        """Load state for contact, create new if not exists."""
        # Check cache first
        if contact_id in self._cache:
            return self._cache[contact_id]

        path = self._get_path(contact_id)

        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                state = ConversationState.from_dict(data)
                logger.debug(f"[STATE] Loaded state for {contact_id}")
            except Exception as e:
                logger.warning(f"[STATE] Error loading state for {contact_id}: {e}")
                state = ConversationState(contact_id=contact_id)
        else:
            state = ConversationState(contact_id=contact_id)

        self._cache[contact_id] = state
        return state

    def save(self, state: ConversationState):
        """Save state to disk."""
        path = self._get_path(state.contact_id)

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
            self._cache[state.contact_id] = state
            logger.debug(f"[STATE] Saved state for {state.contact_id}")
        except Exception as e:
            logger.error(f"[STATE] Error saving state for {state.contact_id}: {e}")

    def delete(self, contact_id: int):
        """Delete state."""
        path = self._get_path(contact_id)
        if path.exists():
            path.unlink()
        if contact_id in self._cache:
            del self._cache[contact_id]


# =============================================================================
# STATE ANALYZER
# =============================================================================

STATE_ANALYZER_PROMPT = """Ты анализатор состояния разговора. Твоя задача - определить текущую фазу разговора и дать рекомендации.

ФАЗЫ РАЗГОВОРА:
1. "discovery" - Начало разговора. Нужно понять запрос клиента, ответить на вопросы, дать полезную информацию.
2. "engagement" - Клиент заинтересован. Нужно углубить интерес, показать экспертизу и ценность.
3. "call_ready" - Хороший момент предложить созвон. Клиент выразил явный интерес, задаёт детальные вопросы, обсуждает проект.
4. "call_pending" - Созвон уже предложен, ждём ответа. НЕ повторять предложение.
5. "call_declined" - Клиент отказался от созвона. Работаем в переписке, НЕ предлагаем созвон снова.

ПРАВИЛА ОПРЕДЕЛЕНИЯ ФАЗЫ:
- Если сообщений мало (1-2) → обычно "discovery"
- Если клиент только спрашивает базовые вопросы → "discovery" или "engagement"
- Если клиент говорит "интересно", "хочу узнать больше", обсуждает детали проекта → можно "call_ready"
- Если созвон УЖЕ предлагали и не получили ответ → "call_pending"
- Если клиент сказал "не хочу звонить", "давайте в переписке" → "call_declined"
- Если прошло много времени (дни) с момента предложения созвона без ответа → можно вернуться к "engagement"

ТЕКУЩЕЕ СОСТОЯНИЕ:
{state_context}

ИСТОРИЯ РАЗГОВОРА (последние сообщения):
{conversation}

ПОСЛЕДНЕЕ СООБЩЕНИЕ ОТ КЛИЕНТА:
{last_message}

Проанализируй и верни JSON:
{{
    "phase": "discovery|engagement|call_ready|call_pending|call_declined",
    "confidence": 0.0-1.0,
    "answer_question_first": true/false,
    "mention_founders": true/false,
    "call_offered_in_history": true/false,
    "call_declined_in_history": true/false,
    "reasoning": "краткое объяснение почему эта фаза"
}}

ВАЖНО:
- "answer_question_first": true если клиент задал прямой вопрос - сначала нужно ответить
- "mention_founders": true только если клиент спрашивает кто мы / хочет говорить с руководством
- "call_offered_in_history": true если в истории видишь что мы уже предлагали созвон
- "call_declined_in_history": true если клиент явно отказывался от созвона
- Будь консервативен: лучше остаться в "engagement" чем преждевременно перейти в "call_ready"

Верни ТОЛЬКО JSON, без пояснений."""


@dataclass
class AnalysisResult:
    """Result of state analysis."""
    phase: str
    confidence: float
    answer_question_first: bool
    mention_founders: bool
    call_offered_in_history: bool
    call_declined_in_history: bool
    reasoning: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalysisResult":
        return cls(
            phase=data.get("phase", "discovery"),
            confidence=data.get("confidence", 0.5),
            answer_question_first=data.get("answer_question_first", False),
            mention_founders=data.get("mention_founders", False),
            call_offered_in_history=data.get("call_offered_in_history", False),
            call_declined_in_history=data.get("call_declined_in_history", False),
            reasoning=data.get("reasoning", ""),
        )

    @classmethod
    def default(cls, phase: str = "discovery") -> "AnalysisResult":
        """Create default result."""
        return cls(
            phase=phase,
            confidence=0.5,
            answer_question_first=False,
            mention_founders=False,
            call_offered_in_history=False,
            call_declined_in_history=False,
            reasoning="default fallback",
        )


class StateAnalyzer:
    """
    LLM-based conversation state analyzer.

    Analyzes conversation history and determines the current phase.
    """

    def __init__(
        self,
        llm_client: UnifiedLLMClient,
        storage: Optional[StateStorage] = None,
    ):
        self.llm = llm_client
        self.storage = storage or StateStorage()

    def format_conversation(self, messages: List[Dict[str, str]]) -> str:
        """Format messages for analysis."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user":
                lines.append(f"КЛИЕНТ: {content}")
            elif role == "assistant":
                lines.append(f"МЫ: {content}")
            elif role == "system":
                lines.append(f"[КОНТЕКСТ: {content[:100]}...]")

        return "\n".join(lines[-10:])  # Last 10 messages

    async def analyze(
        self,
        contact_id: int,
        messages: List[Dict[str, str]],
        last_message: str,
    ) -> AnalysisResult:
        """
        Analyze conversation and determine phase.

        Args:
            contact_id: Contact ID
            messages: Conversation history
            last_message: Latest message from contact

        Returns:
            AnalysisResult with phase and recommendations
        """
        # Load current state
        state = self.storage.load(contact_id)

        # Build prompt
        prompt = STATE_ANALYZER_PROMPT.format(
            state_context=state.to_context(),
            conversation=self.format_conversation(messages),
            last_message=last_message,
        )

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "Ты анализатор. Отвечай только JSON."},
                {"role": "user", "content": prompt}
            ])

            # Parse JSON
            # Handle potential markdown code blocks
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            response = response.strip()

            data = json.loads(response)
            result = AnalysisResult.from_dict(data)

            logger.info(f"[ANALYZER] Contact {contact_id}: {result.phase} (conf={result.confidence:.2f}) - {result.reasoning}")

            # Update state based on analysis
            if result.call_offered_in_history and not state.call_offered:
                state.call_offered = True

            if result.call_declined_in_history and not state.call_declined:
                state.call_declined = True

            # Update phase
            state.set_phase(result.phase)
            state.update_interaction()

            # Save state
            self.storage.save(state)

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[ANALYZER] JSON parse error: {e}, response: {response[:200]}")
            return AnalysisResult.default(state.current_phase)
        except Exception as e:
            logger.error(f"[ANALYZER] Error: {e}")
            return AnalysisResult.default(state.current_phase)

    def get_state(self, contact_id: int) -> ConversationState:
        """Get current state for contact."""
        return self.storage.load(contact_id)

    def update_state_after_response(
        self,
        contact_id: int,
        bot_response: str,
    ):
        """
        Update state after bot response.

        Detects if call was offered in the response.
        """
        state = self.storage.load(contact_id)

        # Simple detection of call offer
        call_indicators = [
            "созвониться",
            "календар",
            "встреч",
            "звонок",
            "call",
            "meet",
            "calendly",
            "cal.com",
        ]

        response_lower = bot_response.lower()
        if any(ind in response_lower for ind in call_indicators):
            if not state.call_offered:
                logger.info(f"[ANALYZER] Detected call offer in response for {contact_id}")
                state.mark_call_offered()
                self.storage.save(state)

    def reset_state(self, contact_id: int):
        """Reset state for contact."""
        self.storage.delete(contact_id)
        logger.info(f"[ANALYZER] Reset state for {contact_id}")
