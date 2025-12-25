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
    ):
        self.providers_config = providers_config
        self.min_salary_rub = min_salary_rub
        self.provider_name = provider_name
        self.model = model
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
        return f"""You are a job posting analyzer. Analyze Russian/English job ads.

Your task:
1. Determine if this is a REAL job vacancy or a PAID ADVERTISEMENT
2. Extract salary and convert to monthly RUB (minimum acceptable: {self.min_salary_rub} RUB)
3. Find the Telegram contact username for applying

Signs of PAID ADVERTISEMENT (reject these):
- "Реклама", "Партнерский материал", "На правах рекламы"
- Promotes a course, training, or info-product
- "Заработок от X рублей" without specific job duties
- MLM/pyramid scheme indicators
- Too good to be true offers (easy money, no skills needed)
- Recruiting for network marketing or similar schemes

CONTACT EXTRACTION RULES:
- Look for @username of a PERSON (HR, recruiter, hiring manager) to contact for job applications
- IGNORE channel/group usernames - these usually contain words like: job, jobs, work, career, vacancy, remote, junior, senior, dev, marketing, channel, chat, news, group, hire, hiring
- Personal usernames usually look like real names: @ivan_petrov, @hr_anna, @recruiter_kate
- The contact is usually near words: "писать", "резюме", "связь", "контакт", "HR", "обращаться", "откликнуться"
- If no clear personal contact found, return null - DO NOT return channel usernames
- If multiple usernames found, pick the one that looks like a person's name

Respond ONLY with valid JSON, no other text."""

    def _build_prompt(self, text: str) -> str:
        return f'''Analyze this job posting:

---
{text[:2000]}
---

Respond in JSON format:
{{
    "is_real_job": true/false,
    "is_paid_ad": true/false,
    "paid_ad_reason": "reason if paid ad, else null",

    "salary_amount": number or null,
    "salary_currency": "RUB" | "USD" | "EUR" | null,
    "salary_period": "month" | "hour" | "day" | "project" | null,
    "salary_monthly_rub": number or null,

    "contact_username": "@username" or null,

    "summary": "1-sentence analysis"
}}'''

    def _parse_llm_response(self, response: str, original_text: str) -> JobAnalysisResult:
        """Parse LLM JSON response."""
        try:
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                raise ValueError("No JSON found in response")

            data = json.loads(json_match.group())

            is_real_job = data.get("is_real_job", True) and not data.get("is_paid_ad", False)
            salary_monthly = data.get("salary_monthly_rub")

            # Validate salary
            is_salary_ok = True
            if salary_monthly is not None:
                is_salary_ok = salary_monthly >= self.min_salary_rub

            # Build rejection reason
            rejection_reason = None
            if not is_real_job:
                rejection_reason = data.get("paid_ad_reason", "Paid advertisement")
            elif not is_salary_ok:
                rejection_reason = f"Salary too low: {salary_monthly} RUB/month (min: {self.min_salary_rub})"

            # Normalize contact
            contact = data.get("contact_username")
            if contact and not contact.startswith("@"):
                contact = f"@{contact}"

            return JobAnalysisResult(
                is_real_job=is_real_job,
                is_salary_ok=is_salary_ok,
                is_relevant=is_real_job and is_salary_ok,
                contact_username=contact,
                salary_monthly_rub=salary_monthly,
                rejection_reason=rejection_reason,
                analysis_summary=data.get("summary", ""),
                used_fallback=False,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"[JobAnalyzer] Failed to parse LLM response: {e}")
            # Partial parse - try to at least get contact
            contact = self._extract_contact_from_response(response)
            result = self._analyze_with_regex(original_text)
            if contact:
                result.contact_username = contact
            return result

    def _extract_contact_from_response(self, response: str) -> Optional[str]:
        """Try to extract contact even from malformed response."""
        match = re.search(r'"contact_username":\s*"(@[a-zA-Z0-9_]{5,32})"', response)
        return match.group(1) if match else None

    def _analyze_with_regex(self, text: str) -> JobAnalysisResult:
        """Fallback regex-based analysis."""
        text_lower = text.lower()

        # Paid ad detection
        ad_patterns = [
            r'реклама', r'партн[её]рский материал', r'на правах рекламы',
            r'инфопродукт', r'инфо-продукт',
            r'mlm', r'сетевой маркетинг', r'пирамид',
            r'лёгкие деньги', r'легкие деньги', r'легкий заработок',
            r'без опыта.{0,20}от \d+.{0,10}руб',  # "без опыта от 100000 руб" - suspicious
        ]
        is_paid_ad = any(re.search(p, text_lower) for p in ad_patterns)

        # Salary extraction
        salary = self._extract_salary_regex(text)
        is_salary_ok = salary is None or salary >= self.min_salary_rub

        # Contact extraction
        contact = self._extract_contact_regex(text)

        rejection_reason = None
        if is_paid_ad:
            rejection_reason = "Detected as paid advertisement (regex)"
        elif salary and not is_salary_ok:
            rejection_reason = f"Salary too low: {salary} RUB/month (min: {self.min_salary_rub})"

        return JobAnalysisResult(
            is_real_job=not is_paid_ad,
            is_salary_ok=is_salary_ok,
            is_relevant=not is_paid_ad and is_salary_ok,
            contact_username=contact,
            salary_monthly_rub=salary,
            rejection_reason=rejection_reason,
            analysis_summary="Analyzed with regex fallback",
            used_fallback=True,
        )

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
