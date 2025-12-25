"""
API endpoints for AI self-correction stats.
"""
import logging
import aiosqlite
from pathlib import Path
from fastapi import APIRouter
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

# Database path
DB_PATH = Path(__file__).parent.parent.parent / "jobs.db"


async def get_db_connection():
    """Get database connection for web context."""
    return await aiosqlite.connect(str(DB_PATH))


@router.get("/stats")
async def get_ai_stats():
    """Get self-correction system statistics."""
    try:
        conn = await get_db_connection()

        # Outcome counts
        cursor = await conn.execute("""
            SELECT outcome, COUNT(*) as count
            FROM conversation_outcomes
            GROUP BY outcome
        """)
        outcome_rows = await cursor.fetchall()
        outcomes = {row[0]: row[1] for row in outcome_rows}

        # Total outcomes
        cursor = await conn.execute("SELECT COUNT(*) FROM conversation_outcomes")
        total_outcomes = (await cursor.fetchone())[0]

        # Recent outcomes (last 7 days)
        cursor = await conn.execute("""
            SELECT contact_id, outcome, channel_id, total_messages,
                   datetime(created_at) as created_at
            FROM conversation_outcomes
            ORDER BY created_at DESC
            LIMIT 20
        """)
        recent = await cursor.fetchall()
        recent_outcomes = [
            {
                "contact_id": r[0],
                "outcome": r[1],
                "channel_id": r[2],
                "total_messages": r[3],
                "created_at": r[4]
            }
            for r in recent
        ]

        # Experiments
        cursor = await conn.execute("""
            SELECT id, name, prompt_type, status,
                   datetime(start_date) as start_date
            FROM prompt_experiments
            ORDER BY start_date DESC
        """)
        exp_rows = await cursor.fetchall()
        experiments = [
            {
                "id": r[0],
                "name": r[1],
                "prompt_type": r[2],
                "status": r[3],
                "start_date": r[4]
            }
            for r in exp_rows
        ]

        # Prompt suggestions
        cursor = await conn.execute("""
            SELECT id, status, reasoning,
                   datetime(created_at) as created_at
            FROM prompt_suggestions
            ORDER BY created_at DESC
            LIMIT 10
        """)
        sugg_rows = await cursor.fetchall()
        suggestions = [
            {
                "id": r[0],
                "status": r[1],
                "reasoning": r[2][:200] + "..." if r[2] and len(r[2]) > 200 else r[2],
                "created_at": r[3]
            }
            for r in sugg_rows
        ]

        # Contact type learnings
        cursor = await conn.execute("""
            SELECT contact_type, COUNT(*) as count
            FROM contact_type_learnings
            GROUP BY contact_type
        """)
        learning_rows = await cursor.fetchall()
        contact_learnings = {row[0]: row[1] for row in learning_rows}

        await conn.close()

        return {
            "success": True,
            "stats": {
                "total_outcomes": total_outcomes,
                "outcome_breakdown": outcomes,
                "experiments_count": len(experiments),
                "suggestions_count": len(suggestions),
                "contact_types_learned": len(contact_learnings)
            },
            "recent_outcomes": recent_outcomes,
            "experiments": experiments,
            "suggestions": suggestions,
            "contact_learnings": contact_learnings
        }

    except Exception as e:
        logger.error(f"Error getting AI stats: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/outcomes")
async def get_outcomes(limit: int = 50, offset: int = 0):
    """Get conversation outcomes with pagination."""
    try:
        conn = await get_db_connection()

        cursor = await conn.execute(f"""
            SELECT contact_id, outcome, channel_id, outcome_details,
                   total_messages, phases_visited,
                   conversation_duration_hours,
                   datetime(created_at) as created_at
            FROM conversation_outcomes
            ORDER BY created_at DESC
            LIMIT {limit} OFFSET {offset}
        """)
        rows = await cursor.fetchall()

        outcomes = [
            {
                "contact_id": r[0],
                "outcome": r[1],
                "channel_id": r[2],
                "outcome_details": r[3],
                "total_messages": r[4],
                "phases_visited": r[5],
                "duration_hours": r[6],
                "created_at": r[7]
            }
            for r in rows
        ]

        cursor = await conn.execute("SELECT COUNT(*) FROM conversation_outcomes")
        total = (await cursor.fetchone())[0]

        await conn.close()

        return {
            "success": True,
            "outcomes": outcomes,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Error getting outcomes: {e}")
        return {"success": False, "error": str(e)}
