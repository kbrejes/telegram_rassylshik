"""
Repository for agent interactions and auto-response tracking.
"""
import logging
from typing import Optional, List, Dict
from src.db.base import BaseRepository

logger = logging.getLogger(__name__)


class AgentRepository(BaseRepository):
    """
    Handles bot interactions, auto-response attempts, and supervisor chat.

    Tables: bot_interactions, bot_messages, auto_response_attempts, supervisor_chat_history
    """

    # === Bot Interactions ===

    async def create_bot_interaction(
        self,
        bot_username: str,
        vacancy_id: Optional[int] = None,
        channel_id: Optional[str] = None
    ) -> int:
        """Create a new bot interaction record."""
        cursor = await self._conn.execute("""
            INSERT INTO bot_interactions (bot_username, vacancy_id, channel_id, status, started_at)
            VALUES (?, ?, ?, 'in_progress', CURRENT_TIMESTAMP)
        """, (bot_username, vacancy_id, channel_id))
        await self._conn.commit()
        return cursor.lastrowid

    async def update_bot_interaction(
        self,
        interaction_id: int,
        status: str,
        error_reason: Optional[str] = None,
        success_message: Optional[str] = None,
        messages_sent: Optional[int] = None,
        messages_received: Optional[int] = None
    ) -> None:
        """Update bot interaction status."""
        updates = ["status = ?", "completed_at = CURRENT_TIMESTAMP"]
        params = [status]

        if error_reason is not None:
            updates.append("error_reason = ?")
            params.append(error_reason)
        if success_message is not None:
            updates.append("success_message = ?")
            params.append(success_message)
        if messages_sent is not None:
            updates.append("messages_sent = ?")
            params.append(messages_sent)
        if messages_received is not None:
            updates.append("messages_received = ?")
            params.append(messages_received)

        params.append(interaction_id)
        await self._conn.execute(
            f"UPDATE bot_interactions SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )
        await self._conn.commit()

    async def save_bot_message(
        self,
        interaction_id: int,
        direction: str,
        message_text: Optional[str] = None,
        has_buttons: bool = False,
        button_clicked: Optional[str] = None,
        file_sent: Optional[str] = None
    ) -> int:
        """Save a message in bot conversation."""
        cursor = await self._conn.execute("""
            INSERT INTO bot_messages
            (interaction_id, direction, message_text, has_buttons, button_clicked, file_sent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (interaction_id, direction, message_text, 1 if has_buttons else 0, button_clicked, file_sent))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_bot_interaction(self, interaction_id: int) -> Optional[Dict]:
        """Get bot interaction by ID."""
        cursor = await self._conn.execute("""
            SELECT id, bot_username, vacancy_id, channel_id, status,
                   started_at, completed_at, messages_sent, messages_received,
                   error_reason, success_message
            FROM bot_interactions WHERE id = ?
        """, (interaction_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0], "bot_username": row[1], "vacancy_id": row[2],
                "channel_id": row[3], "status": row[4], "started_at": row[5],
                "completed_at": row[6], "messages_sent": row[7],
                "messages_received": row[8], "error_reason": row[9],
                "success_message": row[10]
            }
        return None

    async def get_bot_interactions(
        self,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict]:
        """Get bot interactions with optional status filter."""
        if status:
            cursor = await self._conn.execute("""
                SELECT id, bot_username, vacancy_id, status, started_at, completed_at,
                       messages_sent, error_reason, success_message
                FROM bot_interactions WHERE status = ?
                ORDER BY created_at DESC LIMIT ?
            """, (status, limit))
        else:
            cursor = await self._conn.execute("""
                SELECT id, bot_username, vacancy_id, status, started_at, completed_at,
                       messages_sent, error_reason, success_message
                FROM bot_interactions
                ORDER BY created_at DESC LIMIT ?
            """, (limit,))

        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "bot_username": r[1], "vacancy_id": r[2],
                "status": r[3], "started_at": r[4], "completed_at": r[5],
                "messages_sent": r[6], "error_reason": r[7], "success_message": r[8]
            }
            for r in rows
        ]

    async def check_bot_already_contacted(self, bot_username: str) -> bool:
        """Check if we already contacted this bot recently (last 24h)."""
        cursor = await self._conn.execute("""
            SELECT id FROM bot_interactions
            WHERE bot_username = ?
            AND created_at >= datetime('now', '-24 hours')
        """, (bot_username,))
        row = await cursor.fetchone()
        return row is not None

    # === Auto-Response Attempts ===

    async def save_auto_response_attempt(
        self,
        vacancy_id: int,
        contact_username: Optional[str],
        contact_user_id: Optional[int],
        agent_session: Optional[str],
        status: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        attempt_number: int = 1
    ) -> int:
        """
        Save an auto-response attempt.

        Status values:
        - 'success': Message sent successfully
        - 'failed': Send failed (with error_type and error_message)
        - 'skipped': Skipped (no TG contact, already contacted, etc.)
        - 'queued': Added to retry queue

        Error types:
        - 'invalid_peer': Invalid peer (bot, privacy settings, deleted account)
        - 'spam_limit': Agent spam limitation
        - 'flood_wait': Flood wait error
        - 'no_contact': No TG contact extracted
        - 'already_contacted': Contact already messaged
        - 'no_agent': No available agent
        - 'resolve_failed': Username resolution failed
        - 'other': Other error
        """
        cursor = await self._conn.execute("""
            INSERT INTO auto_response_attempts
            (vacancy_id, contact_username, contact_user_id, agent_session, status, error_type, error_message, attempt_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (vacancy_id, contact_username, contact_user_id, agent_session, status, error_type, error_message, attempt_number))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_auto_response_attempts(self, vacancy_id: int) -> List[Dict]:
        """Get all auto-response attempts for a vacancy."""
        cursor = await self._conn.execute("""
            SELECT id, contact_username, contact_user_id, agent_session, status,
                   error_type, error_message, attempt_number, created_at
            FROM auto_response_attempts
            WHERE vacancy_id = ?
            ORDER BY created_at ASC
        """, (vacancy_id,))
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "contact_username": r[1],
                "contact_user_id": r[2],
                "agent_session": r[3],
                "status": r[4],
                "error_type": r[5],
                "error_message": r[6],
                "attempt_number": r[7],
                "created_at": r[8]
            }
            for r in rows
        ]

    async def get_latest_auto_response_status(self, vacancy_id: int) -> Optional[Dict]:
        """Get the latest auto-response attempt status for a vacancy."""
        cursor = await self._conn.execute("""
            SELECT status, error_type, error_message, contact_username, created_at
            FROM auto_response_attempts
            WHERE vacancy_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (vacancy_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "status": row[0],
                "error_type": row[1],
                "error_message": row[2],
                "contact_username": row[3],
                "created_at": row[4]
            }
        return None

    # === Supervisor Chat History ===

    async def get_supervisor_chat_history(self, limit: int = 50) -> List[Dict]:
        """Get supervisor chat history."""
        cursor = await self._conn.execute("""
            SELECT id, role, content, tool_calls, created_at
            FROM supervisor_chat_history
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "role": r[1],
                "content": r[2],
                "tool_calls": r[3],
                "created_at": r[4]
            }
            for r in rows
        ]

    async def add_supervisor_message(self, role: str, content: str, tool_calls: str = None) -> None:
        """Add a message to supervisor chat history."""
        await self._conn.execute("""
            INSERT INTO supervisor_chat_history (role, content, tool_calls)
            VALUES (?, ?, ?)
        """, (role, content, tool_calls))
        await self._conn.commit()

    async def clear_supervisor_chat_history(self) -> None:
        """Clear all supervisor chat history."""
        await self._conn.execute("DELETE FROM supervisor_chat_history")
        await self._conn.commit()
