"""
LLM-based Job Ad Analyzer

Analyzes job postings to:
1. Filter paid advertisements (not real job vacancies)
2. Filter low-salary jobs (below threshold)
3. Extract contact information intelligently
"""

import json
import re
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from ai_conversation.llm_client import UnifiedLLMClient

logger = logging.getLogger(__name__)


@dataclass
class JobAnalysisResult:
    """Result of job ad analysis."""
    is_real_job: bool  # Not a paid advertisement
    is_salary_ok: bool  # Salary >= min threshold
    is_relevant: bool  # Combined: is_real_job AND is_salary_ok

    # Extracted data
    contact_username: Optional[str] = None  # @username or None
    contact_type: str = "user"  # "user", "bot", or "none"
    bot_username: Optional[str] = None  # Bot username if contact is a bot
    salary_monthly_rub: Optional[int] = None  # Normalized monthly salary in RUB

    # Reasoning
    rejection_reason: Optional[str] = None
    analysis_summary: str = ""

    # Fallback flag
    used_fallback: bool = False  # True if LLM failed and regex was used


class JobAnalyzer:
    """
    LLM-powered job ad analyzer with regex fallback.

    Usage:
        analyzer = JobAnalyzer(llm_providers_config)
        await analyzer.initialize()

        result = await analyzer.analyze(message_text)
        if result.is_relevant:
            contact = result.contact_username
    """

    USD_TO_RUB = 100  # Approximate conversion rate

    def __init__(
        self,
        providers_config: Dict[str, Any],
        min_salary_rub: int = 70_000,
        provider_name: str = "groq",
        model: Optional[str] = None,
        require_tg_contact: bool = False,
    ):
        self.providers_config = providers_config
        self.min_salary_rub = min_salary_rub
        self.provider_name = provider_name
        self.model = model
        self.require_tg_contact = require_tg_contact
        self.llm: Optional[UnifiedLLMClient] = None
        self._initialized = False

    async def initialize(self):
        """Initialize LLM client."""
        if self._initialized:
            return

        try:
            self.llm = UnifiedLLMClient.from_config(
                providers_config=self.providers_config,
                provider_name=self.provider_name,
                model=self.model,
                temperature=0.1,  # Low temperature for consistent analysis
                max_tokens=512,
            )
            self._initialized = True
            logger.info(f"[JobAnalyzer] Initialized with {self.provider_name}, min_salary={self.min_salary_rub}")
        except Exception as e:
            logger.error(f"[JobAnalyzer] Init failed: {e}")
            raise

    async def analyze(self, text: str) -> JobAnalysisResult:
        """
        Analyze job posting text.

        Args:
            text: Job posting text

        Returns:
            JobAnalysisResult with analysis details
        """
        if not self._initialized:
            await self.initialize()

        try:
            return await self._analyze_with_llm(text)
        except Exception as e:
            logger.warning(f"[JobAnalyzer] LLM failed, using fallback: {e}")
            return self._analyze_with_regex(text)

    async def _analyze_with_llm(self, text: str) -> JobAnalysisResult:
        """Analyze using LLM."""
        prompt = self._build_prompt(text)

        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": prompt},
        ]

        response = await self.llm.achat(messages)
        return self._parse_llm_response(response, text)

    def _get_system_prompt(self) -> str:
        """Get system prompt - custom if set, otherwise default."""
        from web.utils import load_filter_prompt

        custom_prompt = load_filter_prompt()
        if custom_prompt:
            # Replace placeholder with actual min salary value
            return custom_prompt.replace("{min_salary}", str(self.min_salary_rub))

        return self._get_default_system_prompt()

    def _is_using_custom_prompt(self) -> bool:
        """Check if a custom prompt is configured."""
        from web.utils import load_filter_prompt
        return load_filter_prompt() is not None

    def _get_default_system_prompt(self) -> str:
        """Get the hardcoded default system prompt."""
        return """You are a telegram contact extractor. Your ONLY task is to find a personal Telegram username (@username) in job postings.

RULES:
1. Look for @username of a PERSON (HR manager, recruiter, hiring manager) - someone you can write to apply for the job
2. The contact is usually near words: "писать", "резюме", "связь", "контакт", "HR", "обращаться", "откликнуться", "написать", "отклик", "телеграм"

IGNORE and DO NOT return:
- Channel/group usernames containing: job, jobs, work, vacancy, career, remote, marketing, smm, digital, channel, chat, group, news, hire, hiring, freelance, вакансии, работа
- Bot usernames (ending with "bot" or "_bot")
- t.me/ links to channels or groups (not personal profiles)
- Any @username that doesn't look like a person's name

Personal usernames usually look like:
- Real names: @ivan_petrov, @anna_hr, @recruiter_kate, @maria_hiring
- Name + role: @hr_anna, @ceo_john, @pm_alex
- Short names: @Ritttka1, @seohr, @kbrejes

If NO personal contact found, return null.
If MULTIPLE usernames found, pick the one that looks most like a person's real name or HR contact.

Respond ONLY with valid JSON, no other text."""

    def _build_prompt(self, text: str) -> str:
        return f'''Extract the personal Telegram contact from this job posting:

---
{text[:2000]}
---

JSON response:
{{
    "contact_username": "@username" or null,
    "reason": "brief explanation of why this contact was chosen or why no contact found"
}}'''

    def _parse_llm_response(self, response: str, original_text: str) -> JobAnalysisResult:
        """Parse LLM JSON response.

        The primary filtering criteria is: has personal telegram contact.
        Salary and paid ad checks are secondary/optional.
        """
        try:
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                raise ValueError("No JSON found in response")

            data = json.loads(json_match.group())

            # Normalize contact from LLM
            contact = data.get("contact_username")
            if contact and not contact.startswith("@"):
                contact = f"@{contact}"

            # Check if LLM-extracted contact is a bot
            is_bot_contact = False
            if contact:
                contact_lower = contact.lower()
                if contact_lower.endswith('bot') or '_bot' in contact_lower:
                    is_bot_contact = True
                    logger.info(f"[JobAnalyzer] LLM extracted bot username: {contact}")

            # Detect bot from original text (fallback check)
            _, text_contact_type, bot_username = self.detect_contact_type(original_text)

            # Determine final contact type
            if is_bot_contact:
                contact_type = "bot"
                bot_username = contact
                contact = None  # Don't use bot as personal contact
            elif contact:
                contact_type = "user"
            elif text_contact_type == "bot":
                contact_type = "bot"
            else:
                contact_type = "none"

            # PRIMARY CRITERIA: Has personal telegram contact
            has_personal_contact = contact is not None and contact_type == "user"

            # Build result - is_relevant based on having personal contact
            rejection_reason = None
            if not has_personal_contact:
                if contact_type == "bot":
                    rejection_reason = f"Only bot contact found: {bot_username}"
                else:
                    rejection_reason = data.get("reason", "No personal Telegram contact found")

            result = JobAnalysisResult(
                is_real_job=True,  # We don't check this anymore
                is_salary_ok=True,  # We don't check salary anymore
                is_relevant=has_personal_contact,  # ONLY criteria: has personal TG contact
                contact_username=contact,
                contact_type=contact_type,
                bot_username=bot_username,
                salary_monthly_rub=None,  # Not extracting salary
                rejection_reason=rejection_reason,
                analysis_summary=data.get("reason", ""),
                used_fallback=False,
            )

            if result.is_relevant:
                logger.info(f"[JobAnalyzer] Vacancy PASSED - personal contact: {contact}")
            else:
                logger.info(f"[JobAnalyzer] Vacancy REJECTED - {rejection_reason}")

            return result

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"[JobAnalyzer] Failed to parse LLM response: {e}")
            # Fallback to regex
            contact = self._extract_contact_from_response(response)
            result = self._analyze_with_regex(original_text)
            if contact and not contact.lower().endswith('bot'):
                result.contact_username = contact
                result.contact_type = "user"
                result.is_relevant = True
            return result

    def _extract_contact_from_response(self, response: str) -> Optional[str]:
        """Try to extract contact even from malformed response."""
        match = re.search(r'"contact_username":\s*"(@[a-zA-Z0-9_]{5,32})"', response)
        return match.group(1) if match else None

    def _analyze_with_regex(self, text: str) -> JobAnalysisResult:
        """Fallback regex-based analysis.

        Uses the same criteria as LLM: has personal telegram contact.
        """
        # Contact extraction with bot detection
        contact, contact_type, bot_username = self.detect_contact_type(text)

        # PRIMARY CRITERIA: Has personal telegram contact
        has_personal_contact = contact is not None and contact_type == "user"

        rejection_reason = None
        if not has_personal_contact:
            if contact_type == "bot":
                rejection_reason = f"Only bot contact found: {bot_username}"
            else:
                rejection_reason = "No personal Telegram contact found (regex)"

        result = JobAnalysisResult(
            is_real_job=True,  # We don't check this anymore
            is_salary_ok=True,  # We don't check salary anymore
            is_relevant=has_personal_contact,  # ONLY criteria: has personal TG contact
            contact_username=contact,
            contact_type=contact_type,
            bot_username=bot_username,
            salary_monthly_rub=None,  # Not extracting salary
            rejection_reason=rejection_reason,
            analysis_summary="Analyzed with regex fallback",
            used_fallback=True,
        )

        if result.is_relevant:
            logger.info(f"[JobAnalyzer] (regex) Vacancy PASSED - personal contact: {contact}")
        else:
            logger.info(f"[JobAnalyzer] (regex) Vacancy REJECTED - {rejection_reason}")

        return result

    def _extract_salary_regex(self, text: str) -> Optional[int]:
        """Extract and normalize salary using regex."""
        text_lower = text.lower()

        # Pattern for RUB amounts (handles spaces and non-breaking spaces)
        rub_patterns = [
            r'(\d[\d\s\u00A0]*\d)\s*(?:₽|руб|rub)',  # 150 000 руб
            r'(?:зп|зарплата|оклад|ставка)[:\s]*(\d[\d\s\u00A0]*)',  # зп: 150000
            r'от\s*(\d[\d\s\u00A0]*)\s*(?:₽|руб|rub)',  # от 150000 руб
        ]

        for pattern in rub_patterns:
            match = re.search(pattern, text_lower)
            if match:
                amount_str = match.group(1).replace(' ', '').replace('\u00A0', '')
                try:
                    amount = int(amount_str)
                    # Normalize hourly to monthly
                    if 'час' in text_lower or '/hour' in text_lower:
                        amount *= 160
                    # Normalize daily to monthly
                    elif '/день' in text_lower or '/day' in text_lower:
                        amount *= 22
                    return amount
                except ValueError:
                    continue

        # Pattern for USD amounts
        usd_patterns = [
            r'\$\s*(\d[\d\s\u00A0,]*)',  # $2000
            r'(\d[\d\s\u00A0,]*)\s*\$',  # 2000$
            r'(\d[\d\s\u00A0,]*)\s*(?:usd|долл)',  # 2000 usd
        ]

        for pattern in usd_patterns:
            match = re.search(pattern, text_lower)
            if match:
                amount_str = match.group(1).replace(' ', '').replace('\u00A0', '').replace(',', '')
                try:
                    amount = int(amount_str)
                    return amount * self.USD_TO_RUB
                except ValueError:
                    continue

        return None

    def _extract_contact_regex(self, text: str) -> Optional[str]:
        """Extract Telegram contact using regex."""
        # Find all @usernames
        matches = re.findall(r'@([a-zA-Z][a-zA-Z0-9_]{4,31})', text)
        if not matches:
            return None

        # Filter out common channel/group patterns
        exclude_patterns = [
            # English
            'channel', 'group', 'chat', 'news', 'bot',
            'jobs', 'job', 'vacancy', 'work', 'career', 'hire', 'hiring',
            'remote', 'junior', 'senior', 'dev', 'developer',
            'marketing', 'design', 'frontend', 'backend',
            'chiefs', 'chief', 'team', 'community', 'official',
            # Russian
            'канал', 'группа', 'чат', 'новости', 'бот',
            'вакансии', 'вакансия', 'работа', 'карьера',
            'удаленка', 'удалёнка', 'джуниор', 'сеньор',
        ]

        for username in matches:
            username_lower = username.lower()
            if not any(ex in username_lower for ex in exclude_patterns):
                return f"@{username}"

        # If all filtered out, don't return channel usernames
        return None

    def _extract_bot_username(self, text: str) -> Optional[str]:
        """Extract bot username from text (t.me/bot_name or @bot_name_bot)."""
        # Pattern for t.me links to bots
        tme_patterns = [
            r't\.me/([a-zA-Z][a-zA-Z0-9_]{4,}[Bb][Oo][Tt])',  # ends with bot
            r't\.me/([a-zA-Z][a-zA-Z0-9_]*_[Bb][Oo][Tt])',  # has _bot suffix
        ]
        for pattern in tme_patterns:
            match = re.search(pattern, text)
            if match:
                return f"@{match.group(1)}"

        # Pattern for @username that is clearly a bot
        bot_patterns = [
            r'@([a-zA-Z][a-zA-Z0-9_]*[Bb][Oo][Tt])\b',  # ends with bot
            r'@([a-zA-Z][a-zA-Z0-9_]*_[Bb][Oo][Tt])\b',  # has _bot suffix
        ]
        for pattern in bot_patterns:
            match = re.search(pattern, text)
            if match:
                return f"@{match.group(1)}"

        return None

    def detect_contact_type(self, text: str) -> tuple[Optional[str], str, Optional[str]]:
        """
        Detect contact type and extract relevant username.

        Returns:
            Tuple of (contact_username, contact_type, bot_username)
            - contact_type: "user", "bot", or "none"
        """
        # First check for bot
        bot_username = self._extract_bot_username(text)
        if bot_username:
            return (None, "bot", bot_username)

        # Then check for user contact
        contact = self._extract_contact_regex(text)
        if contact:
            return (contact, "user", None)

        return (None, "none", None)
