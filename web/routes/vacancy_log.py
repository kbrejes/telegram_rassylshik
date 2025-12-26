"""
API endpoints for vacancy/job log with AI analysis.
"""
import json
import logging
import aiosqlite
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from web.utils import load_filter_prompt, save_filter_prompt, reset_filter_prompt

# Path to conversation states
CONVERSATION_STATES_DIR = Path(__file__).parent.parent.parent / "data" / "conversation_states"

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
        where_clause_pj = ""  # With table alias for join query
        if filter_status == "passed":
            where_clause = "WHERE is_relevant = 1"
            where_clause_pj = "WHERE pj.is_relevant = 1"
        elif filter_status == "filtered":
            where_clause = "WHERE is_relevant = 0 OR status = 'filtered_by_ai'"
            where_clause_pj = "WHERE pj.is_relevant = 0 OR pj.status = 'filtered_by_ai'"

        # Get vacancies with AI analysis
        cursor = await conn.execute(f"""
            SELECT
                pj.id,
                pj.message_id,
                pj.chat_id,
                pj.chat_title,
                pj.message_text,
                pj.is_relevant,
                pj.ai_reason,
                pj.status,
                datetime(pj.processed_at) as processed_at,
                pj.contact_username
            FROM processed_jobs pj
            {where_clause_pj}
            ORDER BY pj.processed_at DESC
            LIMIT {limit} OFFSET {offset}
        """)
        rows = await cursor.fetchall()

        # Get all contact IDs that have conversation states (with messages > 0)
        contacts_with_convos = set()
        if CONVERSATION_STATES_DIR.exists():
            for f in CONVERSATION_STATES_DIR.glob("*.json"):
                try:
                    with open(f, 'r') as fp:
                        data = json.load(fp)
                        if data.get('total_messages', 0) > 0:
                            contacts_with_convos.add(f.stem)
                except Exception:
                    pass

        # Get crm_topic_contacts with vacancy_id mapping
        cursor = await conn.execute("SELECT vacancy_id, contact_id FROM crm_topic_contacts WHERE vacancy_id IS NOT NULL")
        vacancy_to_contact = {r[0]: str(r[1]) for r in await cursor.fetchall()}

        vacancies = []
        for r in rows:
            # Truncate message text for display
            text = r[4] or ""
            text_preview = text[:300] + "..." if len(text) > 300 else text
            contact_username = r[9]
            vacancy_id = r[0]

            # Check if this vacancy has a linked CRM contact with active conversation
            contact_id = vacancy_to_contact.get(vacancy_id)
            has_messages = contact_id is not None and contact_id in contacts_with_convos

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
                "processed_at": r[8],
                "contact_username": contact_username,
                "has_messages": has_messages
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
    """Get CRM conversation for a vacancy."""
    try:
        conn = await get_db_connection()

        # Find CRM topic contact linked to this vacancy
        cursor = await conn.execute("""
            SELECT contact_id, contact_name
            FROM crm_topic_contacts
            WHERE vacancy_id = ?
        """, (vacancy_id,))
        crm_contact = await cursor.fetchone()

        result = []

        if crm_contact:
            contact_id = str(crm_contact[0])
            contact_name = crm_contact[1]

            # Load conversation state from file
            conv_file = CONVERSATION_STATES_DIR / f"{contact_id}.json"
            if conv_file.exists():
                try:
                    with open(conv_file, 'r') as f:
                        conv_data = json.load(f)

                    result.append({
                        "contact_id": contact_id,
                        "contact_name": contact_name,
                        "current_phase": conv_data.get("current_phase", "unknown"),
                        "total_messages": conv_data.get("total_messages", 0),
                        "call_offered": conv_data.get("call_offered", False),
                        "call_scheduled": conv_data.get("call_scheduled", False),
                        "last_interaction": conv_data.get("last_interaction"),
                        "messages": []  # Actual messages are in Telegram, we just have state
                    })
                except Exception as e:
                    logger.error(f"Error reading conversation state: {e}")

        await conn.close()

        return {"success": True, "conversations": result}

    except Exception as e:
        logger.error(f"Error getting vacancy messages: {e}")
        return {"success": False, "error": str(e)}
