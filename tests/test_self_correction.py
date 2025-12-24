"""
Tests for the self-correcting prompt system.

Tests:
- Outcome detection
- A/B testing variant assignment
- Statistical significance testing
- Prompt analysis
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from ai_conversation.outcome_tracker import OutcomeTracker, OutcomeResult
from ai_conversation.ab_testing import ABTestingEngine, VariantAssignment, ExperimentStats
from ai_conversation.prompt_analyzer import PromptAnalyzer, ContactLearner


# ============= Outcome Tracker Tests =============

class TestOutcomeTracker:
    """Tests for OutcomeTracker."""

    @pytest.fixture
    def tracker(self):
        """Create tracker without LLM."""
        return OutcomeTracker(llm_client=None)

    @pytest.fixture
    def mock_state(self):
        """Create a mock conversation state."""
        state = MagicMock()
        state.call_scheduled = False
        state.call_offered = False
        state.last_interaction = None
        return state

    @pytest.mark.asyncio
    async def test_call_scheduled_from_state_flag(self, tracker, mock_state):
        """Test detection when state.call_scheduled is True."""
        mock_state.call_scheduled = True

        result = await tracker.detect_outcome(
            contact_id=123,
            state=mock_state,
            messages=[],
            channel_id="test"
        )

        assert result.outcome == "call_scheduled"
        assert result.confidence == 1.0
        assert result.detection_method == "state_flag"

    @pytest.mark.asyncio
    async def test_success_keyword_detection(self, tracker, mock_state):
        """Test detection of success keywords in assistant messages."""
        messages = [
            {"role": "assistant", "content": "Отлично! Созвон назначен на завтра в 15:00"},
            {"role": "user", "content": "Хорошо, жду"}
        ]

        result = await tracker.detect_outcome(
            contact_id=123,
            state=mock_state,
            messages=messages,
            channel_id="test"
        )

        assert result.outcome == "call_scheduled"
        assert result.detection_method == "keyword"
        assert "созвон назначен" in result.details.get("matched_indicator", "")

    @pytest.mark.asyncio
    async def test_rejection_keyword_detection(self, tracker, mock_state):
        """Test detection of rejection keywords in user messages."""
        messages = [
            {"role": "assistant", "content": "Давайте назначим звонок?"},
            {"role": "user", "content": "Нет, спасибо, мне не интересно"}
        ]

        result = await tracker.detect_outcome(
            contact_id=123,
            state=mock_state,
            messages=messages,
            channel_id="test"
        )

        assert result.outcome == "declined"
        assert result.detection_method == "keyword"

    @pytest.mark.asyncio
    async def test_disengagement_timeout(self, tracker, mock_state):
        """Test disengagement detection after timeout."""
        mock_state.call_offered = True
        mock_state.last_interaction = (
            datetime.now() - timedelta(days=8)
        ).isoformat()

        result = await tracker.detect_outcome(
            contact_id=123,
            state=mock_state,
            messages=[],
            channel_id="test"
        )

        assert result.outcome == "disengaged"
        assert result.detection_method == "timeout"
        assert result.details.get("hours_since_last_interaction", 0) >= 168

    @pytest.mark.asyncio
    async def test_ongoing_when_no_signals(self, tracker, mock_state):
        """Test ongoing status when no terminal signals."""
        messages = [
            {"role": "assistant", "content": "Привет! Как дела?"},
            {"role": "user", "content": "Хорошо, спасибо"}
        ]

        result = await tracker.detect_outcome(
            contact_id=123,
            state=mock_state,
            messages=messages,
            channel_id="test"
        )

        assert result.outcome == "ongoing"
        assert result.detection_method == "default"

    def test_check_success_in_message(self, tracker):
        """Test quick success check."""
        assert tracker.check_success_in_message("Созвон назначен!") is True
        assert tracker.check_success_in_message("Okay") is False

    def test_check_rejection_in_message(self, tracker):
        """Test quick rejection check."""
        assert tracker.check_rejection_in_message("Не интересно") is True
        assert tracker.check_rejection_in_message("Интересно!") is False


# ============= A/B Testing Tests =============

class TestABTestingEngine:
    """Tests for ABTestingEngine."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        db = AsyncMock()
        db.get_active_experiment = AsyncMock(return_value=None)
        db.get_active_experiments = AsyncMock(return_value=[])
        db.get_experiment_stats = AsyncMock(return_value={})
        return db

    @pytest.fixture
    def engine(self, mock_db):
        """Create A/B testing engine with mock db."""
        return ABTestingEngine(mock_db)

    @pytest.mark.asyncio
    async def test_no_assignment_when_no_experiment(self, engine, mock_db):
        """Test no assignment when no active experiment."""
        result = await engine.assign_variant(
            contact_id=123,
            prompt_type="phase",
            prompt_name="discovery"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_variant_assignment_deterministic(self, engine, mock_db):
        """Test variant assignment is deterministic."""
        mock_db.get_active_experiment.return_value = {
            "id": 1,
            "name": "test_exp",
            "control_version_id": 10,
            "treatment_version_id": 11,
            "traffic_split": 0.5
        }

        # Same contact should always get same variant
        results = []
        for _ in range(5):
            result = await engine.assign_variant(
                contact_id=123,
                prompt_type="phase",
                prompt_name="discovery"
            )
            results.append(result.variant)

        assert len(set(results)) == 1  # All same variant

    def test_variant_computation_different_contacts(self, engine):
        """Test different contacts can get different variants."""
        variants = set()
        for contact_id in range(1, 100):
            variant = engine._compute_variant(contact_id, experiment_id=1, traffic_split=0.5)
            variants.add(variant)

        # Should have both variants with reasonable probability
        assert len(variants) == 2

    def test_chi_square_test_significant(self, engine):
        """Test chi-square test with significant difference."""
        # Clear difference: 80% vs 20%
        chi_sq, p_value = engine._chi_square_test(
            control_success=20,
            control_fail=80,
            treatment_success=80,
            treatment_fail=20
        )

        assert chi_sq > 0
        assert p_value < 0.05  # Significant

    def test_chi_square_test_not_significant(self, engine):
        """Test chi-square test with no significant difference."""
        # Similar rates: ~50% vs ~50%
        chi_sq, p_value = engine._chi_square_test(
            control_success=50,
            control_fail=50,
            treatment_success=48,
            treatment_fail=52
        )

        assert p_value > 0.05  # Not significant

    def test_chi_square_p_value_bounds(self, engine):
        """Test p-value is bounded properly."""
        # Zero chi-square
        p_val = engine._chi_square_p_value(0)
        assert p_val == 1.0

        # Large chi-square
        p_val = engine._chi_square_p_value(100)
        assert 0 <= p_val < 0.001


# ============= Prompt Analyzer Tests =============

class TestPromptAnalyzer:
    """Tests for PromptAnalyzer."""

    @pytest.fixture
    def mock_llm(self):
        """Create mock LLM client."""
        llm = AsyncMock()
        llm.achat = AsyncMock(return_value='{"is_rejection": false}')
        return llm

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        return AsyncMock()

    @pytest.fixture
    def analyzer(self, mock_llm, mock_db):
        """Create prompt analyzer."""
        return PromptAnalyzer(mock_llm, mock_db)

    @pytest.mark.asyncio
    async def test_analyze_single_failure(self, analyzer, mock_llm):
        """Test single failure analysis."""
        mock_llm.achat.return_value = '''
        {
            "failure_reason": "Too aggressive",
            "conversation_summary": "User was pushed too hard",
            "issues": ["Rushed to call", "Ignored concerns"],
            "turning_point": "Message 3"
        }
        '''

        messages = [
            {"role": "assistant", "content": "Let's schedule a call!"},
            {"role": "user", "content": "I'm not sure..."},
            {"role": "assistant", "content": "Come on, just 15 minutes!"}
        ]

        result = await analyzer.analyze_single_failure(
            messages=messages,
            outcome="declined",
            outcome_details={}
        )

        assert result.failure_type == "declined"
        assert "aggressive" in result.failure_reason.lower()
        assert len(result.identified_issues) == 2

    @pytest.mark.asyncio
    async def test_analyze_failure_patterns_min_failures(self, analyzer):
        """Test pattern analysis requires minimum failures."""
        failures = [{"messages": [], "outcome": "declined"}] * 3

        patterns = await analyzer.analyze_failure_patterns(failures)

        assert patterns == []  # Not enough failures

    def test_format_conversation(self, analyzer):
        """Test conversation formatting."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]

        formatted = analyzer._format_conversation(messages)

        assert "User: Hello" in formatted
        assert "AI: Hi there!" in formatted

    def test_summarize_conversation(self, analyzer):
        """Test conversation summarization."""
        messages = [
            {"role": "user", "content": "I need help"},
            {"role": "assistant", "content": "Sure!"},
            {"role": "user", "content": "Thanks"}
        ]

        summary = analyzer._summarize_conversation(messages)

        assert "2 user msgs" in summary
        assert "1 AI msgs" in summary


# ============= Contact Learner Tests =============

class TestContactLearner:
    """Tests for ContactLearner."""

    @pytest.fixture
    def mock_llm(self):
        """Create mock LLM client."""
        llm = AsyncMock()
        llm.achat = AsyncMock(return_value="developer")
        return llm

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        db = AsyncMock()
        db.get_contact_type_learnings = AsyncMock(return_value=[])
        db.add_contact_type_learning = AsyncMock()
        return db

    @pytest.fixture
    def learner(self, mock_llm, mock_db):
        """Create contact learner."""
        return ContactLearner(mock_llm, mock_db)

    @pytest.mark.asyncio
    async def test_classify_contact_developer(self, learner, mock_llm):
        """Test contact classification as developer."""
        mock_llm.achat.return_value = "developer"

        messages = [
            {"role": "user", "content": "I'm a Python developer"}
        ]

        contact_type = await learner.classify_contact(messages)

        assert contact_type == "developer"

    @pytest.mark.asyncio
    async def test_classify_contact_unknown(self, learner, mock_llm):
        """Test fallback to 'other' for unknown types."""
        mock_llm.achat.return_value = "unknown_type"

        messages = [
            {"role": "user", "content": "Hello"}
        ]

        contact_type = await learner.classify_contact(messages)

        assert contact_type == "other"

    @pytest.mark.asyncio
    async def test_classify_empty_messages(self, learner):
        """Test classification with no messages."""
        contact_type = await learner.classify_contact([])

        assert contact_type == "other"

    @pytest.mark.asyncio
    async def test_learn_from_outcome(self, learner, mock_db, mock_llm):
        """Test learning from successful outcome."""
        mock_llm.achat.return_value = "Being technical helped"

        messages = [
            {"role": "user", "content": "Can you explain the tech stack?"},
            {"role": "assistant", "content": "We use Python and React"}
        ]

        await learner.learn_from_outcome(
            contact_type="developer",
            outcome="call_scheduled",
            messages=messages
        )

        mock_db.add_contact_type_learning.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_type_specific_additions_empty(self, learner, mock_db):
        """Test no additions when no learnings."""
        mock_db.get_contact_type_learnings.return_value = []

        additions = await learner.get_type_specific_prompt_additions("developer")

        assert additions == ""

    @pytest.mark.asyncio
    async def test_get_type_specific_additions_with_learnings(self, learner, mock_db):
        """Test additions with existing learnings."""
        mock_db.get_contact_type_learnings.return_value = [
            {"learning": "Be technical"},
            {"learning": "Mention code quality"}
        ]

        additions = await learner.get_type_specific_prompt_additions("developer")

        assert "developer" in additions
        assert "Be technical" in additions
        assert "Mention code quality" in additions


# ============= Integration Tests =============

class TestSelfCorrectionIntegration:
    """Integration tests for the self-correction system."""

    @pytest.mark.asyncio
    async def test_outcome_to_experiment_flow(self):
        """Test flow from outcome detection to experiment check."""
        # Create mock components
        mock_db = AsyncMock()
        mock_db.get_active_experiment.return_value = {
            "id": 1,
            "name": "test",
            "control_version_id": 10,
            "treatment_version_id": 11,
            "traffic_split": 0.5
        }
        mock_db.get_experiment_stats.return_value = {
            "control_success": 30,
            "control_fail": 70,
            "treatment_success": 60,
            "treatment_fail": 40
        }
        mock_db.get_active_experiments.return_value = [
            {"id": 1, "name": "test"}
        ]

        engine = ABTestingEngine(mock_db)

        # Get assignment
        assignment = await engine.assign_variant(123, "phase", "discovery")
        assert assignment is not None

        # Check stats
        stats = await engine.get_experiment_statistics(1)
        assert stats is not None
        assert stats.treatment_rate > stats.control_rate  # Treatment is winning
