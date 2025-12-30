"""
Base repository class for database operations.
"""
import aiosqlite
from typing import Optional


class BaseRepository:
    """
    Base class for all repositories.

    Repositories receive a connection from the Database class
    and provide domain-specific data access methods.
    """

    def __init__(self, connection: aiosqlite.Connection):
        self._conn = connection

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a query without returning results."""
        await self._conn.execute(query, params)
        await self._conn.commit()

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Execute query and return first row as dict."""
        cursor = await self._conn.execute(query, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [description[0] for description in cursor.description]
        return dict(zip(columns, row))

    async def fetch_all(self, query: str, params: tuple = ()) -> list:
        """Execute query and return all rows as list of dicts."""
        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        if not rows:
            return []
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
