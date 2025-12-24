"""
Correction Applier - Orchestrates the prompt optimization cycle.

This module ties together:
- Outcome tracking
- Failure analysis
- A/B testing
- Suggestion management

It runs as a background job to continuously improve prompts.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.database import Database
    from ai_conversation.llm_client import UnifiedLLMClient

from ai_conversation.outcome_tracker import OutcomeTracker, OutcomeResult
from ai_conversation.ab_testing import ABTestingEngine
from ai_conversation.prompt_analyzer import PromptAnalyzer, ContactLearner

logger = logging.getLogger(__name__)


@dataclass
class OptimizationCycleResult:
    """Result of an optimization cycle run."""
    experiments_checked: int
    experiments_completed: int
    failures_analyzed: int
    suggestions_generated: int
    new_experiments_created: int
    contact_learnings_added: int


class CorrectionApplier:
    """
    Orchestrates the self-correcting prompt optimization cycle.

    Usage:
        applier = CorrectionApplier(db, llm_client)
        result = await applier.run_optimization_cycle()
    """

    # Minimum failures before generating suggestions
    MIN_FAILURES_FOR_SUGGESTION = 5

    # Auto-deploy suggestions with this confidence or higher
    AUTO_DEPLOY_CONFIDENCE = 0.85

    # Traffic split for new experiments (50% treatment by default)
    DEFAULT_TRAFFIC_SPLIT = 0.5

    def __init__(self, db: "Database", llm: "UnifiedLLMClient"):
        """
        Initialize correction applier.

        Args:
            db: Database instance
            llm: LLM client for analysis
        """
        self.db = db
        self.llm = llm

        # Initialize components
        self.outcome_tracker = OutcomeTracker(llm)
        self.ab_engine = ABTestingEngine(db)
        self.analyzer = PromptAnalyzer(llm, db)
        self.contact_learner = ContactLearner(llm, db)

    async def run_optimization_cycle(self) -> OptimizationCycleResult:
        """
        Run a full optimization cycle.

        Steps:
        1. Check A/B experiments and promote winners
        2. Analyze recent failures
        3. Generate suggestions for underperforming prompts
        4. Create experiments for high-confidence suggestions
        5. Update contact type learnings

        Returns:
            OptimizationCycleResult with cycle statistics
        """
        result = OptimizationCycleResult(
            experiments_checked=0,
            experiments_completed=0,
            failures_analyzed=0,
            suggestions_generated=0,
            new_experiments_created=0,
            contact_learnings_added=0
        )

        try:
            # Step 1: Check and promote A/B experiment winners
            logger.info("[CorrectionApplier] Checking A/B experiments...")
            promotions = await self.ab_engine.check_and_promote_winners()
            result.experiments_completed = len(promotions)

            all_experiments = await self.db.get_active_experiments()
            result.experiments_checked = len(all_experiments)

            for promo in promotions:
                logger.info(
                    f"[CorrectionApplier] Promoted {promo['winner']} in experiment "
                    f"{promo['experiment_name']}: control={promo['control_rate']}, "
                    f"treatment={promo['treatment_rate']}"
                )

            # Step 2: Get recent failures for analysis
            logger.info("[CorrectionApplier] Fetching recent failures...")
            failures = await self._get_recent_failures()
            result.failures_analyzed = len(failures)

            if len(failures) >= self.MIN_FAILURES_FOR_SUGGESTION:
                # Step 3: Analyze failure patterns
                logger.info(f"[CorrectionApplier] Analyzing {len(failures)} failures...")
                patterns = await self.analyzer.analyze_failure_patterns(failures)

                # Step 4: Generate suggestions for prompts with failures
                prompt_failures = self._group_failures_by_prompt(failures)
                for (prompt_type, prompt_name), prompt_fails in prompt_failures.items():
                    if len(prompt_fails) >= 3:  # At least 3 failures
                        suggestion = await self._generate_and_save_suggestion(
                            prompt_type, prompt_name, patterns, prompt_fails
                        )
                        if suggestion:
                            result.suggestions_generated += 1

            # Step 5: Create experiments from high-confidence suggestions
            new_experiments = await self._create_experiments_from_suggestions()
            result.new_experiments_created = len(new_experiments)

            # Step 6: Process contact type learnings
            learnings = await self._process_contact_learnings(failures)
            result.contact_learnings_added = learnings

            logger.info(
                f"[CorrectionApplier] Optimization cycle complete: "
                f"{result.experiments_checked} experiments checked, "
                f"{result.experiments_completed} completed, "
                f"{result.suggestions_generated} suggestions generated, "
                f"{result.new_experiments_created} new experiments created"
            )

        except Exception as e:
            logger.error(f"[CorrectionApplier] Optimization cycle failed: {e}")

        return result

    async def record_conversation_outcome(
        self,
        contact_id: int,
        channel_id: str,
        messages: List[Dict[str, str]],
        state: Any,
        prompt_version_id: Optional[int] = None,
        experiment_id: Optional[int] = None,
        variant: Optional[str] = None
    ) -> OutcomeResult:
        """
        Detect and record a conversation outcome.

        Args:
            contact_id: Telegram user ID
            channel_id: Channel ID
            messages: Conversation messages
            state: ConversationState
            prompt_version_id: ID of prompt version used
            experiment_id: ID of active experiment
            variant: A/B variant ('control' or 'treatment')

        Returns:
            OutcomeResult from detection
        """
        # Detect outcome
        outcome = await self.outcome_tracker.detect_outcome(
            contact_id, state, messages, channel_id
        )

        # Only record terminal outcomes
        if outcome.outcome in ["call_scheduled", "disengaged", "declined"]:
            # Calculate conversation stats
            phases_visited = getattr(state, "phases_visited", [])
            start_time = getattr(state, "created_at", None)
            duration_hours = None
            if start_time:
                try:
                    start_dt = datetime.fromisoformat(start_time)
                    duration_hours = (datetime.now() - start_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass

            # Record to database
            await self.db.record_conversation_outcome(
                contact_id=contact_id,
                channel_id=channel_id,
                outcome=outcome.outcome,
                outcome_details=outcome.details,
                prompt_version_id=prompt_version_id,
                experiment_id=experiment_id,
                variant=variant,
                total_messages=len(messages),
                phases_visited=phases_visited,
                conversation_duration_hours=duration_hours
            )

            logger.info(
                f"[CorrectionApplier] Recorded outcome '{outcome.outcome}' for "
                f"contact {contact_id} (method: {outcome.detection_method})"
            )

            # Learn from outcome
            if messages:
                contact_type = await self.contact_learner.classify_contact(messages)
                await self.contact_learner.learn_from_outcome(
                    contact_type, outcome.outcome, messages
                )

        return outcome

    async def get_variant_for_contact(
        self,
        contact_id: int,
        prompt_type: str,
        prompt_name: str
    ) -> Dict[str, Any]:
        """
        Get the prompt variant to use for a contact.

        Checks for active experiments and assigns variant if applicable.

        Args:
            contact_id: Telegram user ID
            prompt_type: Type of prompt
            prompt_name: Name of prompt

        Returns:
            Dict with version_id, experiment_id, variant, and content
        """
        # Check for active experiment
        assignment = await self.ab_engine.assign_variant(
            contact_id, prompt_type, prompt_name
        )

        if assignment:
            # Get prompt content for assigned version
            version = await self.db.get_prompt_version_by_id(assignment.prompt_version_id)
            return {
                "version_id": assignment.prompt_version_id,
                "experiment_id": assignment.experiment_id,
                "variant": assignment.variant,
                "content": version["content"] if version else None
            }

        # No experiment - get active version
        active_version = await self.db.get_active_prompt_version(prompt_type, prompt_name)
        if active_version:
            return {
                "version_id": active_version["id"],
                "experiment_id": None,
                "variant": None,
                "content": active_version["content"]
            }

        return {
            "version_id": None,
            "experiment_id": None,
            "variant": None,
            "content": None
        }

    async def get_contact_type_additions(
        self,
        messages: List[Dict[str, str]]
    ) -> str:
        """
        Get contact-type-specific prompt additions.

        Args:
            messages: Conversation messages

        Returns:
            Additional prompt text based on contact type
        """
        contact_type = await self.contact_learner.classify_contact(messages)
        return await self.contact_learner.get_type_specific_prompt_additions(contact_type)

    async def _get_recent_failures(
        self,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """Get recent failure outcomes with messages."""
        outcomes = await self.db.get_recent_outcomes(
            outcome_types=["declined", "disengaged"],
            days=days
        )

        # Enrich with messages (from state files or cached)
        enriched = []
        for outcome in outcomes:
            # For now, include what we have
            enriched.append({
                "contact_id": outcome["contact_id"],
                "channel_id": outcome["channel_id"],
                "outcome": outcome["outcome"],
                "outcome_details": outcome.get("outcome_details"),
                "prompt_version_id": outcome.get("prompt_version_id"),
                "messages": [],  # Would need to load from state files
                "created_at": outcome.get("created_at")
            })

        return enriched

    def _group_failures_by_prompt(
        self,
        failures: List[Dict[str, Any]]
    ) -> Dict[tuple, List[Dict]]:
        """Group failures by prompt type and name."""
        grouped = {}

        for failure in failures:
            version_id = failure.get("prompt_version_id")
            if not version_id:
                continue

            # We'd need to look up the prompt info from version_id
            # For now, use a placeholder grouping
            key = ("phase", "unknown")
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(failure)

        return grouped

    async def _generate_and_save_suggestion(
        self,
        prompt_type: str,
        prompt_name: str,
        patterns: List[str],
        failures: List[Dict]
    ) -> Optional[int]:
        """Generate and save a prompt suggestion."""
        # Get current active prompt
        active_version = await self.db.get_active_prompt_version(prompt_type, prompt_name)
        if not active_version:
            return None

        # Generate suggestion
        suggestion = await self.analyzer.generate_prompt_suggestion(
            prompt_type=prompt_type,
            prompt_name=prompt_name,
            current_content=active_version["content"],
            failure_patterns=patterns,
            example_failures=failures
        )

        if suggestion:
            suggestion_id = await self.analyzer.save_suggestion(
                suggestion, active_version["id"]
            )
            logger.info(
                f"[CorrectionApplier] Generated suggestion {suggestion_id} "
                f"for {prompt_type}/{prompt_name} (confidence: {suggestion.confidence_score:.2f})"
            )
            return suggestion_id

        return None

    async def _create_experiments_from_suggestions(self) -> List[int]:
        """Create experiments from high-confidence pending suggestions."""
        created = []
        suggestions = await self.analyzer.get_pending_suggestions()

        for suggestion in suggestions:
            # Only auto-deploy high confidence suggestions
            if suggestion.get("confidence_score", 0) < self.AUTO_DEPLOY_CONFIDENCE:
                continue

            # Approve and create new version
            new_version_id = await self.analyzer.approve_suggestion(suggestion["id"])
            if not new_version_id:
                continue

            # Get the original version info
            orig_version = await self.db.get_prompt_version_by_id(
                suggestion["prompt_version_id"]
            )
            if not orig_version:
                continue

            # Create experiment
            experiment_id = await self.ab_engine.create_experiment(
                name=f"auto_{orig_version['prompt_type']}_{orig_version['prompt_name']}_{datetime.now().strftime('%Y%m%d')}",
                prompt_type=orig_version["prompt_type"],
                prompt_name=orig_version["prompt_name"],
                control_version_id=suggestion["prompt_version_id"],
                treatment_version_id=new_version_id,
                traffic_split=self.DEFAULT_TRAFFIC_SPLIT
            )

            created.append(experiment_id)
            logger.info(
                f"[CorrectionApplier] Created experiment {experiment_id} "
                f"from suggestion {suggestion['id']}"
            )

        return created

    async def _process_contact_learnings(
        self,
        failures: List[Dict[str, Any]]
    ) -> int:
        """Process failures for contact type learnings."""
        count = 0

        for failure in failures:
            messages = failure.get("messages", [])
            if not messages:
                continue

            contact_type = await self.contact_learner.classify_contact(messages)
            await self.contact_learner.learn_from_outcome(
                contact_type=contact_type,
                outcome=failure.get("outcome", "declined"),
                messages=messages
            )
            count += 1

        return count

    async def get_optimization_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the optimization system.

        Returns:
            Dict with various statistics
        """
        # Get experiment stats
        experiments = await self.ab_engine.get_all_experiment_stats()
        active_experiments = [e for e in experiments]

        # Get outcome counts
        recent_outcomes = await self.db.get_recent_outcomes(days=30)
        outcome_counts = {}
        for outcome in recent_outcomes:
            otype = outcome.get("outcome", "unknown")
            outcome_counts[otype] = outcome_counts.get(otype, 0) + 1

        # Get pending suggestions
        pending = await self.analyzer.get_pending_suggestions()

        return {
            "active_experiments": len(active_experiments),
            "experiments": [
                {
                    "id": e.experiment_id,
                    "name": e.name,
                    "control_rate": f"{e.control_rate:.1%}",
                    "treatment_rate": f"{e.treatment_rate:.1%}",
                    "is_significant": e.is_significant,
                    "sample_size": e.control_total + e.treatment_total
                }
                for e in active_experiments
            ],
            "outcome_counts_30d": outcome_counts,
            "pending_suggestions": len(pending),
            "total_outcomes_30d": len(recent_outcomes)
        }
