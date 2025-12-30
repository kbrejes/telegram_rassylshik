"""
Repository for CRM topic/contact mapping operations.
"""
import logging
from typing import Optional, Dict
from src.db.base import BaseRepository

logger = logging.getLogger(__name__)


class TopicRepository(BaseRepository):
    """
    Handles CRM topic-contact mapping operations.

    Tables: crm_topic_contacts, synced_crm_messages
    """

    async def save_topic_contact(
        self,
        group_id: int,
        topic_id: int,
        contact_id: int,
        contact_name: str,
        agent_session: str = None,
        vacancy_id: int = None
    ) -> None:
        """Save or update topic-contact mapping."""
        await self._conn.execute("""
            INSERT OR REPLACE INTO crm_topic_contacts
            (group_id, topic_id, contact_id, contact_name, agent_session, vacancy_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (group_id, topic_id, contact_id, contact_name, agent_session, vacancy_id))
        await self._conn.commit()

    async def get_contact_by_topic(self, group_id: int, topic_id: int) -> Optional[Dict]:
        """Get contact info by topic."""
        cursor = await self._conn.execute("""
            SELECT contact_id, contact_name, agent_session, vacancy_id
            FROM crm_topic_contacts
            WHERE group_id = ? AND topic_id = ?
        """, (group_id, topic_id))
        row = await cursor.fetchone()
        if row:
            return {
                'contact_id': row[0],
                'contact_name': row[1],
                'agent_session': row[2],
                'vacancy_id': row[3]
            }
        return None

    async def get_topic_by_contact(self, group_id: int, contact_id: int) -> Optional[int]:
        """Get topic ID by contact."""
        cursor = await self._conn.execute("""
            SELECT topic_id FROM crm_topic_contacts
            WHERE group_id = ? AND contact_id = ?
        """, (group_id, contact_id))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def load_all_topic_contacts(self, group_id: int) -> Dict[int, int]:
        """Load all topic-contact mappings for a group. Returns {contact_id: topic_id}."""
        cursor = await self._conn.execute(
            "SELECT contact_id, topic_id FROM crm_topic_contacts WHERE group_id = ?",
            (group_id,)
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def delete_topic_contacts_by_group(self, group_id: int) -> int:
        """Delete all topic-contact mappings for a group. Returns count deleted."""
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM crm_topic_contacts WHERE group_id = ?",
            (group_id,)
        )
        count = (await cursor.fetchone())[0]

        await self._conn.execute(
            "DELETE FROM crm_topic_contacts WHERE group_id = ?",
            (group_id,)
        )
        await self._conn.commit()
        return count

    async def is_message_synced(self, contact_id: int, message_id: int) -> bool:
        """Check if message was already synced."""
        cursor = await self._conn.execute(
            "SELECT 1 FROM synced_crm_messages WHERE contact_id = ? AND message_id = ?",
            (contact_id, message_id)
        )
        return await cursor.fetchone() is not None

    async def mark_message_synced(self, contact_id: int, message_id: int) -> None:
        """Mark message as synced."""
        await self._conn.execute("""
            INSERT OR IGNORE INTO synced_crm_messages (contact_id, message_id)
            VALUES (?, ?)
        """, (contact_id, message_id))
        await self._conn.commit()
