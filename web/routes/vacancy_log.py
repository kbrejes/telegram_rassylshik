"""
API endpoints for vacancy/job log with AI analysis.
"""
import logging
import aiosqlite
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from web.utils import load_filter_prompt, save_filter_prompt, reset_filter_prompt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vacancies", tags=["vacancies"])


class FilterPromptRequest(BaseModel):
    prompt: str

# Database path
DB_PATH = Path(__file__).parent.parent.parent / "jobs.db"


async def get_db_connection():
    """Get database connection for web context."""
    return await aiosqlite.connect(str(DB_PATH))


@router.get("/log")
async def get_vacancy_log(limit: int = 50, offset: int = 0, filter_status: Optional[str] = None):
    """
    Get vacancy log with AI analysis outcomes.

    Args:
        limit: Number of records to return
        offset: Pagination offset
        filter_status: Filter by status ('all', 'passed', 'filtered')
    """
    try:
        conn = await get_db_connection()

        # Build query based on filter
        where_clause = ""
        if filter_status == "passed":
            where_clause = "WHERE is_relevant = 1"
        elif filter_status == "filtered":
            where_clause = "WHERE is_relevant = 0 OR status = 'filtered_by_ai'"

        # Get vacancies with AI analysis
        cursor = await conn.execute(f"""
            SELECT
                id,
                message_id,
                chat_id,
                chat_title,
                message_text,
                is_relevant,
                ai_reason,
                status,
                datetime(processed_at) as processed_at
            FROM processed_jobs
            {where_clause}
            ORDER BY processed_at DESC
            LIMIT {limit} OFFSET {offset}
        """)
        rows = await cursor.fetchall()

        vacancies = []
        for r in rows:
            # Truncate message text for display
            text = r[4] or ""
            text_preview = text[:300] + "..." if len(text) > 300 else text

            vacancies.append({
                "id": r[0],
                "message_id": r[1],
                "chat_id": r[2],
                "chat_title": r[3],
                "text_preview": text_preview,
                "text_full": text,
                "is_relevant": bool(r[5]),
                "ai_reason": r[6],
                "status": r[7],
                "processed_at": r[8]
            })

        # Get total count
        cursor = await conn.execute(f"""
            SELECT COUNT(*) FROM processed_jobs {where_clause}
        """)
        total = (await cursor.fetchone())[0]

        # Get stats
        cursor = await conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_relevant = 1 THEN 1 ELSE 0 END) as passed,
                SUM(CASE WHEN is_relevant = 0 OR status = 'filtered_by_ai' THEN 1 ELSE 0 END) as filtered
            FROM processed_jobs
        """)
        stats_row = await cursor.fetchone()
        stats = {
            "total": stats_row[0] or 0,
            "passed": stats_row[1] or 0,
            "filtered": stats_row[2] or 0
        }

        await conn.close()

        return {
            "success": True,
            "vacancies": vacancies,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": stats
        }

    except Exception as e:
        logger.error(f"Error getting vacancy log: {e}")
        return {"success": False, "error": str(e)}


@router.get("/filter-prompt")
async def get_filter_prompt():
    """Get the current AI filter prompt."""
    try:
        from src.job_analyzer import JobAnalyzer

        # Check for custom prompt
        custom_prompt = load_filter_prompt()
        is_custom = custom_prompt is not None

        # Get the effective prompt (custom or default from analyzer)
        analyzer = JobAnalyzer(providers_config={}, min_salary_rub=70000)
        prompt = analyzer._get_system_prompt()

        # Also get the default for reset functionality
        default_prompt = analyzer._get_default_system_prompt()

        return {
            "success": True,
            "prompt": prompt,
            "is_custom": is_custom,
            "default_prompt": default_prompt
        }
    except Exception as e:
        logger.error(f"Error getting filter prompt: {e}")
        return {"success": False, "error": str(e)}


@router.put("/filter-prompt")
async def update_filter_prompt(request: FilterPromptRequest):
    """Save custom AI filter prompt."""
    try:
        save_filter_prompt(request.prompt)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error saving filter prompt: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/filter-prompt")
async def delete_filter_prompt():
    """Reset AI filter prompt to default."""
    try:
        reset_filter_prompt()
        return {"success": True}
    except Exception as e:
        logger.error(f"Error resetting filter prompt: {e}")
        return {"success": False, "error": str(e)}


@router.get("/messages/{vacancy_id}")
async def get_vacancy_messages(vacancy_id: int):
    """Get bot interaction messages for a vacancy."""
    try:
        conn = await get_db_connection()

        # Get bot interactions for this vacancy
        cursor = await conn.execute("""
            SELECT id, bot_username, status, started_at, completed_at,
                   messages_sent, messages_received, error_reason, success_message
            FROM bot_interactions
            WHERE vacancy_id = ?
            ORDER BY started_at DESC
        """, (vacancy_id,))
        interactions = await cursor.fetchall()

        result = []
        for interaction in interactions:
            interaction_id = interaction[0]

            # Get messages for this interaction
            msg_cursor = await conn.execute("""
                SELECT direction, message_text, has_buttons, button_clicked, created_at
                FROM bot_messages
                WHERE interaction_id = ?
                ORDER BY created_at ASC
            """, (interaction_id,))
            messages = await msg_cursor.fetchall()

            result.append({
                "interaction_id": interaction_id,
                "bot_username": interaction[1],
                "status": interaction[2],
                "started_at": interaction[3],
                "completed_at": interaction[4],
                "messages_sent": interaction[5],
                "messages_received": interaction[6],
                "error_reason": interaction[7],
                "success_message": interaction[8],
                "messages": [
                    {
                        "direction": m[0],
                        "text": m[1],
                        "has_buttons": bool(m[2]),
                        "button_clicked": m[3],
                        "time": m[4]
                    }
                    for m in messages
                ]
            })

        await conn.close()

        return {"success": True, "interactions": result}

    except Exception as e:
        logger.error(f"Error getting vacancy messages: {e}")
        return {"success": False, "error": str(e)}
