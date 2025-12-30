"""
Repository for job/vacancy related database operations.
"""
import logging
from typing import Optional, List, Dict
from src.db.base import BaseRepository

logger = logging.getLogger(__name__)


class JobRepository(BaseRepository):
    """
    Handles all job/vacancy related database operations.

    Tables: processed_jobs, notifications
    """

    async def check_duplicate(self, message_id: int, chat_id: int) -> bool:
        """
        Check if message was already processed.

        Returns:
            True if message was already processed, False otherwise
        """
        cursor = await self._conn.execute(
            "SELECT id FROM processed_jobs WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id)
        )
        result = await cursor.fetchone()
        return result is not None

    async def save_job(
        self,
        message_id: int,
        chat_id: int,
        chat_title: str,
        message_text: str,
        position: Optional[str] = None,
        skills: Optional[List[str]] = None,
        is_relevant: bool = False,
        ai_reason: Optional[str] = None,
        status: str = 'processed',
        contact_username: Optional[str] = None
    ) -> int:
        """
        Save processed job information.

        Returns:
            ID of created record
        """
        skills_str = ','.join(skills) if skills else None

        cursor = await self._conn.execute("""
            INSERT INTO processed_jobs
            (message_id, chat_id, chat_title, message_text, position, skills, is_relevant, ai_reason, status, contact_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message_id, chat_id, chat_title, message_text, position, skills_str, is_relevant, ai_reason, status, contact_username))

        await self._conn.commit()
        return cursor.lastrowid

    async def save_notification(self, job_id: int, template_used: str) -> None:
        """Save notification record."""
        await self._conn.execute("""
            INSERT INTO notifications (job_id, template_used)
            VALUES (?, ?)
        """, (job_id, template_used))
        await self._conn.commit()

    async def get_relevant_jobs(self, limit: int = 50) -> List[Dict]:
        """Get recent relevant jobs."""
        cursor = await self._conn.execute("""
            SELECT
                id, message_id, chat_id, chat_title, message_text,
                position, skills, is_relevant, ai_reason, processed_at,
                status, contact_username
            FROM processed_jobs
            WHERE is_relevant = 1
            ORDER BY processed_at DESC
            LIMIT ?
        """, (limit,))

        rows = await cursor.fetchall()
        jobs = []
        for row in rows:
            jobs.append({
                'id': row[0],
                'message_id': row[1],
                'chat_id': row[2],
                'chat_title': row[3],
                'message_text': row[4],
                'position': row[5],
                'skills': row[6].split(',') if row[6] else [],
                'is_relevant': bool(row[7]),
                'ai_reason': row[8],
                'processed_at': row[9],
                'status': row[10],
                'contact_username': row[11]
            })
        return jobs

    async def get_statistics(self) -> Dict:
        """Get job processing statistics."""
        cursor = await self._conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_relevant = 1 THEN 1 ELSE 0 END) as relevant,
                COUNT(DISTINCT chat_id) as unique_chats
            FROM processed_jobs
        """)
        row = await cursor.fetchone()
        return {
            'total': row[0] or 0,
            'relevant': row[1] or 0,
            'unique_chats': row[2] or 0
        }

    async def get_vacancy_id(self, message_id: int, chat_id: int) -> Optional[int]:
        """Get vacancy ID by message and chat IDs."""
        cursor = await self._conn.execute(
            "SELECT id FROM processed_jobs WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None
