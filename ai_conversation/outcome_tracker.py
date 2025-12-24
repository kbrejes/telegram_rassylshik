"""
Outcome Tracker - Detects and records conversation outcomes.

Outcomes:
- call_scheduled: SUCCESS - User agreed to a call
- disengaged: FAILURE - No response for 7+ days after call_pending
- declined: FAILURE - Explicit rejection detected
- ongoing: IN PROGRESS - Conversation still active
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ai_conversation.llm_client import UnifiedLLMClient
    from ai_conversation.state_analyzer import ConversationState

logger = logging.getLogger(__name__)


@dataclass
class OutcomeResult:
    """Result of outcome detection."""
    outcome: str  # 'call_scheduled', 'disengaged', 'declined', 'ongoing'
    confidence: float
    details: Dict[str, Any]
    detection_method: str  # 'keyword', 'state_flag', 'timeout', 'llm'


class OutcomeTracker:
    """
    Tracks and detects conversation outcomes.

    Usage:
        tracker = OutcomeTracker(llm_client)
        result = await tracker.detect_outcome(contact_id, state, messages)
    """

    # Success indicators (checked against agent/operator messages)
    SUCCESS_INDICATORS = [
        "созвон назначен",
        "созвон подтвержден",
        "записал",
        "забронировал",
        "встреча подтверждена",
        "договорились на",
        "жду вас",
        "ждем вас",
        "до встречи",
        "calendly",
        "cal.com",
        "время подходит",
        "отлично, тогда",
    ]

    # Rejection indicators (checked against contact messages)
    REJECTION_INDICATORS = [
        "не интересно",
        "не нужно",
        "отказываюсь",
        "спам",
        "не пишите",
        "не звоните",
        "удалите",
        "отпишите",
        "не актуально",
        "не подходит",
        "передумал",
        "нет, спасибо",
        "не буду",
        "отстаньте",
    ]

    # Disengagement timeout in hours (7 days)
    DISENGAGEMENT_HOURS = 168

    def __init__(self, llm_client: Optional["UnifiedLLMClient"] = None):
        """
        Initialize outcome tracker.

        Args:
            llm_client: Optional LLM client for advanced rejection detection
        """
        self.llm = llm_client

    async def detect_outcome(
        self,
        contact_id: int,
        state: "ConversationState",
        messages: List[Dict[str, str]],
        channel_id: str = ""
    ) -> OutcomeResult:
        """
        Detect conversation outcome based on state and messages.

        Args:
            contact_id: Telegram user ID
            state: Current conversation state
            messages: List of conversation messages [{"role": "user/assistant", "content": "..."}]
            channel_id: Channel ID for logging

        Returns:
            OutcomeResult with outcome type and details
        """
        # 1. Check state flags first (most reliable)
        if state.call_scheduled:
            return OutcomeResult(
                outcome="call_scheduled",
                confidence=1.0,
                details={"source": "state_flag"},
                detection_method="state_flag"
            )

        # 2. Check for explicit success keywords in recent assistant messages
        success_result = self._check_success_keywords(messages)
        if success_result:
            return success_result

        # 3. Check for disengagement (timeout)
        disengagement_result = self._check_disengagement(state)
        if disengagement_result:
            return disengagement_result

        # 4. Check for explicit rejection keywords
        rejection_result = self._check_rejection_keywords(messages)
        if rejection_result:
            return rejection_result

        # 5. Use LLM for subtle rejection detection (if available)
        if self.llm and len(messages) >= 3:
            llm_result = await self._detect_rejection_llm(messages)
            if llm_result:
                return llm_result

        # 6. Default: ongoing
        return OutcomeResult(
            outcome="ongoing",
            confidence=1.0,
            details={},
            detection_method="default"
        )

    def _check_success_keywords(self, messages: List[Dict[str, str]]) -> Optional[OutcomeResult]:
        """Check for success keywords in assistant messages."""
        # Get last 5 assistant messages
        assistant_messages = [
            m["content"].lower() for m in messages
            if m.get("role") == "assistant"
        ][-5:]

        for msg in assistant_messages:
            for indicator in self.SUCCESS_INDICATORS:
                if indicator in msg:
                    return OutcomeResult(
                        outcome="call_scheduled",
                        confidence=0.85,
                        details={"matched_indicator": indicator},
                        detection_method="keyword"
                    )
        return None

    def _check_disengagement(self, state: "ConversationState") -> Optional[OutcomeResult]:
        """Check for disengagement based on timeout."""
        # Only check if call was offered
        if not state.call_offered:
            return None

        # Check time since last interaction
        if state.last_interaction:
            try:
                last_dt = datetime.fromisoformat(state.last_interaction)
                hours_since = (datetime.now() - last_dt).total_seconds() / 3600

                if hours_since >= self.DISENGAGEMENT_HOURS:
                    return OutcomeResult(
                        outcome="disengaged",
                        confidence=0.9,
                        details={
                            "hours_since_last_interaction": round(hours_since, 1),
                            "threshold_hours": self.DISENGAGEMENT_HOURS
                        },
                        detection_method="timeout"
                    )
            except (ValueError, TypeError):
                pass

        return None

    def _check_rejection_keywords(self, messages: List[Dict[str, str]]) -> Optional[OutcomeResult]:
        """Check for rejection keywords in user messages."""
        # Get last 5 user messages
        user_messages = [
            m["content"].lower() for m in messages
            if m.get("role") == "user"
        ][-5:]

        for msg in user_messages:
            for indicator in self.REJECTION_INDICATORS:
                if indicator in msg:
                    return OutcomeResult(
                        outcome="declined",
                        confidence=0.8,
                        details={"matched_indicator": indicator},
                        detection_method="keyword"
                    )
        return None

    async def _detect_rejection_llm(self, messages: List[Dict[str, str]]) -> Optional[OutcomeResult]:
        """Use LLM to detect subtle rejection patterns."""
        if not self.llm:
            return None

        # Get last 5 user messages
        user_messages = [
            m["content"] for m in messages
            if m.get("role") == "user"
        ][-5:]

        if not user_messages:
            return None

        prompt = f"""Analyze these messages from a potential client.
Determine if they are rejecting further communication or showing disinterest.

Messages:
{chr(10).join(f"- {msg}" for msg in user_messages)}

Return ONLY valid JSON:
{{
    "is_rejection": true or false,
    "confidence": 0.0 to 1.0,
    "reason": "brief explanation"
}}

Only return is_rejection=true if you are confident (>0.7) of explicit rejection or strong disinterest.
Cold/neutral responses are NOT rejections."""

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You analyze messages and return JSON. Be conservative - only flag clear rejections."},
                {"role": "user", "content": prompt}
            ])

            # Parse JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                if data.get("is_rejection") and data.get("confidence", 0) > 0.7:
                    return OutcomeResult(
                        outcome="declined",
                        confidence=data["confidence"],
                        details={"llm_reason": data.get("reason", "")},
                        detection_method="llm"
                    )
        except Exception as e:
            logger.warning(f"[OutcomeTracker] LLM rejection detection failed: {e}")

        return None

    def check_success_in_message(self, message: str) -> bool:
        """Quick check if a message contains success indicators."""
        message_lower = message.lower()
        return any(ind in message_lower for ind in self.SUCCESS_INDICATORS)

    def check_rejection_in_message(self, message: str) -> bool:
        """Quick check if a message contains rejection indicators."""
        message_lower = message.lower()
        return any(ind in message_lower for ind in self.REJECTION_INDICATORS)
