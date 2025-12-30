"""
Database module for SQLite operations.

This is a facade class that provides a unified interface to all repositories.
Schema creation and migrations are handled here.
For migration definitions, see src/db/migrations.py
"""
import aiosqlite
import logging
from typing import Optional, List, Dict
from src.config import config
from src.db.migrations import MigrationRunner
from src.db.jobs import JobRepository
from src.db.topics import TopicRepository
from src.db.prompts import PromptRepository
from src.db.agents import AgentRepository

logger = logging.getLogger(__name__)


class Database:
    """
    Database facade providing unified access to all repositories.

    Repositories:
    - jobs: Job/vacancy operations (processed_jobs, notifications)
    - topics: CRM topic-contact mapping (crm_topic_contacts, synced_crm_messages)
    - prompts: Self-correcting prompts (prompt_versions, experiments, outcomes)
    - agents: Bot interactions and auto-responses (bot_interactions, auto_response_attempts)
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DATABASE_PATH
        self._connection = None
        # Repositories (initialized on connect)
        self._jobs: Optional[JobRepository] = None
        self._topics: Optional[TopicRepository] = None
        self._prompts: Optional[PromptRepository] = None
        self._agents: Optional[AgentRepository] = None

    @property
    def jobs(self) -> JobRepository:
        """Access job repository directly."""
        return self._jobs

    @property
    def topics(self) -> TopicRepository:
        """Access topic repository directly."""
        return self._topics

    @property
    def prompts(self) -> PromptRepository:
        """Access prompt repository directly."""
        return self._prompts

    @property
    def agents(self) -> AgentRepository:
        """Access agent repository directly."""
        return self._agents

    async def connect(self):
        """Connect to database and initialize repositories."""
        self._connection = await aiosqlite.connect(self.db_path)
        await self._create_tables()

        # Initialize repositories with the connection
        self._jobs = JobRepository(self._connection)
        self._topics = TopicRepository(self._connection)
        self._prompts = PromptRepository(self._connection)
        self._agents = AgentRepository(self._connection)

        logger.info(f"Connected to database: {self.db_path}")

    async def close(self):
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            logger.info("Database connection closed")

    async def _create_tables(self) -> None:
        """Create database tables and run migrations."""
        # Create base tables first
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS processed_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                message_text TEXT,
                position TEXT,
                skills TEXT,
                is_relevant BOOLEAN DEFAULT 0,
                ai_reason TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'processed',
                contact_username TEXT,
                UNIQUE(message_id, chat_id)
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                template_used TEXT,
                FOREIGN KEY (job_id) REFERENCES processed_jobs(id)
            )
        """)

        # CRM topic-contact mapping table
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS crm_topic_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                contact_id INTEGER NOT NULL,
                contact_name TEXT,
                agent_session TEXT,
                vacancy_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, topic_id)
            )
        """)

        # === Self-correcting prompts tables ===

        # Prompt versions with content and metadata
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_type TEXT NOT NULL,
                prompt_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                parent_version_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'manual',
                is_active BOOLEAN DEFAULT 0,
                UNIQUE(prompt_type, prompt_name, version)
            )
        """)

        # Conversation outcomes for tracking success/failure
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS conversation_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                outcome_details TEXT,
                prompt_version_id INTEGER,
                experiment_id INTEGER,
                variant TEXT,
                total_messages INTEGER,
                phases_visited TEXT,
                conversation_duration_hours REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # A/B test experiments
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS prompt_experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                prompt_type TEXT NOT NULL,
                prompt_name TEXT NOT NULL,
                control_version_id INTEGER NOT NULL,
                treatment_version_id INTEGER NOT NULL,
                traffic_split REAL DEFAULT 0.5,
                status TEXT DEFAULT 'active',
                start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_date TIMESTAMP,
                min_sample_size INTEGER DEFAULT 30
            )
        """)

        # Aggregated metrics per prompt version
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS prompt_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_version_id INTEGER NOT NULL,
                total_conversations INTEGER DEFAULT 0,
                successful_outcomes INTEGER DEFAULT 0,
                failed_outcomes INTEGER DEFAULT 0,
                avg_messages_to_success REAL,
                avg_messages_to_failure REAL,
                conversion_rate REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # LLM-generated prompt improvement suggestions
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS prompt_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_version_id INTEGER NOT NULL,
                suggested_content TEXT NOT NULL,
                reasoning TEXT,
                analyzed_conversation_ids TEXT,
                confidence_score REAL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Per-contact-type learnings
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS contact_type_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_type TEXT NOT NULL,
                learning TEXT NOT NULL,
                source_conversation_ids TEXT,
                confidence_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Synced CRM messages (to avoid duplicate syncing on restart)
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS synced_crm_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(contact_id, message_id)
            )
        """)

        # === Bot Interaction Tables ===

        # Bot interactions tracking
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS bot_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_username TEXT NOT NULL,
                vacancy_id INTEGER,
                channel_id TEXT,
                status TEXT DEFAULT 'pending',
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                messages_sent INTEGER DEFAULT 0,
                messages_received INTEGER DEFAULT 0,
                error_reason TEXT,
                success_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Individual messages in bot conversations
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS bot_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interaction_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                message_text TEXT,
                has_buttons INTEGER DEFAULT 0,
                button_clicked TEXT,
                file_sent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (interaction_id) REFERENCES bot_interactions(id)
            )
        """)

        # === Auto-Response Attempts ===
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS auto_response_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vacancy_id INTEGER NOT NULL,
                contact_username TEXT,
                contact_user_id INTEGER,
                agent_session TEXT,
                status TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT,
                attempt_number INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vacancy_id) REFERENCES processed_jobs(id)
            )
        """)

        # === Supervisor AI Chat ===
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS supervisor_chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._connection.commit()

        # Run migrations for schema changes
        migration_runner = MigrationRunner(self._connection)
        await migration_runner.run_migrations()

        logger.info("Database tables created/verified, migrations applied")

    # =========================================================================
    # FACADE METHODS - Delegate to repositories for backward compatibility
    # =========================================================================

    # === Job Repository Facade ===

    async def check_duplicate(self, message_id: int, chat_id: int) -> bool:
        """Check if message was already processed."""
        return await self._jobs.check_duplicate(message_id, chat_id)

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
        """Save processed job information."""
        job_id = await self._jobs.save_job(
            message_id, chat_id, chat_title, message_text,
            position, skills, is_relevant, ai_reason, status, contact_username
        )
        logger.info(f"Saved job ID={job_id} from chat {chat_title}")
        return job_id

    async def save_notification(self, job_id: int, template_used: str) -> None:
        """Save notification record."""
        await self._jobs.save_notification(job_id, template_used)
        logger.info(f"Saved notification for job ID={job_id}")

    async def get_relevant_jobs(self, limit: int = 50) -> List[Dict]:
        """Get recent relevant jobs."""
        return await self._jobs.get_relevant_jobs(limit)

    async def get_statistics(self) -> Dict:
        """Get job processing statistics."""
        return await self._jobs.get_statistics()

    async def get_vacancy_id(self, message_id: int, chat_id: int) -> Optional[int]:
        """Get vacancy ID by message_id and chat_id."""
        return await self._jobs.get_vacancy_id(message_id, chat_id)

    # === Topic Repository Facade ===

    async def save_topic_contact(
        self,
        group_id: int,
        topic_id: int,
        contact_id: int,
        contact_name: str = None,
        agent_session: str = None,
        vacancy_id: int = None
    ) -> None:
        """Save topic-contact mapping."""
        await self._topics.save_topic_contact(
            group_id, topic_id, contact_id, contact_name, agent_session, vacancy_id
        )
        logger.debug(f"Saved mapping: topic {topic_id} -> contact {contact_id}, vacancy {vacancy_id}")

    async def get_contact_by_topic(self, group_id: int, topic_id: int) -> Optional[Dict]:
        """Get contact info by topic."""
        return await self._topics.get_contact_by_topic(group_id, topic_id)

    async def get_topic_by_contact(self, group_id: int, contact_id: int) -> Optional[int]:
        """Get topic ID by contact."""
        return await self._topics.get_topic_by_contact(group_id, contact_id)

    async def load_all_topic_contacts(self, group_id: int) -> Dict[int, int]:
        """Load all topic-contact mappings for a group."""
        return await self._topics.load_all_topic_contacts(group_id)

    async def delete_topic_contacts_by_group(self, group_id: int) -> int:
        """Delete all topic-contact mappings for a group."""
        count = await self._topics.delete_topic_contacts_by_group(group_id)
        logger.info(f"Deleted {count} crm_topic_contacts for group {group_id}")
        return count

    async def is_message_synced(self, contact_id: int, message_id: int) -> bool:
        """Check if message was already synced."""
        return await self._topics.is_message_synced(contact_id, message_id)

    async def mark_message_synced(self, contact_id: int, message_id: int) -> None:
        """Mark message as synced."""
        try:
            await self._topics.mark_message_synced(contact_id, message_id)
        except Exception as e:
            logger.warning(f"Error marking message as synced: {e}")

    # === Prompt Repository Facade ===

    async def create_prompt_version(
        self,
        prompt_type: str,
        prompt_name: str,
        content: str,
        parent_version_id: Optional[int] = None,
        created_by: str = "manual",
        is_active: bool = False
    ) -> int:
        """Create a new prompt version."""
        return await self._prompts.create_prompt_version(
            prompt_type, prompt_name, content, parent_version_id, created_by, is_active
        )

    async def get_active_prompt_version(self, prompt_type: str, prompt_name: str) -> Optional[Dict]:
        """Get the currently active prompt version."""
        return await self._prompts.get_active_prompt_version(prompt_type, prompt_name)

    async def get_prompt_version_by_id(self, version_id: int) -> Optional[Dict]:
        """Get prompt version by ID."""
        return await self._prompts.get_prompt_version_by_id(version_id)

    async def set_active_prompt_version(self, version_id: int) -> None:
        """Set a prompt version as active."""
        await self._prompts.set_active_prompt_version(version_id)

    async def save_conversation_outcome(
        self,
        contact_id: int,
        channel_id: str,
        outcome: str,
        outcome_details: Optional[str] = None,
        prompt_version_id: Optional[int] = None,
        experiment_id: Optional[int] = None,
        variant: Optional[str] = None,
        total_messages: Optional[int] = None,
        phases_visited: Optional[str] = None,
        conversation_duration_hours: Optional[float] = None
    ) -> int:
        """Save a conversation outcome."""
        return await self._prompts.save_conversation_outcome(
            contact_id, channel_id, outcome, outcome_details, prompt_version_id,
            experiment_id, variant, total_messages, phases_visited, conversation_duration_hours
        )

    async def get_outcomes_for_prompt_version(
        self,
        prompt_version_id: int,
        outcomes: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get conversation outcomes for a prompt version."""
        return await self._prompts.get_outcomes_for_prompt_version(prompt_version_id, outcomes, limit)

    async def update_conversation_outcome(
        self,
        contact_id: int,
        channel_id: str,
        outcome: str,
        outcome_details: str = None
    ) -> None:
        """Update outcome for an existing conversation."""
        await self._prompts.update_conversation_outcome(contact_id, channel_id, outcome, outcome_details)

    async def get_recent_outcomes(
        self,
        channel_id: Optional[str] = None,
        outcome_filter: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict]:
        """Get recent conversation outcomes."""
        return await self._prompts.get_recent_outcomes(channel_id, outcome_filter, limit)

    async def create_experiment(
        self,
        name: str,
        prompt_type: str,
        prompt_name: str,
        control_version_id: int,
        treatment_version_id: int,
        traffic_split: float = 0.5,
        min_sample_size: int = 30
    ) -> int:
        """Create a new A/B experiment."""
        return await self._prompts.create_experiment(
            name, prompt_type, prompt_name, control_version_id, treatment_version_id,
            traffic_split, min_sample_size
        )

    async def get_active_experiment(self, prompt_type: str, prompt_name: str) -> Optional[Dict]:
        """Get active experiment for a prompt."""
        return await self._prompts.get_active_experiment(prompt_type, prompt_name)

    async def get_experiment_stats(self, experiment_id: int) -> Dict:
        """Get statistics for an experiment."""
        return await self._prompts.get_experiment_stats(experiment_id)

    async def complete_experiment(self, experiment_id: int, winner: str) -> None:
        """Mark experiment as completed."""
        await self._prompts.complete_experiment(experiment_id, winner)

    async def get_active_experiments(self) -> List[Dict]:
        """Get all active experiments."""
        return await self._prompts.get_active_experiments()

    async def save_prompt_suggestion(
        self,
        prompt_version_id: int,
        suggested_content: str,
        reasoning: str,
        analyzed_conversation_ids: List[int],
        confidence_score: float
    ) -> int:
        """Save an LLM-generated prompt suggestion."""
        return await self._prompts.save_prompt_suggestion(
            prompt_version_id, suggested_content, reasoning,
            analyzed_conversation_ids, confidence_score
        )

    async def update_suggestion_status(self, suggestion_id: int, status: str) -> None:
        """Update suggestion status."""
        await self._prompts.update_suggestion_status(suggestion_id, status)

    async def save_contact_type_learning(
        self,
        contact_type: str,
        learning: str,
        source_conversation_ids: List[int],
        confidence_score: float
    ) -> int:
        """Save a contact type learning."""
        return await self._prompts.save_contact_type_learning(
            contact_type, learning, source_conversation_ids, confidence_score
        )

    async def get_contact_type_learnings(self, contact_type: str) -> List[Dict]:
        """Get learnings for a contact type."""
        return await self._prompts.get_contact_type_learnings(contact_type)

    async def get_prompt_metrics(self, prompt_version_id: int) -> Dict:
        """Get metrics for a prompt version."""
        return await self._prompts.get_prompt_metrics(prompt_version_id)

    async def update_prompt_metrics(self, prompt_version_id: int) -> None:
        """Recalculate and update metrics for a prompt version."""
        await self._prompts.update_prompt_metrics(prompt_version_id)

    # === Agent Repository Facade ===

    async def create_bot_interaction(
        self,
        bot_username: str,
        vacancy_id: Optional[int] = None,
        channel_id: Optional[str] = None
    ) -> int:
        """Create a new bot interaction record."""
        return await self._agents.create_bot_interaction(bot_username, vacancy_id, channel_id)

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
        await self._agents.update_bot_interaction(
            interaction_id, status, error_reason, success_message,
            messages_sent, messages_received
        )

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
        return await self._agents.save_bot_message(
            interaction_id, direction, message_text, has_buttons, button_clicked, file_sent
        )

    async def get_bot_interaction(self, interaction_id: int) -> Optional[Dict]:
        """Get bot interaction by ID."""
        return await self._agents.get_bot_interaction(interaction_id)

    async def get_bot_interactions(
        self,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict]:
        """Get bot interactions with optional status filter."""
        return await self._agents.get_bot_interactions(status, limit)

    async def check_bot_already_contacted(self, bot_username: str) -> bool:
        """Check if we already contacted this bot recently."""
        return await self._agents.check_bot_already_contacted(bot_username)

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
        """Save an auto-response attempt."""
        return await self._agents.save_auto_response_attempt(
            vacancy_id, contact_username, contact_user_id, agent_session,
            status, error_type, error_message, attempt_number
        )

    async def get_auto_response_attempts(self, vacancy_id: int) -> List[Dict]:
        """Get all auto-response attempts for a vacancy."""
        return await self._agents.get_auto_response_attempts(vacancy_id)

    async def get_latest_auto_response_status(self, vacancy_id: int) -> Optional[Dict]:
        """Get the latest auto-response attempt status for a vacancy."""
        return await self._agents.get_latest_auto_response_status(vacancy_id)

    async def get_supervisor_chat_history(self, limit: int = 50) -> List[Dict]:
        """Get supervisor chat history."""
        return await self._agents.get_supervisor_chat_history(limit)

    async def add_supervisor_message(self, role: str, content: str, tool_calls: str = None) -> None:
        """Add a message to supervisor chat history."""
        await self._agents.add_supervisor_message(role, content, tool_calls)

    async def clear_supervisor_chat_history(self) -> None:
        """Clear all supervisor chat history."""
        await self._agents.clear_supervisor_chat_history()

    # === Raw SQL Execution ===

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a raw SQL query."""
        await self._connection.execute(query, params)
        await self._connection.commit()


# Global database instance
db = Database()
