"""Tests for JobAnalyzer.

The primary filtering criteria is: has personal telegram contact.
Salary and paid ad checks are no longer the main filters.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.job_analyzer import JobAnalyzer, JobAnalysisResult


class TestJobAnalyzerContactExtraction:
    """Test contact extraction - the main filtering criteria."""

    @pytest.fixture
    def analyzer(self):
        return JobAnalyzer(providers_config={}, min_salary_rub=70_000)

    def test_contact_extraction_simple(self, analyzer):
        """Test contact username extraction."""
        text = "Писать сюда: @recruiter_ivan"
        result = analyzer._extract_contact_regex(text)
        assert result == "@recruiter_ivan"

    def test_contact_extraction_filters_channels(self, analyzer):
        """Test that channel names are filtered."""
        text = "@news_channel - наш канал. Резюме: @hr_manager"
        result = analyzer._extract_contact_regex(text)
        assert result == "@hr_manager"  # Not @news_channel

    def test_contact_extraction_filters_bots(self, analyzer):
        """Test that bot names are filtered."""
        text = "Используйте @support_bot для вопросов. HR: @anna_hr"
        result = analyzer._extract_contact_regex(text)
        assert result == "@anna_hr"

    def test_contact_extraction_filters_jobs_channels(self, analyzer):
        """Test filtering of job channel names."""
        text = "Канал @jobs_moscow. Связь: @recruiter123"
        result = analyzer._extract_contact_regex(text)
        assert result == "@recruiter123"

    def test_contact_extraction_none(self, analyzer):
        """Test when no contact found."""
        text = "Ищем разработчика. Пишите в личку."
        result = analyzer._extract_contact_regex(text)
        assert result is None


class TestJobAnalyzerRegex:
    """Test regex-based fallback analysis."""

    @pytest.fixture
    def analyzer(self):
        return JobAnalyzer(providers_config={}, min_salary_rub=70_000)

    def test_vacancy_with_contact_passes(self, analyzer):
        """Test that vacancy with personal contact passes."""
        text = """
        Ищем Senior Python разработчика
        Зарплата: 200000 руб
        Требования: Python, Django, PostgreSQL
        Писать: @hr_manager
        """
        result = analyzer._analyze_with_regex(text)
        assert result.is_relevant
        assert result.contact_username == "@hr_manager"

    def test_vacancy_without_contact_rejected(self, analyzer):
        """Test that vacancy without personal contact is rejected."""
        text = """
        Ищем Senior Python разработчика
        Зарплата: 200000 руб
        Откликнуться: https://forms.google.com/apply
        """
        result = analyzer._analyze_with_regex(text)
        assert not result.is_relevant
        assert "No personal Telegram contact" in result.rejection_reason

    def test_vacancy_with_bot_only_rejected(self, analyzer):
        """Test that vacancy with only bot contact is rejected."""
        text = """
        Ищем разработчика
        Для отклика пишите: @hiring_bot
        """
        result = analyzer._analyze_with_regex(text)
        assert not result.is_relevant
        assert "bot" in result.rejection_reason.lower()

    def test_vacancy_low_salary_with_contact_passes(self, analyzer):
        """Test that vacancy with low salary but contact still passes.

        Salary is no longer a filtering criteria.
        """
        text = "Вакансия: менеджер. Зарплата 30000 руб. Писать: @hr_test"
        result = analyzer._analyze_with_regex(text)
        assert result.is_relevant  # Passes because has contact
        assert result.contact_username == "@hr_test"

    def test_paid_ad_with_contact_passes(self, analyzer):
        """Test that even paid ads pass if they have a personal contact.

        The goal is to extract contacts, not filter paid ads.
        """
        text = "Реклама! Заработок от 100000 рублей! Писать: @hr_offer"
        result = analyzer._analyze_with_regex(text)
        assert result.is_relevant  # Passes because has contact
        assert result.contact_username == "@hr_offer"

    def test_fallback_flag_set(self, analyzer):
        """Test that fallback flag is set in regex analysis."""
        text = "Вакансия Python разработчик, 150000 руб, @hr_test"
        result = analyzer._analyze_with_regex(text)
        assert result.used_fallback is True


class TestJobAnalyzerBotDetection:
    """Test bot username detection."""

    @pytest.fixture
    def analyzer(self):
        return JobAnalyzer(providers_config={}, min_salary_rub=70_000)

    def test_detect_bot_from_tme_link(self, analyzer):
        """Test bot detection from t.me link."""
        text = "Откликнуться через бота: t.me/hiring_bot"
        contact, contact_type, bot_username = analyzer.detect_contact_type(text)
        assert contact_type == "bot"
        assert bot_username == "@hiring_bot"

    def test_detect_bot_from_username(self, analyzer):
        """Test bot detection from @username."""
        text = "Писать боту @support_bot"
        contact, contact_type, bot_username = analyzer.detect_contact_type(text)
        assert contact_type == "bot"
        assert bot_username == "@support_bot"

    def test_personal_contact_preferred_over_bot(self, analyzer):
        """Test that personal contact is extracted when both present."""
        text = "Бот @apply_bot. HR контакт: @maria_hr"
        # The _extract_contact_regex should find @maria_hr
        result = analyzer._extract_contact_regex(text)
        assert result == "@maria_hr"


class TestJobAnalyzerLLM:
    """Test LLM-based analysis."""

    @pytest.fixture
    def analyzer(self):
        analyzer = JobAnalyzer(providers_config={}, min_salary_rub=70_000)
        analyzer._initialized = True
        analyzer.llm = MagicMock()
        return analyzer

    @pytest.mark.asyncio
    async def test_llm_analysis_with_contact(self, analyzer):
        """Test LLM analysis when contact is found."""
        mock_response = '''
        {
            "contact_username": "@hr_test",
            "reason": "Found personal contact for job application"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Test job posting with @hr_test")

        assert result.is_relevant
        assert result.contact_username == "@hr_test"
        assert result.used_fallback is False

    @pytest.mark.asyncio
    async def test_llm_analysis_no_contact(self, analyzer):
        """Test LLM analysis when no contact found."""
        mock_response = '''
        {
            "contact_username": null,
            "reason": "No personal Telegram contact found"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Vacancy without contact")

        assert not result.is_relevant
        assert result.contact_username is None
        assert "No personal Telegram contact" in result.rejection_reason

    @pytest.mark.asyncio
    async def test_llm_detects_bot_contact(self, analyzer):
        """Test that LLM-extracted bot contacts are rejected."""
        mock_response = '''
        {
            "contact_username": "@hiring_bot",
            "reason": "Found bot for applications"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Apply via @hiring_bot")

        assert not result.is_relevant
        assert result.contact_type == "bot"
        assert "bot" in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_llm_fallback_on_error(self, analyzer):
        """Test fallback to regex when LLM fails."""
        analyzer.llm.achat = AsyncMock(side_effect=Exception("LLM error"))

        text = "Вакансия: разработчик @hr_anna 100000 руб"
        result = await analyzer.analyze(text)

        assert result.used_fallback
        assert result.contact_username == "@hr_anna"
        assert result.is_relevant

    @pytest.mark.asyncio
    async def test_llm_malformed_json_fallback(self, analyzer):
        """Test fallback when LLM returns malformed JSON."""
        mock_response = "This is not valid JSON at all"

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        text = "Вакансия Python 150000 руб @recruiter"
        result = await analyzer.analyze(text)

        # Should fallback to regex
        assert result.used_fallback
        assert result.is_relevant
        assert result.contact_username == "@recruiter"

    @pytest.mark.asyncio
    async def test_contact_normalization(self, analyzer):
        """Test that contact is normalized with @ prefix."""
        mock_response = '''
        {
            "contact_username": "hr_without_at",
            "reason": "Found contact"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Test")

        # Should add @ prefix
        assert result.contact_username == "@hr_without_at"


class TestJobAnalysisResult:
    """Test JobAnalysisResult dataclass."""

    def test_relevant_when_has_contact(self):
        """Test is_relevant is True when has personal contact."""
        result = JobAnalysisResult(
            is_real_job=True,
            is_salary_ok=True,
            is_relevant=True,
            contact_username="@hr_test",
            contact_type="user",
        )
        assert result.is_relevant

    def test_not_relevant_when_no_contact(self):
        """Test is_relevant is False when no contact."""
        result = JobAnalysisResult(
            is_real_job=True,
            is_salary_ok=True,
            is_relevant=False,
            contact_username=None,
            contact_type="none",
            rejection_reason="No personal Telegram contact found"
        )
        assert not result.is_relevant

    def test_not_relevant_when_bot_contact(self):
        """Test is_relevant is False for bot contact."""
        result = JobAnalysisResult(
            is_real_job=True,
            is_salary_ok=True,
            is_relevant=False,
            contact_username=None,
            contact_type="bot",
            bot_username="@hiring_bot",
            rejection_reason="Only bot contact found"
        )
        assert not result.is_relevant
