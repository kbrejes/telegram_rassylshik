"""Tests for JobAnalyzer."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.job_analyzer import JobAnalyzer, JobAnalysisResult


class TestJobAnalyzerRegex:
    """Test regex-based fallback analysis."""

    @pytest.fixture
    def analyzer(self):
        return JobAnalyzer(providers_config={}, min_salary_rub=70_000)

    def test_salary_extraction_rub_simple(self, analyzer):
        """Test salary extraction in rubles - simple format."""
        text = "Зарплата: 150000 руб/мес"
        result = analyzer._extract_salary_regex(text)
        assert result == 150000

    def test_salary_extraction_rub_with_spaces(self, analyzer):
        """Test salary extraction with spaces."""
        text = "Оплата 150 000 рублей в месяц"
        result = analyzer._extract_salary_regex(text)
        assert result == 150000

    def test_salary_extraction_rub_from_keyword(self, analyzer):
        """Test salary extraction from 'от' keyword."""
        text = "от 120000 руб"
        result = analyzer._extract_salary_regex(text)
        assert result == 120000

    def test_salary_extraction_usd(self, analyzer):
        """Test salary extraction in dollars."""
        text = "Salary: $2000/month"
        result = analyzer._extract_salary_regex(text)
        assert result == 200000  # 2000 * 100

    def test_salary_extraction_usd_after_amount(self, analyzer):
        """Test salary with $ after amount."""
        text = "Оплата 1500$ в месяц"
        result = analyzer._extract_salary_regex(text)
        assert result == 150000

    def test_salary_extraction_none(self, analyzer):
        """Test when no salary found."""
        text = "Ищем разработчика на проект"
        result = analyzer._extract_salary_regex(text)
        assert result is None

    def test_paid_ad_detection_reklama(self, analyzer):
        """Test paid advertisement detection - 'реклама'."""
        text = "Реклама. Курс по заработку от 100000 рублей!"
        result = analyzer._analyze_with_regex(text)
        assert not result.is_real_job
        assert not result.is_relevant

    def test_paid_ad_detection_mlm(self, analyzer):
        """Test MLM detection."""
        text = "Приглашаем в сетевой маркетинг! Заработок без вложений!"
        result = analyzer._analyze_with_regex(text)
        assert not result.is_real_job

    def test_paid_ad_detection_easy_money(self, analyzer):
        """Test easy money detection."""
        text = "Легкие деньги! Работа 2 часа в день!"
        result = analyzer._analyze_with_regex(text)
        assert not result.is_real_job

    def test_real_job_passes(self, analyzer):
        """Test that real job ad passes."""
        text = """
        Ищем Senior Python разработчика
        Зарплата: 200000 руб
        Требования: Python, Django, PostgreSQL
        Писать: @hr_manager
        """
        result = analyzer._analyze_with_regex(text)
        assert result.is_real_job
        assert result.is_salary_ok
        assert result.is_relevant

    def test_low_salary_rejection(self, analyzer):
        """Test rejection of low salary jobs."""
        text = "Вакансия: менеджер. Зарплата 50000 руб"
        result = analyzer._analyze_with_regex(text)
        assert result.is_real_job  # It's a real job
        assert not result.is_salary_ok  # But salary too low
        assert not result.is_relevant
        assert "too low" in result.rejection_reason.lower()

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

    def test_fallback_flag_set(self, analyzer):
        """Test that fallback flag is set in regex analysis."""
        text = "Вакансия Python разработчик, 150000 руб, @hr_test"
        result = analyzer._analyze_with_regex(text)
        assert result.used_fallback is True


class TestJobAnalyzerLLM:
    """Test LLM-based analysis."""

    @pytest.fixture
    def analyzer(self):
        analyzer = JobAnalyzer(providers_config={}, min_salary_rub=70_000)
        analyzer._initialized = True
        analyzer.llm = MagicMock()
        return analyzer

    @pytest.mark.asyncio
    async def test_llm_analysis_success(self, analyzer):
        """Test successful LLM analysis."""
        mock_response = '''
        {
            "is_real_job": true,
            "is_paid_ad": false,
            "salary_monthly_rub": 120000,
            "contact_username": "@hr_test",
            "summary": "Real job posting for Python developer"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Test job posting")

        assert result.is_relevant
        assert result.contact_username == "@hr_test"
        assert result.salary_monthly_rub == 120000
        assert result.used_fallback is False

    @pytest.mark.asyncio
    async def test_llm_detects_paid_ad(self, analyzer):
        """Test LLM detection of paid advertisement."""
        mock_response = '''
        {
            "is_real_job": false,
            "is_paid_ad": true,
            "paid_ad_reason": "This is a course promotion, not a job",
            "salary_monthly_rub": null,
            "contact_username": null,
            "summary": "Paid advertisement for online course"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Курс по заработку!")

        assert not result.is_relevant
        assert not result.is_real_job
        assert "course" in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_llm_low_salary_rejection(self, analyzer):
        """Test LLM rejection of low salary."""
        mock_response = '''
        {
            "is_real_job": true,
            "is_paid_ad": false,
            "salary_monthly_rub": 50000,
            "contact_username": "@hr_cheap",
            "summary": "Real job but low salary"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Вакансия с низкой зп")

        assert not result.is_relevant
        assert result.is_real_job  # It IS a real job
        assert not result.is_salary_ok  # But salary is low
        assert result.salary_monthly_rub == 50000
        assert "too low" in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_llm_fallback_on_error(self, analyzer):
        """Test fallback to regex when LLM fails."""
        analyzer.llm.achat = AsyncMock(side_effect=Exception("LLM error"))

        text = "Вакансия: разработчик @dev_contact 100000 руб"
        result = await analyzer.analyze(text)

        assert result.used_fallback
        assert result.contact_username == "@dev_contact"
        assert result.is_relevant  # 100k > 70k threshold

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

    @pytest.mark.asyncio
    async def test_contact_normalization(self, analyzer):
        """Test that contact is normalized with @ prefix."""
        mock_response = '''
        {
            "is_real_job": true,
            "is_paid_ad": false,
            "salary_monthly_rub": 150000,
            "contact_username": "hr_without_at",
            "summary": "Real job"
        }
        '''

        analyzer.llm.achat = AsyncMock(return_value=mock_response)
        result = await analyzer.analyze("Test")

        # Should add @ prefix
        assert result.contact_username == "@hr_without_at"


class TestJobAnalysisResult:
    """Test JobAnalysisResult dataclass."""

    def test_relevant_when_both_true(self):
        """Test is_relevant is True when both conditions met."""
        result = JobAnalysisResult(
            is_real_job=True,
            is_salary_ok=True,
            is_relevant=True,
        )
        assert result.is_relevant

    def test_not_relevant_when_paid_ad(self):
        """Test is_relevant is False for paid ads."""
        result = JobAnalysisResult(
            is_real_job=False,
            is_salary_ok=True,
            is_relevant=False,
            rejection_reason="Paid advertisement"
        )
        assert not result.is_relevant

    def test_not_relevant_when_low_salary(self):
        """Test is_relevant is False for low salary."""
        result = JobAnalysisResult(
            is_real_job=True,
            is_salary_ok=False,
            is_relevant=False,
            rejection_reason="Salary too low"
        )
        assert not result.is_relevant
