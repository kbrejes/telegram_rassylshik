"""
Database Migrations Module

Simple migration system with version tracking.
Migrations are applied in order based on version number.
"""

import logging
from typing import List, Tuple, Callable, Awaitable
import aiosqlite

logger = logging.getLogger(__name__)

# Type alias for migration function
MigrationFunc = Callable[[aiosqlite.Connection], Awaitable[None]]

# List of migrations as (version, description, migration_function) tuples
# Version numbers must be sequential starting from 1
MIGRATIONS: List[Tuple[int, str, MigrationFunc]] = []


def migration(version: int, description: str):
    """Decorator to register a migration function."""
    def decorator(func: MigrationFunc) -> MigrationFunc:
        MIGRATIONS.append((version, description, func))
        return func
    return decorator


# ============================================================================
# MIGRATIONS - Add new migrations below with incrementing version numbers
# ============================================================================

@migration(1, "Add contact_username column to processed_jobs")
async def migration_001(conn: aiosqlite.Connection) -> None:
    try:
        await conn.execute("ALTER TABLE processed_jobs ADD COLUMN contact_username TEXT")
    except Exception:
        pass  # Column already exists (from legacy migration)


@migration(2, "Add vacancy_id column to crm_topic_contacts")
async def migration_002(conn: aiosqlite.Connection) -> None:
    try:
        await conn.execute("ALTER TABLE crm_topic_contacts ADD COLUMN vacancy_id INTEGER")
    except Exception:
        pass  # Column already exists (from legacy migration)


@migration(3, "Add agent_session column to crm_topic_contacts")
async def migration_003(conn: aiosqlite.Connection) -> None:
    try:
        await conn.execute("ALTER TABLE crm_topic_contacts ADD COLUMN agent_session TEXT")
    except Exception:
        pass  # Column may already exist


# ============================================================================
# MIGRATION RUNNER
# ============================================================================

class MigrationRunner:
    """
    Runs database migrations with version tracking.

    Usage:
        runner = MigrationRunner(connection)
        await runner.run_migrations()
    """

    def __init__(self, connection: aiosqlite.Connection):
        self.conn = connection

    async def _ensure_migrations_table(self) -> None:
        """Create schema_migrations table if it doesn't exist."""
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.conn.commit()

    async def _get_current_version(self) -> int:
        """Get the highest applied migration version."""
        cursor = await self.conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        )
        row = await cursor.fetchone()
        return row[0] if row[0] is not None else 0

    async def _record_migration(self, version: int, description: str) -> None:
        """Record that a migration has been applied."""
        await self.conn.execute(
            "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
            (version, description)
        )
        await self.conn.commit()

    async def run_migrations(self) -> int:
        """
        Run all pending migrations.

        Returns:
            Number of migrations applied.
        """
        await self._ensure_migrations_table()
        current_version = await self._get_current_version()

        # Sort migrations by version
        sorted_migrations = sorted(MIGRATIONS, key=lambda m: m[0])

        applied_count = 0
        for version, description, migration_func in sorted_migrations:
            if version <= current_version:
                continue  # Already applied

            logger.info(f"[MIGRATION] Applying v{version}: {description}")
            try:
                await migration_func(self.conn)
                await self._record_migration(version, description)
                applied_count += 1
                logger.info(f"[MIGRATION] v{version} applied successfully")
            except Exception as e:
                logger.error(f"[MIGRATION] v{version} failed: {e}")
                raise

        if applied_count == 0:
            logger.debug("[MIGRATION] Database schema is up to date")
        else:
            logger.info(f"[MIGRATION] Applied {applied_count} migration(s)")

        return applied_count

    async def get_migration_status(self) -> List[dict]:
        """Get status of all migrations."""
        await self._ensure_migrations_table()
        current_version = await self._get_current_version()

        status = []
        for version, description, _ in sorted(MIGRATIONS, key=lambda m: m[0]):
            status.append({
                "version": version,
                "description": description,
                "applied": version <= current_version
            })
        return status
