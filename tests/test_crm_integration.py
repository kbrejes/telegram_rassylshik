"""
Integration tests for CRM functionality.

Tests the critical paths:
- Auto-response sending
- Message queue operations
- Database operations
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Test Fixtures
# ============================================================================

@dataclass
class MockChannelConfig:
    """Mock channel config for testing."""
    id: str = "test_channel"
    name: str = "Test Channel"
    auto_response_enabled: bool = True
    auto_response_template: str = "Hello! Thanks for your interest."


@dataclass
class MockAgentPool:
    """Mock agent pool for testing."""
    agents: list = None

    def __post_init__(self):
        if self.agents is None:
            self.agents = [MagicMock()]

    async def send_message(self, target, text, max_retries=3):
        """Mock send_message that succeeds."""
        return True


class MockAgentPoolFailing(MockAgentPool):
    """Mock agent pool that always fails."""
    async def send_message(self, target, text, max_retries=3):
        return False


# ============================================================================
# Auto-Responder Tests
# ============================================================================

class TestAutoResponder:
    """Tests for auto-response sending logic."""

    @pytest.mark.asyncio
    async def test_send_auto_response_success(self):
        """Test successful auto-response sending."""
        from src.crm.auto_responder import send_auto_response

        channel = MockChannelConfig()
        agent_pool = MockAgentPool()
        contacts = {"telegram": "@testuser"}
        contacted_users = set()

        with patch("src.crm.auto_responder.db") as mock_db:
            mock_db.save_auto_response_attempt = AsyncMock()

            result = await send_auto_response(
                channel=channel,
                agent_pool=agent_pool,
                contacts=contacts,
                contacted_users=contacted_users,
            )

        assert result is True
        assert "@testuser".lower() in contacted_users

    @pytest.mark.asyncio
    async def test_send_auto_response_skips_disabled(self):
        """Test that auto-response is skipped when disabled."""
        from src.crm.auto_responder import send_auto_response

        channel = MockChannelConfig(auto_response_enabled=False)
        agent_pool = MockAgentPool()
        contacts = {"telegram": "@testuser"}
        contacted_users = set()

        result = await send_auto_response(
            channel=channel,
            agent_pool=agent_pool,
            contacts=contacts,
            contacted_users=contacted_users,
        )

        assert result is False
        assert len(contacted_users) == 0

    @pytest.mark.asyncio
    async def test_send_auto_response_skips_no_contact(self):
        """Test that auto-response is skipped when no contact."""
        from src.crm.auto_responder import send_auto_response

        channel = MockChannelConfig()
        agent_pool = MockAgentPool()
        contacts = {}  # No telegram contact
        contacted_users = set()

        with patch("src.crm.auto_responder.db") as mock_db:
            mock_db.save_auto_response_attempt = AsyncMock()

            result = await send_auto_response(
                channel=channel,
                agent_pool=agent_pool,
                contacts=contacts,
                contacted_users=contacted_users,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_auto_response_skips_already_contacted(self):
        """Test that auto-response is skipped for already contacted users."""
        from src.crm.auto_responder import send_auto_response

        channel = MockChannelConfig()
        agent_pool = MockAgentPool()
        contacts = {"telegram": "@testuser"}
        contacted_users = {"@testuser"}  # Already contacted

        with patch("src.crm.auto_responder.db") as mock_db:
            mock_db.save_auto_response_attempt = AsyncMock()

            result = await send_auto_response(
                channel=channel,
                agent_pool=agent_pool,
                contacts=contacts,
                contacted_users=contacted_users,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_auto_response_queues_on_failure(self):
        """Test that failed messages are queued for retry."""
        from src.crm.auto_responder import send_auto_response

        channel = MockChannelConfig()
        agent_pool = MockAgentPoolFailing()
        contacts = {"telegram": "@testuser"}
        contacted_users = set()

        with patch("src.crm.auto_responder.db") as mock_db, \
             patch("src.crm.auto_responder.message_queue") as mock_queue:
            mock_db.save_auto_response_attempt = AsyncMock()
            mock_queue.add = AsyncMock()

            result = await send_auto_response(
                channel=channel,
                agent_pool=agent_pool,
                contacts=contacts,
                contacted_users=contacted_users,
            )

        assert result is False
        mock_queue.add.assert_called_once()


# ============================================================================
# Message Queue Tests
# ============================================================================

class TestMessageQueue:
    """Tests for message queue operations."""

    @pytest.mark.asyncio
    async def test_queue_add_and_get(self):
        """Test adding and retrieving messages from queue."""
        from src.message_queue import MessageQueue

        queue = MessageQueue()

        result = await queue.add(
            contact="@testuser",
            text="Hello",
            channel_id="test_channel",
        )

        assert result is True
        count = await queue.get_pending_count()
        assert count == 1

        messages = await queue.get_pending_messages()
        assert len(messages) == 1
        assert messages[0].contact == "@testuser"

    @pytest.mark.asyncio
    async def test_queue_remove(self):
        """Test removing messages from queue."""
        from src.message_queue import MessageQueue

        queue = MessageQueue()

        await queue.add(
            contact="@testuser",
            text="Hello",
            channel_id="test_channel",
        )

        result = await queue.remove("@testuser", "test_channel")
        assert result is True

        count = await queue.get_pending_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_queue_no_duplicate(self):
        """Test that duplicate messages are not added."""
        from src.message_queue import MessageQueue

        queue = MessageQueue()

        await queue.add(
            contact="@testuser",
            text="Hello",
            channel_id="test_channel",
        )
        await queue.add(
            contact="@testuser",
            text="Hello again",
            channel_id="test_channel",
        )

        count = await queue.get_pending_count()
        assert count == 1  # Still just one message


# ============================================================================
# Database Tests
# ============================================================================

class TestDatabaseOperations:
    """Tests for database operations."""

    @pytest.mark.asyncio
    async def test_check_duplicate(self):
        """Test duplicate checking."""
        from src.database import Database
        import tempfile
        import os

        # Create a temporary database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = Database(db_path)
            await db.connect()

            # First check should be False (not duplicate)
            is_dup = await db.check_duplicate(message_id=123, chat_id=456)
            assert is_dup is False

            # Save the job
            await db.save_job(
                message_id=123,
                chat_id=456,
                chat_title="Test Chat",
                message_text="Test message",
            )

            # Second check should be True (is duplicate)
            is_dup = await db.check_duplicate(message_id=123, chat_id=456)
            assert is_dup is True

            await db.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_topic_contact_mapping(self):
        """Test topic-contact mapping operations."""
        from src.database import Database
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = Database(db_path)
            await db.connect()

            # Save a topic-contact mapping
            await db.save_topic_contact(
                group_id=123,
                topic_id=456,
                contact_id=789,
                contact_name="Test User",
            )

            # Load mappings
            mappings = await db.load_all_topic_contacts(group_id=123)
            assert 456 in mappings
            assert mappings[456] == 789

            await db.close()
        finally:
            os.unlink(db_path)


# ============================================================================
# Migration Tests
# ============================================================================

class TestMigrations:
    """Tests for database migrations."""

    @pytest.mark.asyncio
    async def test_migration_runner(self):
        """Test that migrations run successfully."""
        from src.db.migrations import MigrationRunner
        import aiosqlite
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            async with aiosqlite.connect(db_path) as conn:
                runner = MigrationRunner(conn)

                # Run migrations
                applied = await runner.run_migrations()

                # Check status
                status = await runner.get_migration_status()
                assert len(status) > 0
                assert all(s["applied"] for s in status)

        finally:
            os.unlink(db_path)


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
