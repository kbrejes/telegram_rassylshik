"""
Phase-based Prompt Builder

Builds dynamic system prompts based on conversation phase.
Loads prompts from prompts/ directory.
"""

import os
import logging
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path

from .state_analyzer import AnalysisResult, ConversationState
from .style_analyzer import style_analyzer, UserStyle

logger = logging.getLogger(__name__)


class PhasePromptBuilder:
    """
    Builds system prompts dynamically based on conversation phase.

    Loads prompt templates from prompts/ directory and combines them
    based on current phase and analysis results.
    """

    # Default prompts (fallback if files don't exist)
    DEFAULT_PROMPTS = {
        "base_context": """Ты работник отдела коммуникации компании [COMPANY_NAME].

[COMPANY_DESCRIPTION]

Общайся дружелюбно, профессионально, но не формально. Пиши короткими сообщениями, как в обычной переписке в мессенджере. Не используй эмодзи слишком часто.""",

        "founders_context": """О нашей команде:
- [FOUNDER_1_NAME] - [FOUNDER_1_ROLE]. [FOUNDER_1_DESCRIPTION]
- [FOUNDER_2_NAME] - [FOUNDER_2_ROLE]. [FOUNDER_2_DESCRIPTION]

Упоминай основателей только когда это уместно - если клиент спрашивает с кем общается или хочет поговорить с руководством.""",

        "phases": {
            "discovery": """ТЕКУЩАЯ ЗАДАЧА: Понять запрос клиента

Сейчас важно:
- Выслушать и понять что нужно клиенту
- Ответить на вопросы
- Дать полезную информацию
- Показать что мы понимаем его задачу

НЕ НУЖНО сейчас:
- Предлагать созвониться (слишком рано)
- Продавать агрессивно
- Давить на клиента""",

            "engagement": """ТЕКУЩАЯ ЗАДАЧА: Углубить интерес

Клиент заинтересован. Сейчас важно:
- Показать экспертизу
- Рассказать о релевантном опыте
- Ответить на детальные вопросы
- Помочь клиенту понять ценность

НЕ НУЖНО сейчас:
- Предлагать созвониться (если клиент сам не просит)
- Давить на решение""",

            "call_ready": """ТЕКУЩАЯ ЗАДАЧА: Предложить созвониться

Это хороший момент МЯГКО предложить созвон. Клиент выразил интерес.

Как предложить:
- "Давайте созвонимся на 15 минут? Так быстрее обсудим детали"
- "Можем созвониться, чтобы я лучше понял задачу. Вот мой календарь: [CALENDAR_LINK]"
- "Если удобно - вот ссылка на запись звонка: [CALENDAR_LINK]"

ВАЖНО:
- Предложи созвон ОДИН раз, мягко
- Не дави если клиент не реагирует
- Если клиент задаёт вопрос - сначала ответь на него, потом предлагай созвон""",

            "call_pending": """ТЕКУЩАЯ ЗАДАЧА: Продолжить диалог, ждать ответа

Ты уже предложил созвониться. ВАЖНО:
- НЕ повторяй предложение созвона
- Продолжай отвечать на вопросы
- Жди реакции клиента на предложение
- Если клиент игнорирует предложение - это нормально, продолжай диалог""",

            "call_declined": """ТЕКУЩАЯ ЗАДАЧА: Работать в переписке

Клиент не хочет созваниваться. Это нормально. ВАЖНО:
- НЕ предлагай созвон снова
- Продолжай помогать в переписке
- Отвечай на вопросы
- Будь полезным

Клиент сам скажет если передумает.""",
        },

        "answer_question_instruction": """ВАЖНО: Клиент задал прямой вопрос. Сначала ответь на него, потом можешь продолжить по задаче.""",
    }

    def __init__(self, prompts_dir: str = "prompts"):
        """
        Initialize prompt builder.

        Args:
            prompts_dir: Directory with prompt files
        """
        self.prompts_dir = Path(prompts_dir)
        self._cache: Dict[str, str] = {}

    def _load_prompt(self, name: str, subdir: str = "") -> str:
        """Load prompt from file or return default."""
        # Check cache
        cache_key = f"{subdir}/{name}" if subdir else name
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try to load from file
        if subdir:
            path = self.prompts_dir / subdir / f"{name}.txt"
        else:
            path = self.prompts_dir / f"{name}.txt"

        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                self._cache[cache_key] = content
                logger.debug(f"[PROMPTS] Loaded {path}")
                return content
            except Exception as e:
                logger.warning(f"[PROMPTS] Error loading {path}: {e}")

        # Fallback to defaults
        if subdir == "phases":
            default = self.DEFAULT_PROMPTS["phases"].get(name, "")
        else:
            default = self.DEFAULT_PROMPTS.get(name, "")

        if default:
            self._cache[cache_key] = default
            logger.debug(f"[PROMPTS] Using default for {cache_key}")

        return default

    def _strip_calendar_link(self, text: str) -> str:
        """Remove calendar link and related instructions from text."""
        import re

        # Remove the calendar link URL
        text = re.sub(r'https://cal\.com/[^\s\n]+', '[ССЫЛКА УЖЕ ОТПРАВЛЕНА]', text)

        # Remove the calendar section from base_context
        text = re.sub(
            r'ССЫЛКА НА КАЛЕНДАРЬ \(для созвонов\):.*?НЕ СПАМЬ ССЫЛКОЙ!.*?\n',
            'КАЛЕНДАРЬ УЖЕ ОТПРАВЛЕН - НЕ ПОВТОРЯЙ ССЫЛКУ!\n',
            text,
            flags=re.DOTALL
        )

        return text

    def _strip_introduction(self, text: str) -> str:
        """Remove introduction instructions from text when already introduced."""
        import re

        # Remove "introduce yourself" instruction from base_context
        text = re.sub(
            r'- В ПЕРВОМ сообщении ОБЯЗАТЕЛЬНО представься[^\n]*\n',
            '- ТЫ УЖЕ ПРЕДСТАВИЛСЯ. НЕ представляйся повторно!\n',
            text
        )

        # Remove "present the agency" from discovery phase
        text = re.sub(
            r'- Кратко представить агентство[^\n]*\n',
            '- ТЫ УЖЕ ПРЕДСТАВИЛСЯ. Продолжай разговор.\n',
            text
        )

        # Change discovery task title if present
        text = re.sub(
            r'ТЕКУЩАЯ ЗАДАЧА: Представиться и понять запрос',
            'ТЕКУЩАЯ ЗАДАЧА: Понять запрос (ты уже представился)',
            text
        )

        return text

    def build_system_prompt(
        self,
        phase: str,
        analysis: Optional[AnalysisResult] = None,
        state: Optional[ConversationState] = None,
        include_founders: bool = False,
        contact_id: Optional[int] = None,
    ) -> str:
        """
        Build complete system prompt for the given phase.

        Args:
            phase: Current conversation phase
            analysis: Analysis result (optional)
            state: Conversation state (optional)
            include_founders: Whether to include founders context
            contact_id: Contact ID for style lookup (optional)

        Returns:
            Complete system prompt
        """
        parts = []

        # Check milestone flags
        calendar_sent = state.calendar_sent if state else False
        introduced = state.introduced if state else False

        # 0. Current date/time context
        now = datetime.now()
        weekdays_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня",
                     "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        date_context = f"Сегодня: {weekdays_ru[now.weekday()]}, {now.day} {months_ru[now.month - 1]} {now.year}, {now.strftime('%H:%M')} МСК"
        parts.append(date_context)

        # 1. Base context (always included)
        base = self._load_prompt("base_context")
        if base:
            # CRITICAL: Remove introduction instructions if already introduced
            if introduced:
                base = self._strip_introduction(base)

            # CRITICAL: Remove calendar link if already sent
            if calendar_sent:
                base = self._strip_calendar_link(base)

            parts.append(base)

        # 2. Founders context (if needed)
        if include_founders or (analysis and analysis.mention_founders):
            founders = self._load_prompt("founders_context")
            if founders:
                parts.append(founders)

        # 3. Phase-specific instructions
        phase_prompt = self._load_prompt(phase, "phases")
        if phase_prompt:
            # CRITICAL: Remove introduction instructions if already introduced
            if introduced:
                phase_prompt = self._strip_introduction(phase_prompt)

            # CRITICAL: Remove calendar link from phase prompt if already sent
            if calendar_sent:
                phase_prompt = self._strip_calendar_link(phase_prompt)

            parts.append(phase_prompt)

        # 4. Answer question instruction (if needed)
        if analysis and analysis.answer_question_first:
            instruction = self._load_prompt("answer_question_instruction")
            if not instruction:
                instruction = self.DEFAULT_PROMPTS["answer_question_instruction"]
            parts.append(instruction)

        # 5. State context with milestones (if available)
        if state:
            parts.append(state.to_context())

        # 6. Style mirroring instructions (if contact_id provided)
        if contact_id:
            style_instructions = style_analyzer.build_style_instructions(contact_id)
            if style_instructions:
                parts.append(style_instructions)

        # 7. CRITICAL: Final instruction to pay attention to conversation history
        history_instruction = """🚨 КРИТИЧЕСКИ ВАЖНО - ПРОЧИТАЙ ВНИМАТЕЛЬНО:

После этого системного промпта идёт ИСТОРИЯ РАЗГОВОРА - это реальные сообщения которые уже были отправлены.

ПЕРЕД ответом:
1. Внимательно прочитай ВСЮ историю разговора
2. Посмотри что ТЫ уже говорил (роль "assistant")
3. НЕ ПОВТОРЯЙ то что уже сказал - ни представление, ни кейсы, ни ссылки
4. Отвечай как ПРОДОЛЖЕНИЕ разговора, а не как новое начало

Если в истории ты уже:
- Представился → НЕ представляйся снова
- Отправил календарь → НЕ отправляй снова
- Рассказал кейс → НЕ рассказывай тот же кейс
- Предложил созвон → НЕ предлагай снова

Веди себя естественно, как человек который ПОМНИТ весь разговор."""
        parts.append(history_instruction)

        return "\n\n---\n\n".join(parts)

    def reload_prompts(self):
        """Clear cache and reload prompts from files."""
        self._cache.clear()
        logger.info("[PROMPTS] Cache cleared")

    def get_available_phases(self) -> list:
        """Get list of available phases."""
        return list(self.DEFAULT_PROMPTS["phases"].keys())


def ensure_prompts_directory(prompts_dir: str = "prompts"):
    """
    Ensure prompts directory exists with default files.

    Creates directory structure and placeholder files if they don't exist.
    """
    prompts_path = Path(prompts_dir)
    phases_path = prompts_path / "phases"

    # Create directories
    prompts_path.mkdir(parents=True, exist_ok=True)
    phases_path.mkdir(parents=True, exist_ok=True)

    # Create placeholder files if they don't exist
    builder = PhasePromptBuilder(prompts_dir)

    # Base files
    for name in ["base_context", "founders_context", "answer_question_instruction"]:
        path = prompts_path / f"{name}.txt"
        if not path.exists():
            content = builder.DEFAULT_PROMPTS.get(name, "")
            if content:
                path.write_text(content, encoding="utf-8")
                logger.info(f"[PROMPTS] Created {path}")

    # Phase files
    for phase, content in builder.DEFAULT_PROMPTS["phases"].items():
        path = phases_path / f"{phase}.txt"
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            logger.info(f"[PROMPTS] Created {path}")

    logger.info(f"[PROMPTS] Directory structure ensured at {prompts_path}")
