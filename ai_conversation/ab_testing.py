"""
A/B Testing Engine - Manages prompt experiments and variant assignment.

Features:
- Deterministic variant assignment based on contact_id
- Chi-square statistical significance testing
- Automatic winner promotion
- Traffic split support
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)


@dataclass
class VariantAssignment:
    """Result of variant assignment for a contact."""
    experiment_id: int
    variant: str  # 'control' or 'treatment'
    prompt_version_id: int
    experiment_name: str


@dataclass
class ExperimentStats:
    """Statistics for an experiment."""
    experiment_id: int
    name: str
    control_successes: int
    control_total: int
    treatment_successes: int
    treatment_total: int
    control_rate: float
    treatment_rate: float
    chi_square: float
    p_value: float
    is_significant: bool
    recommended_winner: Optional[str]


class ABTestingEngine:
    """
    Manages A/B testing for prompt optimization.

    Usage:
        engine = ABTestingEngine(database)
        assignment = await engine.assign_variant(contact_id, "phase", "discovery")
        if assignment:
            # Use assigned variant
            prompt_version_id = assignment.prompt_version_id
    """

    # Minimum sample size per variant before checking significance
    MIN_SAMPLE_SIZE = 30

    # P-value threshold for significance (95% confidence)
    SIGNIFICANCE_THRESHOLD = 0.05

    def __init__(self, db: "Database"):
        """
        Initialize A/B testing engine.

        Args:
            db: Database instance for persistence
        """
        self.db = db

    async def assign_variant(
        self,
        contact_id: int,
        prompt_type: str,
        prompt_name: str
    ) -> Optional[VariantAssignment]:
        """
        Assign a contact to an experiment variant.

        Uses deterministic hashing so the same contact always gets
        the same variant for a given experiment.

        Args:
            contact_id: Telegram user ID
            prompt_type: Type of prompt ('phase', 'base_context')
            prompt_name: Name of the prompt ('discovery', 'engagement', etc.)

        Returns:
            VariantAssignment if an active experiment exists, None otherwise
        """
        # Get active experiment for this prompt
        experiment = await self.db.get_active_experiment(prompt_type, prompt_name)
        if not experiment:
            return None

        # Deterministic variant assignment
        variant = self._compute_variant(
            contact_id,
            experiment["id"],
            experiment["traffic_split"]
        )

        # Get the appropriate prompt version ID
        if variant == "control":
            prompt_version_id = experiment["control_version_id"]
        else:
            prompt_version_id = experiment["treatment_version_id"]

        logger.debug(
            f"[ABTest] Assigned contact {contact_id} to {variant} "
            f"for experiment {experiment['id']} ({prompt_type}/{prompt_name})"
        )

        return VariantAssignment(
            experiment_id=experiment["id"],
            variant=variant,
            prompt_version_id=prompt_version_id,
            experiment_name=experiment["name"]
        )

    def _compute_variant(
        self,
        contact_id: int,
        experiment_id: int,
        traffic_split: float
    ) -> str:
        """
        Compute variant assignment deterministically.

        Uses hash of contact_id + experiment_id to ensure:
        1. Same contact always gets same variant for same experiment
        2. Different experiments can have different assignments for same contact
        """
        # Create deterministic hash
        hash_input = f"{contact_id}:{experiment_id}"
        hash_value = hashlib.md5(hash_input.encode()).hexdigest()

        # Convert first 8 chars to float in [0, 1)
        hash_float = int(hash_value[:8], 16) / 0xFFFFFFFF

        # Assign based on traffic split
        return "treatment" if hash_float < traffic_split else "control"

    async def get_experiment_statistics(
        self,
        experiment_id: int
    ) -> Optional[ExperimentStats]:
        """
        Calculate statistics for an experiment.

        Args:
            experiment_id: ID of the experiment

        Returns:
            ExperimentStats with chi-square test results
        """
        stats = await self.db.get_experiment_stats(experiment_id)
        if not stats:
            return None

        # Get experiment name
        experiments = await self.db.get_active_experiments()
        experiment = next(
            (e for e in experiments if e["id"] == experiment_id),
            None
        )
        name = experiment["name"] if experiment else f"Experiment {experiment_id}"

        # Extract counts
        control_success = stats.get("control_success", 0)
        control_fail = stats.get("control_fail", 0)
        treatment_success = stats.get("treatment_success", 0)
        treatment_fail = stats.get("treatment_fail", 0)

        control_total = control_success + control_fail
        treatment_total = treatment_success + treatment_fail

        # Calculate rates
        control_rate = control_success / control_total if control_total > 0 else 0
        treatment_rate = treatment_success / treatment_total if treatment_total > 0 else 0

        # Chi-square test
        chi_square, p_value = self._chi_square_test(
            control_success, control_fail,
            treatment_success, treatment_fail
        )

        # Determine significance and winner
        is_significant = (
            p_value < self.SIGNIFICANCE_THRESHOLD and
            control_total >= self.MIN_SAMPLE_SIZE and
            treatment_total >= self.MIN_SAMPLE_SIZE
        )

        recommended_winner = None
        if is_significant:
            if treatment_rate > control_rate:
                recommended_winner = "treatment"
            elif control_rate > treatment_rate:
                recommended_winner = "control"

        return ExperimentStats(
            experiment_id=experiment_id,
            name=name,
            control_successes=control_success,
            control_total=control_total,
            treatment_successes=treatment_success,
            treatment_total=treatment_total,
            control_rate=control_rate,
            treatment_rate=treatment_rate,
            chi_square=chi_square,
            p_value=p_value,
            is_significant=is_significant,
            recommended_winner=recommended_winner
        )

    def _chi_square_test(
        self,
        control_success: int,
        control_fail: int,
        treatment_success: int,
        treatment_fail: int
    ) -> Tuple[float, float]:
        """
        Perform chi-square test for independence.

        Returns:
            Tuple of (chi_square_statistic, p_value)
        """
        # Observed values
        observed = [
            [control_success, control_fail],
            [treatment_success, treatment_fail]
        ]

        # Calculate totals
        row_totals = [sum(row) for row in observed]
        col_totals = [
            observed[0][0] + observed[1][0],
            observed[0][1] + observed[1][1]
        ]
        total = sum(row_totals)

        if total == 0:
            return 0.0, 1.0

        # Calculate expected values
        expected = []
        for i in range(2):
            row = []
            for j in range(2):
                exp = (row_totals[i] * col_totals[j]) / total
                row.append(exp)
            expected.append(row)

        # Calculate chi-square statistic
        chi_square = 0.0
        for i in range(2):
            for j in range(2):
                if expected[i][j] > 0:
                    chi_square += (
                        (observed[i][j] - expected[i][j]) ** 2
                    ) / expected[i][j]

        # Calculate p-value using chi-square distribution with 1 df
        # Using approximation for simplicity (avoid scipy dependency)
        p_value = self._chi_square_p_value(chi_square, df=1)

        return chi_square, p_value

    def _chi_square_p_value(self, chi_square: float, df: int = 1) -> float:
        """
        Approximate p-value for chi-square distribution.

        Uses Wilson-Hilferty approximation for simplicity.
        For df=1, we can use a simpler approximation.
        """
        import math

        if chi_square <= 0:
            return 1.0

        # For df=1, use standard normal approximation
        z = math.sqrt(chi_square)

        # Approximate standard normal CDF (one-sided)
        # Using Abramowitz and Stegun approximation
        p = 0.5 * math.erfc(z / math.sqrt(2))

        return p

    async def check_and_promote_winners(self) -> List[Dict]:
        """
        Check all active experiments and promote statistically significant winners.

        Returns:
            List of promotion results
        """
        experiments = await self.db.get_active_experiments()
        results = []

        for exp in experiments:
            stats = await self.get_experiment_statistics(exp["id"])
            if not stats:
                continue

            if stats.is_significant and stats.recommended_winner:
                # Promote the winner
                await self.db.complete_experiment(
                    exp["id"],
                    stats.recommended_winner
                )

                # Activate the winning version
                winner_version_id = (
                    exp["treatment_version_id"]
                    if stats.recommended_winner == "treatment"
                    else exp["control_version_id"]
                )

                # Deactivate old active version and activate winner
                await self._promote_version(
                    exp["prompt_type"],
                    exp["prompt_name"],
                    winner_version_id
                )

                results.append({
                    "experiment_id": exp["id"],
                    "experiment_name": exp["name"],
                    "winner": stats.recommended_winner,
                    "promoted_version_id": winner_version_id,
                    "control_rate": f"{stats.control_rate:.1%}",
                    "treatment_rate": f"{stats.treatment_rate:.1%}",
                    "p_value": stats.p_value
                })

                logger.info(
                    f"[ABTest] Promoted {stats.recommended_winner} for experiment "
                    f"{exp['name']} (p={stats.p_value:.4f})"
                )

        return results

    async def _promote_version(
        self,
        prompt_type: str,
        prompt_name: str,
        version_id: int
    ):
        """
        Promote a version to active status.

        Deactivates any currently active version for the same prompt.
        """
        # Get current active version
        current = await self.db.get_active_prompt_version(prompt_type, prompt_name)
        if current:
            # Deactivate it (set is_active = 0)
            await self.db.execute(
                "UPDATE prompt_versions SET is_active = 0 WHERE id = ?",
                (current["id"],)
            )

        # Activate new version
        await self.db.execute(
            "UPDATE prompt_versions SET is_active = 1 WHERE id = ?",
            (version_id,)
        )

    async def create_experiment(
        self,
        name: str,
        prompt_type: str,
        prompt_name: str,
        control_version_id: int,
        treatment_version_id: int,
        traffic_split: float = 0.5,
        min_sample_size: int = 30
    ) -> int:
        """
        Create a new A/B experiment.

        Args:
            name: Human-readable experiment name
            prompt_type: Type of prompt ('phase', 'base_context')
            prompt_name: Name of the prompt
            control_version_id: ID of the control (current) version
            treatment_version_id: ID of the treatment (new) version
            traffic_split: Fraction of traffic to treatment (0.0-1.0)
            min_sample_size: Minimum samples per variant before checking significance

        Returns:
            ID of the created experiment
        """
        experiment_id = await self.db.create_experiment(
            name=name,
            prompt_type=prompt_type,
            prompt_name=prompt_name,
            control_version_id=control_version_id,
            treatment_version_id=treatment_version_id,
            traffic_split=traffic_split,
            min_sample_size=min_sample_size
        )

        logger.info(
            f"[ABTest] Created experiment '{name}' ({prompt_type}/{prompt_name}) "
            f"with {traffic_split:.0%} treatment traffic"
        )

        return experiment_id

    async def get_all_experiment_stats(self) -> List[ExperimentStats]:
        """
        Get statistics for all active experiments.

        Returns:
            List of ExperimentStats for all active experiments
        """
        experiments = await self.db.get_active_experiments()
        stats_list = []

        for exp in experiments:
            stats = await self.get_experiment_statistics(exp["id"])
            if stats:
                stats_list.append(stats)

        return stats_list
