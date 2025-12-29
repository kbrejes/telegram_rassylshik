"""
Style Analyzer

Analyzes user's texting style and generates mirroring instructions.
"""

import re
import logging
from typing import Dict, List, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UserStyle:
    """User's texting style profile."""
    uses_periods: bool = True          # Ends messages with periods
    uses_caps: bool = True             # Starts with capital letters
    uses_commas: bool = True           # Uses commas
    avg_length: int = 50               # Average message length
    formality: str = "neutral"         # formal/neutral/casual
    message_count: int = 0             # Messages analyzed

    def to_dict(self) -> dict:
        return {
            "uses_periods": self.uses_periods,
            "uses_caps": self.uses_caps,
            "uses_commas": self.uses_commas,
            "avg_length": self.avg_length,
            "formality": self.formality,
            "message_count": self.message_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserStyle":
        return cls(
            uses_periods=data.get("uses_periods", True),
            uses_caps=data.get("uses_caps", True),
            uses_commas=data.get("uses_commas", True),
            avg_length=data.get("avg_length", 50),
            formality=data.get("formality", "neutral"),
            message_count=data.get("message_count", 0),
        )


class StyleAnalyzer:
    """
    Analyzes user messages to build style profile.
    Generates prompt instructions for style mirroring.
    """

    def __init__(self):
        self._styles: Dict[int, UserStyle] = {}

    def analyze_message(self, contact_id: int, message: str) -> UserStyle:
        """
        Analyze a message and update user's style profile.

        Args:
            contact_id: Contact identifier
            message: User's message

        Returns:
            Updated UserStyle
        """
        style = self._styles.get(contact_id, UserStyle())
        msg = message.strip()

        if not msg or len(msg) < 3:
            return style

        # Update message count
        style.message_count += 1
        n = style.message_count

        # Analyze periods (running average)
        ends_with_period = msg.endswith('.') or msg.endswith('!')
        if n == 1:
            style.uses_periods = ends_with_period
        else:
            # Weighted average - recent messages matter more
            style.uses_periods = (style.uses_periods * 0.7) + (ends_with_period * 0.3) > 0.5

        # Analyze capitalization
        starts_with_cap = msg[0].isupper() if msg else True
        if n == 1:
            style.uses_caps = starts_with_cap
        else:
            style.uses_caps = (style.uses_caps * 0.7) + (starts_with_cap * 0.3) > 0.5

        # Analyze comma usage
        has_commas = ',' in msg
        if n == 1:
            style.uses_commas = has_commas
        else:
            style.uses_commas = (style.uses_commas * 0.7) + (has_commas * 0.3) > 0.5

        # Update average length
        length = len(msg)
        if n == 1:
            style.avg_length = length
        else:
            style.avg_length = int((style.avg_length * (n - 1) + length) / n)

        # Detect formality
        style.formality = self._detect_formality(msg, style)

        self._styles[contact_id] = style
        return style

    def _detect_formality(self, message: str, style: UserStyle) -> str:
        """Detect formality level from message."""
        msg_lower = message.lower()

        # Casual indicators
        casual_markers = [
            'привет', 'прив', 'хай', 'здаров', 'йо', 'ок', 'окей',
            'норм', 'ща', 'щас', 'чо', 'че', 'ага', 'угу', 'ну',
            'блин', 'типа', 'короч', 'кста', 'имхо', 'лол', 'кек',
            '))', '((', 'хах', 'ахах',
        ]

        # Formal indicators
        formal_markers = [
            'здравствуйте', 'добрый день', 'добрый вечер', 'уважаем',
            'благодарю', 'пожалуйста', 'будьте добры', 'не могли бы',
            'с уважением', 'искренне',
        ]

        casual_count = sum(1 for m in casual_markers if m in msg_lower)
        formal_count = sum(1 for m in formal_markers if m in msg_lower)

        if casual_count > formal_count:
            return "casual"
        elif formal_count > casual_count:
            return "formal"
        return "neutral"

    def get_style(self, contact_id: int) -> UserStyle:
        """Get style profile for contact."""
        return self._styles.get(contact_id, UserStyle())

    def set_style(self, contact_id: int, style: UserStyle):
        """Set style profile for contact."""
        self._styles[contact_id] = style

    def build_style_instructions(self, contact_id: int) -> str:
        """
        Build prompt instructions for style mirroring.

        Args:
            contact_id: Contact identifier

        Returns:
            Style instructions string for system prompt
        """
        style = self.get_style(contact_id)

        # Not enough data yet
        if style.message_count < 2:
            return ""

        instructions = ["СТИЛЬ ТВОИХ СООБЩЕНИЙ (подстройся под клиента):"]

        # Period usage
        if not style.uses_periods:
            instructions.append("- не ставь точку в конце")

        # Caps usage
        if not style.uses_caps:
            instructions.append("- можешь начинать с маленькой буквы")

        # Length mirroring
        if style.avg_length < 25:
            instructions.append("- пиши ОЧЕНЬ коротко, 2-5 слов")
        elif style.avg_length < 50:
            instructions.append("- пиши коротко, одно предложение")
        elif style.avg_length > 100:
            instructions.append("- можешь писать подробнее")

        # Formality
        if style.formality == "casual":
            instructions.append("- пиши неформально, как друг")
            instructions.append("- можно использовать 'ща', 'норм', 'ок'")
        elif style.formality == "formal":
            instructions.append("- сохраняй вежливый тон")

        # General natural style
        instructions.append("- пиши как живой человек в мессенджере")

        if len(instructions) > 1:
            return "\n".join(instructions)
        return ""

    def clear(self, contact_id: int):
        """Clear style data for contact."""
        if contact_id in self._styles:
            del self._styles[contact_id]


# Singleton instance
style_analyzer = StyleAnalyzer()
