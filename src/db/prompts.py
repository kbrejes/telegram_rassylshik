"""
Repository for self-correcting prompts and A/B testing.
"""
import json
import logging
from typing import Optional, List, Dict
from src.db.base import BaseRepository

logger = logging.getLogger(__name__)


class PromptRepository(BaseRepository):
    """
    Handles prompt versioning, experiments, outcomes, and metrics.

    Tables: prompt_versions, conversation_outcomes, prompt_experiments,
            prompt_metrics, prompt_suggestions, contact_type_learnings
    """

    # === Prompt Versioning ===

    async def create_prompt_version(
        self,
        prompt_type: str,
        prompt_name: str,
        content: str,
        parent_version_id: Optional[int] = None,
        created_by: str = "manual",
        is_active: bool = False
    ) -> int:
        """Create a new prompt version."""
        # Get next version number
        cursor = await self._conn.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_versions
            WHERE prompt_type = ? AND prompt_name = ?
        """, (prompt_type, prompt_name))
        row = await cursor.fetchone()
        version = row[0]

        cursor = await self._conn.execute("""
            INSERT INTO prompt_versions
            (prompt_type, prompt_name, version, content, parent_version_id, created_by, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (prompt_type, prompt_name, version, content, parent_version_id, created_by, is_active))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_active_prompt_version(self, prompt_type: str, prompt_name: str) -> Optional[Dict]:
        """Get the currently active prompt version."""
        cursor = await self._conn.execute("""
            SELECT id, version, content, created_by, created_at
            FROM prompt_versions
            WHERE prompt_type = ? AND prompt_name = ? AND is_active = 1
        """, (prompt_type, prompt_name))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "version": row[1],
                "content": row[2],
                "created_by": row[3],
                "created_at": row[4]
            }
        return None

    async def get_prompt_version_by_id(self, version_id: int) -> Optional[Dict]:
        """Get prompt version by ID."""
        cursor = await self._conn.execute("""
            SELECT id, prompt_type, prompt_name, version, content, created_by, is_active
            FROM prompt_versions WHERE id = ?
        """, (version_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "prompt_type": row[1],
                "prompt_name": row[2],
                "version": row[3],
                "content": row[4],
                "created_by": row[5],
                "is_active": row[6]
            }
        return None

    async def set_active_prompt_version(self, version_id: int) -> None:
        """Set a prompt version as active (deactivates others of same type/name)."""
        version = await self.get_prompt_version_by_id(version_id)
        if not version:
            return

        # Deactivate all versions of same type/name
        await self._conn.execute("""
            UPDATE prompt_versions SET is_active = 0
            WHERE prompt_type = ? AND prompt_name = ?
        """, (version["prompt_type"], version["prompt_name"]))

        # Activate the specified version
        await self._conn.execute("""
            UPDATE prompt_versions SET is_active = 1 WHERE id = ?
        """, (version_id,))
        await self._conn.commit()

    # === Conversation Outcomes ===

    async def save_conversation_outcome(
        self,
        contact_id: int,
        channel_id: str,
        outcome: str,
        outcome_details: Optional[str] = None,
        prompt_version_id: Optional[int] = None,
        experiment_id: Optional[int] = None,
        variant: Optional[str] = None,
        total_messages: Optional[int] = None,
        phases_visited: Optional[str] = None,
        conversation_duration_hours: Optional[float] = None
    ) -> int:
        """Save a conversation outcome."""
        cursor = await self._conn.execute("""
            INSERT INTO conversation_outcomes
            (contact_id, channel_id, outcome, outcome_details, prompt_version_id,
             experiment_id, variant, total_messages, phases_visited, conversation_duration_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (contact_id, channel_id, outcome, outcome_details, prompt_version_id,
              experiment_id, variant, total_messages, phases_visited, conversation_duration_hours))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_outcomes_for_prompt_version(
        self,
        prompt_version_id: int,
        outcomes: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get conversation outcomes for a prompt version."""
        if outcomes:
            placeholders = ",".join("?" * len(outcomes))
            query = f"""
                SELECT id, contact_id, channel_id, outcome, outcome_details,
                       total_messages, phases_visited, created_at
                FROM conversation_outcomes
                WHERE prompt_version_id = ? AND outcome IN ({placeholders})
                ORDER BY created_at DESC LIMIT ?
            """
            params = [prompt_version_id] + outcomes + [limit]
        else:
            query = """
                SELECT id, contact_id, channel_id, outcome, outcome_details,
                       total_messages, phases_visited, created_at
                FROM conversation_outcomes
                WHERE prompt_version_id = ?
                ORDER BY created_at DESC LIMIT ?
            """
            params = [prompt_version_id, limit]

        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "contact_id": r[1], "channel_id": r[2],
                "outcome": r[3], "outcome_details": r[4],
                "total_messages": r[5], "phases_visited": r[6], "created_at": r[7]
            }
            for r in rows
        ]

    async def update_conversation_outcome(
        self,
        contact_id: int,
        channel_id: str,
        outcome: str,
        outcome_details: str = None
    ) -> None:
        """Update outcome for an existing conversation."""
        await self._conn.execute("""
            UPDATE conversation_outcomes
            SET outcome = ?, outcome_details = ?, updated_at = CURRENT_TIMESTAMP
            WHERE contact_id = ? AND channel_id = ? AND outcome = 'ongoing'
        """, (outcome, outcome_details, contact_id, channel_id))
        await self._conn.commit()

    async def get_recent_outcomes(
        self,
        channel_id: Optional[str] = None,
        outcome_filter: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict]:
        """Get recent conversation outcomes."""
        query = """
            SELECT co.id, co.contact_id, co.channel_id, co.outcome, co.outcome_details,
                   co.total_messages, co.phases_visited, co.created_at,
                   co.prompt_version_id, co.experiment_id, co.variant
            FROM conversation_outcomes co
            WHERE 1=1
        """
        params = []

        if channel_id:
            query += " AND co.channel_id = ?"
            params.append(channel_id)

        if outcome_filter:
            placeholders = ",".join("?" * len(outcome_filter))
            query += f" AND co.outcome IN ({placeholders})"
            params.extend(outcome_filter)

        query += " ORDER BY co.created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "id": r[0], "contact_id": r[1], "channel_id": r[2],
                "outcome": r[3], "outcome_details": r[4],
                "total_messages": r[5], "phases_visited": r[6], "created_at": r[7],
                "prompt_version_id": r[8], "experiment_id": r[9], "variant": r[10]
            }
            for r in rows
        ]

    # === A/B Experiments ===

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
        """Create a new A/B experiment."""
        cursor = await self._conn.execute("""
            INSERT INTO prompt_experiments
            (name, prompt_type, prompt_name, control_version_id, treatment_version_id,
             traffic_split, min_sample_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, prompt_type, prompt_name, control_version_id, treatment_version_id,
              traffic_split, min_sample_size))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_active_experiment(self, prompt_type: str, prompt_name: str) -> Optional[Dict]:
        """Get active experiment for a prompt."""
        cursor = await self._conn.execute("""
            SELECT id, name, control_version_id, treatment_version_id, traffic_split, min_sample_size
            FROM prompt_experiments
            WHERE prompt_type = ? AND prompt_name = ? AND status = 'active'
        """, (prompt_type, prompt_name))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0], "name": row[1],
                "control_version_id": row[2], "treatment_version_id": row[3],
                "traffic_split": row[4], "min_sample_size": row[5]
            }
        return None

    async def get_experiment_stats(self, experiment_id: int) -> Dict:
        """Get statistics for an experiment."""
        cursor = await self._conn.execute("""
            SELECT variant, outcome, COUNT(*) as count
            FROM conversation_outcomes
            WHERE experiment_id = ?
            GROUP BY variant, outcome
        """, (experiment_id,))
        rows = await cursor.fetchall()

        stats = {
            "control": {"total": 0, "success": 0, "failure": 0},
            "treatment": {"total": 0, "success": 0, "failure": 0}
        }

        for variant, outcome, count in rows:
            if variant in stats:
                stats[variant]["total"] += count
                if outcome == "call_scheduled":
                    stats[variant]["success"] += count
                elif outcome in ("disengaged", "declined"):
                    stats[variant]["failure"] += count

        cursor = await self._conn.execute(
            "SELECT min_sample_size FROM prompt_experiments WHERE id = ?",
            (experiment_id,)
        )
        row = await cursor.fetchone()
        stats["min_sample_size"] = row[0] if row else 30

        return stats

    async def complete_experiment(self, experiment_id: int, winner: str) -> None:
        """Mark experiment as completed."""
        await self._conn.execute("""
            UPDATE prompt_experiments
            SET status = 'completed', end_date = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (experiment_id,))
        await self._conn.commit()

    async def get_active_experiments(self) -> List[Dict]:
        """Get all active experiments."""
        cursor = await self._conn.execute("""
            SELECT id, name, prompt_type, prompt_name, control_version_id, treatment_version_id
            FROM prompt_experiments WHERE status = 'active'
        """)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "name": r[1], "prompt_type": r[2], "prompt_name": r[3],
             "control_version_id": r[4], "treatment_version_id": r[5]}
            for r in rows
        ]

    # === Prompt Suggestions ===

    async def save_prompt_suggestion(
        self,
        prompt_version_id: int,
        suggested_content: str,
        reasoning: str,
        analyzed_conversation_ids: List[int],
        confidence_score: float
    ) -> int:
        """Save an LLM-generated prompt suggestion."""
        cursor = await self._conn.execute("""
            INSERT INTO prompt_suggestions
            (prompt_version_id, suggested_content, reasoning, analyzed_conversation_ids, confidence_score)
            VALUES (?, ?, ?, ?, ?)
        """, (prompt_version_id, suggested_content, reasoning,
              json.dumps(analyzed_conversation_ids), confidence_score))
        await self._conn.commit()
        return cursor.lastrowid

    async def update_suggestion_status(self, suggestion_id: int, status: str) -> None:
        """Update suggestion status."""
        await self._conn.execute("""
            UPDATE prompt_suggestions SET status = ? WHERE id = ?
        """, (status, suggestion_id))
        await self._conn.commit()

    # === Contact Type Learnings ===

    async def save_contact_type_learning(
        self,
        contact_type: str,
        learning: str,
        source_conversation_ids: List[int],
        confidence_score: float
    ) -> int:
        """Save a contact type learning."""
        cursor = await self._conn.execute("""
            INSERT INTO contact_type_learnings
            (contact_type, learning, source_conversation_ids, confidence_score)
            VALUES (?, ?, ?, ?)
        """, (contact_type, learning, json.dumps(source_conversation_ids), confidence_score))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_contact_type_learnings(self, contact_type: str) -> List[Dict]:
        """Get learnings for a contact type."""
        cursor = await self._conn.execute("""
            SELECT id, learning, confidence_score
            FROM contact_type_learnings
            WHERE contact_type = ?
            ORDER BY confidence_score DESC
        """, (contact_type,))
        rows = await cursor.fetchall()
        return [{"id": r[0], "learning": r[1], "confidence_score": r[2]} for r in rows]

    # === Metrics ===

    async def get_prompt_metrics(self, prompt_version_id: int) -> Dict:
        """Get metrics for a prompt version."""
        cursor = await self._conn.execute("""
            SELECT total_conversations, successful_outcomes, failed_outcomes, conversion_rate
            FROM prompt_metrics WHERE prompt_version_id = ?
        """, (prompt_version_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "total_conversations": row[0],
                "successful_outcomes": row[1],
                "failed_outcomes": row[2],
                "conversion_rate": row[3]
            }
        return {"total_conversations": 0, "successful_outcomes": 0, "failed_outcomes": 0, "conversion_rate": 0}

    async def update_prompt_metrics(self, prompt_version_id: int) -> None:
        """Recalculate and update metrics for a prompt version."""
        cursor = await self._conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'call_scheduled' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN outcome IN ('disengaged', 'declined') THEN 1 ELSE 0 END) as failed
            FROM conversation_outcomes
            WHERE prompt_version_id = ?
        """, (prompt_version_id,))
        row = await cursor.fetchone()

        total, success, failed = row[0] or 0, row[1] or 0, row[2] or 0
        conversion_rate = success / total if total > 0 else 0

        # Upsert metrics
        await self._conn.execute("""
            INSERT INTO prompt_metrics (prompt_version_id, total_conversations, successful_outcomes, failed_outcomes, conversion_rate)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(prompt_version_id) DO UPDATE SET
                total_conversations = ?,
                successful_outcomes = ?,
                failed_outcomes = ?,
                conversion_rate = ?,
                updated_at = CURRENT_TIMESTAMP
        """, (prompt_version_id, total, success, failed, conversion_rate,
              total, success, failed, conversion_rate))
        await self._conn.commit()
