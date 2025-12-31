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

# Path to conversation states and working memory
CONVERSATION_STATES_DIR = Path(__file__).parent.parent.parent / "data" / "conversation_states"
WORKING_MEMORY_DIR = Path(__file__).parent.parent.parent / "data" / "working_memory"

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

        # Get crm_topic_contacts with vacancy_id mapping (including contact_name)
        cursor = await conn.execute("SELECT vacancy_id, contact_id, contact_name FROM crm_topic_contacts WHERE vacancy_id IS NOT NULL")
        vacancy_to_contact = {r[0]: {"contact_id": str(r[1]), "contact_name": r[2]} for r in await cursor.fetchall()}

        vacancies = []
        for r in rows:
            # Truncate message text for display
            text = r[4] or ""
            text_preview = text[:300] + "..." if len(text) > 300 else text
            contact_username = r[9]
            vacancy_id = r[0]

            # Check if this vacancy has a linked CRM contact with active conversation
            crm_data = vacancy_to_contact.get(vacancy_id)
            contact_id = crm_data["contact_id"] if crm_data else None
            contact_name = crm_data["contact_name"] if crm_data else None
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
                "contact_name": contact_name,
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
            SELECT contact_id, contact_name, agent_session
            FROM crm_topic_contacts
            WHERE vacancy_id = ?
        """, (vacancy_id,))
        crm_contact = await cursor.fetchone()

        result = []

        if crm_contact:
            contact_id = str(crm_contact[0])
            contact_name = crm_contact[1]
            agent_session = crm_contact[2]

            # Load conversation state from file
            conv_file = CONVERSATION_STATES_DIR / f"{contact_id}.json"
            conv_data = {}
            if conv_file.exists():
                try:
                    with open(conv_file, 'r') as f:
                        conv_data = json.load(f)
                except Exception as e:
                    logger.error(f"Error reading conversation state: {e}")

            # Load message history from working memory
            messages = []
            memory_file = WORKING_MEMORY_DIR / f"{contact_id}.json"
            if memory_file.exists():
                try:
                    with open(memory_file, 'r') as f:
                        messages = json.load(f)
                except Exception as e:
                    logger.error(f"Error reading working memory: {e}")

            result.append({
                "contact_id": contact_id,
                "contact_name": contact_name,
                "agent_session": agent_session,
                "current_phase": conv_data.get("current_phase", "unknown"),
                "total_messages": conv_data.get("total_messages", 0),
                "call_offered": conv_data.get("call_offered", False),
                "call_scheduled": conv_data.get("call_scheduled", False),
                "last_interaction": conv_data.get("last_interaction"),
                "messages": messages
            })

        await conn.close()

        return {"success": True, "conversations": result}

    except Exception as e:
        logger.error(f"Error getting vacancy messages: {e}")
        return {"success": False, "error": str(e)}


class SendMessageRequest(BaseModel):
    contact_id: Optional[str] = None  # Telegram user ID (if CRM topic exists)
    contact_username: Optional[str] = None  # @username (fallback when no CRM topic)
    message: str
    agent_session: Optional[str] = None  # Optional: manually select agent


@router.post("/send-message")
async def send_crm_message(request: SendMessageRequest):
    """Send a message to a CRM contact via the bot."""
    try:
        from src.command_queue import command_queue

        # Need either contact_id or contact_username
        if not request.contact_id and not request.contact_username:
            return {"success": False, "error": "No contact to message (missing contact_id and username)"}

        # Add command to queue for bot to process
        command_data = {
            "message": request.message
        }
        if request.contact_id:
            command_data["contact_id"] = request.contact_id
        if request.contact_username:
            command_data["contact_username"] = request.contact_username
        if request.agent_session:
            command_data["agent_session"] = request.agent_session

        command_id = command_queue.add_command("send_crm_message", command_data)

        # Wait briefly for command to be processed
        import asyncio
        for _ in range(10):  # Wait up to 5 seconds
            await asyncio.sleep(0.5)
            cmd = command_queue.get_command_status(command_id)
            if cmd and cmd.status in ["completed", "failed"]:
                if cmd.status == "completed":
                    return {"success": True}
                else:
                    return {"success": False, "error": cmd.result_message or "Unknown error"}

        return {"success": False, "error": "Timeout waiting for message to be sent"}

    except Exception as e:
        logger.error(f"Error sending CRM message: {e}")
        return {"success": False, "error": str(e)}


@router.get("/agents-for-contact/{contact_id}")
async def get_agents_for_contact(contact_id: int):
    """Get available agents for sending messages to a contact.

    Returns list of agents with their status (available, blocked, flood wait time).
    """
    try:
        from src.connection_status import status_manager
        import time

        # Get all agent statuses
        status = status_manager.get_all_status()
        agents_status = status.get("agents", {})

        agents = []
        for session_name, agent_info in agents_status.items():
            if agent_info.get("status") not in ["connected"]:
                continue

            flood_wait_until = agent_info.get("flood_wait_until")
            is_available = True
            flood_wait_remaining = 0

            if flood_wait_until:
                remaining = int(flood_wait_until - time.time())
                if remaining > 0:
                    is_available = False
                    flood_wait_remaining = remaining

            user_info = agent_info.get("user_info", {})
            display_name = user_info.get("first_name", session_name)
            if user_info.get("username"):
                display_name = f"{display_name} (@{user_info.get('username')})"

            agents.append({
                "session_name": session_name,
                "display_name": display_name,
                "is_available": is_available,
                "flood_wait_remaining": flood_wait_remaining,
                "status": "available" if is_available else f"blocked ({flood_wait_remaining}s)"
            })

        # Sort: available first, then by name
        agents.sort(key=lambda a: (not a["is_available"], a["display_name"]))

        return {"success": True, "agents": agents}

    except Exception as e:
        logger.error(f"Error getting agents for contact: {e}")
        return {"success": False, "error": str(e), "agents": []}


@router.get("/{vacancy_id}/conversation")
async def get_vacancy_conversation(vacancy_id: int):
    """Get simplified conversation messages for a vacancy (for chat UI)."""
    try:
        conn = await get_db_connection()

        # Find CRM topic contact linked to this vacancy
        cursor = await conn.execute("""
            SELECT contact_id, contact_name
            FROM crm_topic_contacts
            WHERE vacancy_id = ?
        """, (vacancy_id,))
        crm_contact = await cursor.fetchone()
        await conn.close()

        if not crm_contact:
            return {"success": True, "messages": [], "contact_id": None}

        contact_id = str(crm_contact[0])
        contact_name = crm_contact[1] or f"User {contact_id}"

        # Load message history from working memory
        memory_file = WORKING_MEMORY_DIR / f"{contact_id}.json"
        if not memory_file.exists():
            return {"success": True, "messages": []}

        try:
            with open(memory_file, 'r') as f:
                raw_messages = json.load(f)
        except Exception as e:
            logger.error(f"Error reading working memory: {e}")
            return {"success": True, "messages": []}

        # Format messages for chat UI
        messages = []
        for msg in raw_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                messages.append({
                    "is_agent": False,
                    "sender_name": contact_name,
                    "text": content
                })
            elif role == "assistant":
                messages.append({
                    "is_agent": True,
                    "sender_name": "Agent",
                    "text": content
                })

        return {"success": True, "messages": messages, "contact_id": contact_id}

    except Exception as e:
        logger.error(f"Error getting vacancy conversation: {e}")
        return {"success": False, "error": str(e), "messages": [], "contact_id": None}


@router.get("/{vacancy_id}/auto-response-attempts")
async def get_auto_response_attempts(vacancy_id: int):
    """Get all auto-response attempts for a vacancy."""
    try:
        conn = await get_db_connection()

        cursor = await conn.execute("""
            SELECT id, contact_username, contact_user_id, agent_session, status,
                   error_type, error_message, attempt_number, created_at
            FROM auto_response_attempts
            WHERE vacancy_id = ?
            ORDER BY created_at ASC
        """, (vacancy_id,))
        rows = await cursor.fetchall()
        await conn.close()

        attempts = []
        for r in rows:
            attempts.append({
                "id": r[0],
                "contact_username": r[1],
                "contact_user_id": r[2],
                "agent_session": r[3],
                "status": r[4],
                "error_type": r[5],
                "error_message": r[6],
                "attempt_number": r[7],
                "created_at": r[8]
            })

        # Compute summary status for UI
        summary = None
        if attempts:
            last_attempt = attempts[-1]
            if last_attempt["status"] == "success":
                summary = {"status": "success", "message": "Message sent successfully"}
            elif last_attempt["status"] == "queued":
                summary = {"status": "queued", "message": "Queued for retry"}
            elif last_attempt["status"] == "skipped":
                error_type = last_attempt.get("error_type", "")
                if error_type == "no_contact":
                    summary = {"status": "skipped", "message": "No Telegram contact"}
                elif error_type == "already_contacted":
                    summary = {"status": "skipped", "message": "Already contacted"}
                else:
                    summary = {"status": "skipped", "message": last_attempt.get("error_message", "Skipped")}
            elif last_attempt["status"] == "failed":
                error_type = last_attempt.get("error_type", "")
                if error_type == "invalid_peer":
                    summary = {"status": "failed", "message": "User unreachable (privacy/bot/deleted)"}
                elif error_type == "spam_limit":
                    summary = {"status": "failed", "message": "Agent spam limited"}
                elif error_type == "flood_wait":
                    summary = {"status": "failed", "message": "Rate limited"}
                elif error_type == "all_agents_failed":
                    summary = {"status": "failed", "message": "All agents failed"}
                else:
                    summary = {"status": "failed", "message": last_attempt.get("error_message", "Send failed")[:100]}

        return {
            "success": True,
            "attempts": attempts,
            "summary": summary,
            "total": len(attempts)
        }

    except Exception as e:
        logger.error(f"Error getting auto-response attempts: {e}")
        return {"success": False, "error": str(e), "attempts": [], "summary": None}


# NOTE: This route must be at the end to avoid catching other routes like /messages, /send-message, /conversation
@router.get("/{vacancy_id}")
async def get_vacancy(vacancy_id: int):
    """Get single vacancy by ID."""
    try:
        conn = await get_db_connection()

        cursor = await conn.execute("""
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
            WHERE pj.id = ?
        """, (vacancy_id,))
        row = await cursor.fetchone()

        if not row:
            await conn.close()
            return {"success": False, "error": "Vacancy not found"}

        # Get CRM contact info
        cursor = await conn.execute("""
            SELECT contact_id, contact_name
            FROM crm_topic_contacts
            WHERE vacancy_id = ?
        """, (vacancy_id,))
        crm_contact = await cursor.fetchone()

        await conn.close()

        text = row[4] or ""
        text_preview = text[:300] + "..." if len(text) > 300 else text

        vacancy = {
            "id": row[0],
            "message_id": row[1],
            "chat_id": row[2],
            "chat_title": row[3],
            "text_preview": text_preview,
            "text_full": text,
            "is_relevant": bool(row[5]),
            "ai_reason": row[6],
            "status": row[7],
            "processed_at": row[8],
            "contact_username": row[9],
            "contact_id": str(crm_contact[0]) if crm_contact else None,
            "contact_name": crm_contact[1] if crm_contact else None
        }

        return {"success": True, "vacancy": vacancy}

    except Exception as e:
        logger.error(f"Error getting vacancy: {e}")
        return {"success": False, "error": str(e)}
