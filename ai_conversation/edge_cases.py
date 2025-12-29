"""
Edge Case Handler

Pre-filters messages before sending to LLM.
Handles probes, bot tests, gibberish, etc.
"""

import re
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EdgeCaseResult:
    """Result of edge case analysis."""
    should_respond: bool = True
    hardcoded_response: Optional[str] = None
    is_probe: bool = False
    is_bot_test: bool = False
    is_gibberish: bool = False


# Probe patterns - single characters or minimal input
PROBE_PATTERNS = {
    r'^\.+$',           # Just dots: . .. ...
    r'^\?+$',           # Just question marks: ? ?? ???
    r'^!+$',            # Just exclamation: ! !!
    r'^-+$',            # Just dashes
    r'^\s*$',           # Empty or whitespace
}

# Bot test phrases
BOT_TEST_PHRASES = [
    'ты бот',
    'это бот',
    'ты робот',
    'это робот',
    'автоответчик',
    'автоответ',
    'это автоматический',
    'ты живой',
    'ты человек',
    'живой человек',
    'are you a bot',
    'is this a bot',
]

# Casual responses for bot tests
BOT_TEST_RESPONSES = [
    "нет, живой) чем могу помочь?",
    "живой) что хотели узнать?",
    "не, человек) слушаю",
]

# Gibberish patterns - repetitive characters, keyboard mashing
GIBBERISH_PATTERNS = [
    r'^(.)\1{4,}$',                    # Same char 5+ times: ааааа, ыыыыы
    r'^[йцукенгшщзхъ]{5,}$',           # Russian keyboard top row mash
    r'^[qwerty]{5,}$',                 # English keyboard mash
    r'^[asdf]{4,}$',                   # Home row mash
    r'^[фывап]{4,}$',                  # Russian home row
]


class EdgeCaseHandler:
    """
    Handles edge cases before sending to LLM.

    Returns early responses for probes, bot tests, gibberish.
    """

    def __init__(self):
        self._probe_count: dict = {}  # contact_id -> consecutive probe count
        self._bot_response_idx = 0

    def analyze(
        self,
        contact_id: int,
        message: str,
    ) -> EdgeCaseResult:
        """
        Analyze message for edge cases.

        Args:
            contact_id: Contact identifier
            message: User's message

        Returns:
            EdgeCaseResult with handling instructions
        """
        msg = message.strip().lower()
        result = EdgeCaseResult()

        # 1. Check for probe messages (., ?, etc)
        for pattern in PROBE_PATTERNS:
            if re.match(pattern, msg):
                result.is_probe = True
                break

        if result.is_probe:
            return self._handle_probe(contact_id, result)

        # Reset probe count on normal message
        self._probe_count[contact_id] = 0

        # 2. Check for bot test
        for phrase in BOT_TEST_PHRASES:
            if phrase in msg:
                result.is_bot_test = True
                result.hardcoded_response = self._get_bot_response()
                logger.info(f"[EDGE] Bot test detected for {contact_id}")
                return result

        # 3. Check for gibberish
        for pattern in GIBBERISH_PATTERNS:
            if re.match(pattern, msg):
                result.is_gibberish = True
                result.should_respond = False
                logger.info(f"[EDGE] Gibberish detected for {contact_id}: {msg[:20]}")
                return result

        # 4. Very short nonsense (2-3 random chars)
        if len(msg) <= 3 and not msg.isalpha() and msg not in ['да', 'нет', 'ок', 'но', 'а', 'и']:
            result.is_gibberish = True
            result.should_respond = False
            return result

        return result

    def _handle_probe(self, contact_id: int, result: EdgeCaseResult) -> EdgeCaseResult:
        """Handle probe messages based on count."""
        count = self._probe_count.get(contact_id, 0) + 1
        self._probe_count[contact_id] = count

        if count == 1:
            # First probe - respond minimally
            result.hardcoded_response = "?"
            logger.info(f"[EDGE] First probe from {contact_id}, responding with '?'")
        elif count == 2:
            # Second probe - still respond but shorter
            result.hardcoded_response = "да?"
            logger.info(f"[EDGE] Second probe from {contact_id}")
        else:
            # Third+ probe - don't respond
            result.should_respond = False
            logger.info(f"[EDGE] Multiple probes from {contact_id}, not responding")

        return result

    def _get_bot_response(self) -> str:
        """Get next bot test response (rotate through options)."""
        response = BOT_TEST_RESPONSES[self._bot_response_idx % len(BOT_TEST_RESPONSES)]
        self._bot_response_idx += 1
        return response

    def reset_probe_count(self, contact_id: int):
        """Reset probe count for contact."""
        self._probe_count[contact_id] = 0


# Singleton instance
edge_case_handler = EdgeCaseHandler()
