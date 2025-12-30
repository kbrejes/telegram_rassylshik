"""
Database module for SQLite operations.

Schema creation and migrations are handled here.
For migration definitions, see src/db/migrations.py
"""
import aiosqlite
import logging
from datetime import datetime
from typing import Optional, List, Dict
from src.config import config
from src.db.migrations import MigrationRunner

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с базой данных вакансий"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DATABASE_PATH
        self._connection = None
    
    async def connect(self):
        """Подключение к базе данных"""
        self._connection = await aiosqlite.connect(self.db_path)
        await self._create_tables()
        logger.info(f"Подключено к базе данных: {self.db_path}")
    
    async def close(self):
        """Закрытие соединения"""
        if self._connection:
            await self._connection.close()
            logger.info("Соединение с базой данных закрыто")
    
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

        # Таблица для маппинга CRM топиков на контакты
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

    async def check_duplicate(self, message_id: int, chat_id: int) -> bool:
        """
        Проверяет, было ли сообщение уже обработано
        
        Returns:
            True если сообщение уже обрабатывалось, False если нет
        """
        cursor = await self._connection.execute(
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
        Сохраняет информацию об обработанной вакансии

        Returns:
            ID созданной записи
        """
        skills_str = ','.join(skills) if skills else None

        cursor = await self._connection.execute("""
            INSERT INTO processed_jobs
            (message_id, chat_id, chat_title, message_text, position, skills, is_relevant, ai_reason, status, contact_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message_id, chat_id, chat_title, message_text, position, skills_str, is_relevant, ai_reason, status, contact_username))

        await self._connection.commit()
        job_id = cursor.lastrowid
        logger.info(f"Сохранена вакансия ID={job_id} из чата {chat_title}")
        return job_id
    
    async def save_notification(self, job_id: int, template_used: str):
        """Сохраняет информацию об отправленном уведомлении"""
        await self._connection.execute(
            "INSERT INTO notifications (job_id, template_used) VALUES (?, ?)",
            (job_id, template_used)
        )
        await self._connection.commit()
        logger.info(f"Сохранено уведомление для вакансии ID={job_id}")
    
    async def get_relevant_jobs(self, limit: int = 50) -> List[Dict]:
        """Получает список релевантных вакансий"""
        cursor = await self._connection.execute("""
            SELECT id, message_id, chat_id, chat_title, position, skills, processed_at
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
                'position': row[4],
                'skills': row[5].split(',') if row[5] else [],
                'processed_at': row[6]
            })
        
        return jobs
    
    async def get_statistics(self) -> Dict:
        """Возвращает статистику по обработанным вакансиям"""
        cursor = await self._connection.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_relevant = 1 THEN 1 ELSE 0 END) as relevant,
                COUNT(DISTINCT chat_id) as unique_chats
            FROM processed_jobs
        """)

        row = await cursor.fetchone()
        return {
            'total': row[0],
            'relevant': row[1],
            'unique_chats': row[2]
        }

    async def get_vacancy_id(self, message_id: int, chat_id: int) -> Optional[int]:
        """Get vacancy ID by message_id and chat_id"""
        cursor = await self._connection.execute(
            "SELECT id FROM processed_jobs WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    # === CRM Topic-Contact методы ===

    async def save_topic_contact(
        self,
        group_id: int,
        topic_id: int,
        contact_id: int,
        contact_name: str = None,
        agent_session: str = None,
        vacancy_id: int = None
    ):
        """Сохраняет маппинг topic_id -> contact_id с опциональной привязкой к вакансии"""
        await self._connection.execute("""
            INSERT OR REPLACE INTO crm_topic_contacts
            (group_id, topic_id, contact_id, contact_name, agent_session, vacancy_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (group_id, topic_id, contact_id, contact_name, agent_session, vacancy_id))
        await self._connection.commit()
        logger.debug(f"Сохранен маппинг: topic {topic_id} -> contact {contact_id}, vacancy {vacancy_id}")

    async def get_contact_by_topic(self, group_id: int, topic_id: int) -> Optional[Dict]:
        """Находит contact_id по topic_id"""
        cursor = await self._connection.execute("""
            SELECT contact_id, contact_name, agent_session
            FROM crm_topic_contacts
            WHERE group_id = ? AND topic_id = ?
        """, (group_id, topic_id))
        row = await cursor.fetchone()
        if row:
            return {
                'contact_id': row[0],
                'contact_name': row[1],
                'agent_session': row[2]
            }
        return None

    async def get_topic_by_contact(self, group_id: int, contact_id: int) -> Optional[int]:
        """Находит topic_id по contact_id"""
        cursor = await self._connection.execute("""
            SELECT topic_id FROM crm_topic_contacts
            WHERE group_id = ? AND contact_id = ?
        """, (group_id, contact_id))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def load_all_topic_contacts(self, group_id: int) -> Dict[int, int]:
        """Загружает все маппинги для группы (topic_id -> contact_id)"""
        cursor = await self._connection.execute("""
            SELECT topic_id, contact_id FROM crm_topic_contacts WHERE group_id = ?
        """, (group_id,))
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def delete_topic_contacts_by_group(self, group_id: int) -> int:
        """Удаляет все записи crm_topic_contacts для указанной группы

        Returns:
            Количество удалённых записей
        """
        cursor = await self._connection.execute("""
            DELETE FROM crm_topic_contacts WHERE group_id = ?
        """, (group_id,))
        await self._connection.commit()
        deleted_count = cursor.rowcount
        logger.info(f"Удалено {deleted_count} записей crm_topic_contacts для группы {group_id}")
        return deleted_count

    # === Prompt Versioning Methods ===

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
        # Get next version number
        cursor = await self._connection.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_versions
            WHERE prompt_type = ? AND prompt_name = ?
        """, (prompt_type, prompt_name))
        row = await cursor.fetchone()
        version = row[0]

        cursor = await self._connection.execute("""
            INSERT INTO prompt_versions
            (prompt_type, prompt_name, version, content, parent_version_id, created_by, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (prompt_type, prompt_name, version, content, parent_version_id, created_by, is_active))
        await self._connection.commit()
        return cursor.lastrowid

    async def get_active_prompt_version(self, prompt_type: str, prompt_name: str) -> Optional[Dict]:
        """Get the currently active prompt version."""
        cursor = await self._connection.execute("""
            SELECT id, version, content, created_by, created_at
            FROM prompt_versions
            WHERE prompt_type = ? AND prompt_name = ? AND is_active = 1
        """, (prompt_type, prompt_name))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "version": row[1],
                "content": row[2],
                "created_by": row[3],
                "created_at": row[4]
            }
        return None

    async def get_prompt_version_by_id(self, version_id: int) -> Optional[Dict]:
        """Get prompt version by ID."""
        cursor = await self._connection.execute("""
            SELECT id, prompt_type, prompt_name, version, content, created_by, is_active
            FROM prompt_versions WHERE id = ?
        """, (version_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "prompt_type": row[1],
                "prompt_name": row[2],
                "version": row[3],
                "content": row[4],
                "created_by": row[5],
                "is_active": row[6]
            }
        return None

    async def set_active_prompt_version(self, version_id: int):
        """Set a prompt version as active (deactivates others of same type/name)."""
        # Get type and name
        version = await self.get_prompt_version_by_id(version_id)
        if not version:
            return

        # Deactivate all versions of same type/name
        await self._connection.execute("""
            UPDATE prompt_versions SET is_active = 0
            WHERE prompt_type = ? AND prompt_name = ?
        """, (version["prompt_type"], version["prompt_name"]))

        # Activate the specified version
        await self._connection.execute("""
            UPDATE prompt_versions SET is_active = 1 WHERE id = ?
        """, (version_id,))
        await self._connection.commit()

    # === Conversation Outcomes Methods ===

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
        cursor = await self._connection.execute("""
            INSERT INTO conversation_outcomes
            (contact_id, channel_id, outcome, outcome_details, prompt_version_id,
             experiment_id, variant, total_messages, phases_visited, conversation_duration_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (contact_id, channel_id, outcome, outcome_details, prompt_version_id,
              experiment_id, variant, total_messages, phases_visited, conversation_duration_hours))
        await self._connection.commit()
        return cursor.lastrowid

    async def get_outcomes_for_prompt_version(
        self,
        prompt_version_id: int,
        outcomes: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get conversation outcomes for a prompt version."""
        if outcomes:
            placeholders = ",".join("?" * len(outcomes))
            query = f"""
                SELECT id, contact_id, channel_id, outcome, outcome_details,
                       total_messages, phases_visited, created_at
                FROM conversation_outcomes
                WHERE prompt_version_id = ? AND outcome IN ({placeholders})
                ORDER BY created_at DESC LIMIT ?
            """
            params = [prompt_version_id] + outcomes + [limit]
        else:
            query = """
                SELECT id, contact_id, channel_id, outcome, outcome_details,
                       total_messages, phases_visited, created_at
                FROM conversation_outcomes
                WHERE prompt_version_id = ?
                ORDER BY created_at DESC LIMIT ?
            """
            params = [prompt_version_id, limit]

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "contact_id": r[1], "channel_id": r[2],
                "outcome": r[3], "outcome_details": r[4],
                "total_messages": r[5], "phases_visited": r[6], "created_at": r[7]
            }
            for r in rows
        ]

    async def update_conversation_outcome(self, contact_id: int, channel_id: str, outcome: str, outcome_details: str = None):
        """Update outcome for an existing conversation."""
        await self._connection.execute("""
            UPDATE conversation_outcomes
            SET outcome = ?, outcome_details = ?, updated_at = CURRENT_TIMESTAMP
            WHERE contact_id = ? AND channel_id = ? AND outcome = 'ongoing'
        """, (outcome, outcome_details, contact_id, channel_id))
        await self._connection.commit()

    # === A/B Experiment Methods ===

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
        cursor = await self._connection.execute("""
            INSERT INTO prompt_experiments
            (name, prompt_type, prompt_name, control_version_id, treatment_version_id,
             traffic_split, min_sample_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, prompt_type, prompt_name, control_version_id, treatment_version_id,
              traffic_split, min_sample_size))
        await self._connection.commit()
        return cursor.lastrowid

    async def get_active_experiment(self, prompt_type: str, prompt_name: str) -> Optional[Dict]:
        """Get active experiment for a prompt."""
        cursor = await self._connection.execute("""
            SELECT id, name, control_version_id, treatment_version_id, traffic_split, min_sample_size
            FROM prompt_experiments
            WHERE prompt_type = ? AND prompt_name = ? AND status = 'active'
        """, (prompt_type, prompt_name))
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0], "name": row[1],
                "control_version_id": row[2], "treatment_version_id": row[3],
                "traffic_split": row[4], "min_sample_size": row[5]
            }
        return None

    async def get_experiment_stats(self, experiment_id: int) -> Dict:
        """Get statistics for an experiment."""
        cursor = await self._connection.execute("""
            SELECT variant, outcome, COUNT(*) as count
            FROM conversation_outcomes
            WHERE experiment_id = ?
            GROUP BY variant, outcome
        """, (experiment_id,))
        rows = await cursor.fetchall()

        stats = {
            "control": {"total": 0, "success": 0, "failure": 0},
            "treatment": {"total": 0, "success": 0, "failure": 0}
        }

        for variant, outcome, count in rows:
            if variant in stats:
                stats[variant]["total"] += count
                if outcome == "call_scheduled":
                    stats[variant]["success"] += count
                elif outcome in ("disengaged", "declined"):
                    stats[variant]["failure"] += count

        # Get min_sample_size
        cursor = await self._connection.execute(
            "SELECT min_sample_size FROM prompt_experiments WHERE id = ?",
            (experiment_id,)
        )
        row = await cursor.fetchone()
        stats["min_sample_size"] = row[0] if row else 30

        return stats

    async def complete_experiment(self, experiment_id: int, winner: str):
        """Mark experiment as completed."""
        await self._connection.execute("""
            UPDATE prompt_experiments
            SET status = 'completed', end_date = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (experiment_id,))
        await self._connection.commit()

    async def get_active_experiments(self) -> List[Dict]:
        """Get all active experiments."""
        cursor = await self._connection.execute("""
            SELECT id, name, prompt_type, prompt_name, control_version_id, treatment_version_id
            FROM prompt_experiments WHERE status = 'active'
        """)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "name": r[1], "prompt_type": r[2], "prompt_name": r[3],
             "control_version_id": r[4], "treatment_version_id": r[5]}
            for r in rows
        ]

    # === Prompt Suggestions Methods ===

    async def save_prompt_suggestion(
        self,
        prompt_version_id: int,
        suggested_content: str,
        reasoning: str,
        analyzed_conversation_ids: List[int],
        confidence_score: float
    ) -> int:
        """Save an LLM-generated prompt suggestion."""
        import json
        cursor = await self._connection.execute("""
            INSERT INTO prompt_suggestions
            (prompt_version_id, suggested_content, reasoning, analyzed_conversation_ids, confidence_score)
            VALUES (?, ?, ?, ?, ?)
        """, (prompt_version_id, suggested_content, reasoning,
              json.dumps(analyzed_conversation_ids), confidence_score))
        await self._connection.commit()
        return cursor.lastrowid

    async def update_suggestion_status(self, suggestion_id: int, status: str):
        """Update suggestion status."""
        await self._connection.execute("""
            UPDATE prompt_suggestions SET status = ? WHERE id = ?
        """, (status, suggestion_id))
        await self._connection.commit()

    # === Contact Type Learnings Methods ===

    async def save_contact_type_learning(
        self,
        contact_type: str,
        learning: str,
        source_conversation_ids: List[int],
        confidence_score: float
    ) -> int:
        """Save a contact type learning."""
        import json
        cursor = await self._connection.execute("""
            INSERT INTO contact_type_learnings
            (contact_type, learning, source_conversation_ids, confidence_score)
            VALUES (?, ?, ?, ?)
        """, (contact_type, learning, json.dumps(source_conversation_ids), confidence_score))
        await self._connection.commit()
        return cursor.lastrowid

    async def get_contact_type_learnings(self, contact_type: str) -> List[Dict]:
        """Get learnings for a contact type."""
        cursor = await self._connection.execute("""
            SELECT id, learning, confidence_score
            FROM contact_type_learnings
            WHERE contact_type = ?
            ORDER BY confidence_score DESC
        """, (contact_type,))
        rows = await cursor.fetchall()
        return [{"id": r[0], "learning": r[1], "confidence_score": r[2]} for r in rows]

    # === Metrics Methods ===

    async def get_prompt_metrics(self, prompt_version_id: int) -> Dict:
        """Get metrics for a prompt version."""
        cursor = await self._connection.execute("""
            SELECT total_conversations, successful_outcomes, failed_outcomes, conversion_rate
            FROM prompt_metrics WHERE prompt_version_id = ?
        """, (prompt_version_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "total_conversations": row[0],
                "successful_outcomes": row[1],
                "failed_outcomes": row[2],
                "conversion_rate": row[3]
            }
        return {"total_conversations": 0, "successful_outcomes": 0, "failed_outcomes": 0, "conversion_rate": 0}

    async def update_prompt_metrics(self, prompt_version_id: int):
        """Recalculate and update metrics for a prompt version."""
        cursor = await self._connection.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'call_scheduled' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN outcome IN ('disengaged', 'declined') THEN 1 ELSE 0 END) as failed
            FROM conversation_outcomes
            WHERE prompt_version_id = ?
        """, (prompt_version_id,))
        row = await cursor.fetchone()

        total, success, failed = row[0] or 0, row[1] or 0, row[2] or 0
        conversion_rate = success / total if total > 0 else 0

        # Upsert metrics
        await self._connection.execute("""
            INSERT INTO prompt_metrics (prompt_version_id, total_conversations, successful_outcomes, failed_outcomes, conversion_rate)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(prompt_version_id) DO UPDATE SET
                total_conversations = ?,
                successful_outcomes = ?,
                failed_outcomes = ?,
                conversion_rate = ?,
                updated_at = CURRENT_TIMESTAMP
        """, (prompt_version_id, total, success, failed, conversion_rate, total, success, failed, conversion_rate))
        await self._connection.commit()

    async def get_recent_outcomes(
        self,
        outcome_types: Optional[List[str]] = None,
        days: int = 7
    ) -> List[Dict]:
        """Get recent conversation outcomes.

        Args:
            outcome_types: Filter by outcome types (e.g., ['declined', 'disengaged'])
            days: Number of days to look back

        Returns:
            List of outcome records
        """
        if outcome_types:
            placeholders = ",".join("?" * len(outcome_types))
            cursor = await self._connection.execute(f"""
                SELECT * FROM conversation_outcomes
                WHERE outcome IN ({placeholders})
                AND created_at >= datetime('now', ?)
                ORDER BY created_at DESC
            """, (*outcome_types, f"-{days} days"))
        else:
            cursor = await self._connection.execute("""
                SELECT * FROM conversation_outcomes
                WHERE created_at >= datetime('now', ?)
                ORDER BY created_at DESC
            """, (f"-{days} days",))

        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a raw SQL query.

        Args:
            query: SQL query to execute
            params: Query parameters
        """
        await self._connection.execute(query, params)
        await self._connection.commit()

    # === Bot Interaction Methods ===

    async def create_bot_interaction(
        self,
        bot_username: str,
        vacancy_id: Optional[int] = None,
        channel_id: Optional[str] = None
    ) -> int:
        """Create a new bot interaction record."""
        cursor = await self._connection.execute("""
            INSERT INTO bot_interactions (bot_username, vacancy_id, channel_id, status, started_at)
            VALUES (?, ?, ?, 'in_progress', CURRENT_TIMESTAMP)
        """, (bot_username, vacancy_id, channel_id))
        await self._connection.commit()
        return cursor.lastrowid

    async def update_bot_interaction(
        self,
        interaction_id: int,
        status: str,
        error_reason: Optional[str] = None,
        success_message: Optional[str] = None,
        messages_sent: Optional[int] = None,
        messages_received: Optional[int] = None
    ):
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
        await self._connection.execute(
            f"UPDATE bot_interactions SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )
        await self._connection.commit()

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
        cursor = await self._connection.execute("""
            INSERT INTO bot_messages
            (interaction_id, direction, message_text, has_buttons, button_clicked, file_sent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (interaction_id, direction, message_text, 1 if has_buttons else 0, button_clicked, file_sent))
        await self._connection.commit()
        return cursor.lastrowid

    async def get_bot_interaction(self, interaction_id: int) -> Optional[Dict]:
        """Get bot interaction by ID."""
        cursor = await self._connection.execute("""
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
            cursor = await self._connection.execute("""
                SELECT id, bot_username, vacancy_id, status, started_at, completed_at,
                       messages_sent, error_reason, success_message
                FROM bot_interactions WHERE status = ?
                ORDER BY created_at DESC LIMIT ?
            """, (status, limit))
        else:
            cursor = await self._connection.execute("""
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
        cursor = await self._connection.execute("""
            SELECT id FROM bot_interactions
            WHERE bot_username = ?
            AND created_at >= datetime('now', '-24 hours')
        """, (bot_username,))
        row = await cursor.fetchone()
        return row is not None

    # === Supervisor Chat History Methods ===

    async def get_supervisor_chat_history(self, limit: int = 50) -> List[Dict]:
        """Get supervisor chat history."""
        cursor = await self._connection.execute("""
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

    async def add_supervisor_message(self, role: str, content: str, tool_calls: str = None):
        """Add a message to supervisor chat history."""
        await self._connection.execute("""
            INSERT INTO supervisor_chat_history (role, content, tool_calls)
            VALUES (?, ?, ?)
        """, (role, content, tool_calls))
        await self._connection.commit()

    async def clear_supervisor_chat_history(self):
        """Clear all supervisor chat history."""
        await self._connection.execute("DELETE FROM supervisor_chat_history")
        await self._connection.commit()

    # === Synced CRM Messages ===

    async def is_message_synced(self, contact_id: int, message_id: int) -> bool:
        """Check if a message was already synced to CRM."""
        async with self._connection.execute(
            "SELECT 1 FROM synced_crm_messages WHERE contact_id = ? AND message_id = ?",
            (contact_id, message_id)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_message_synced(self, contact_id: int, message_id: int):
        """Mark a message as synced to CRM."""
        try:
            await self._connection.execute("""
                INSERT OR IGNORE INTO synced_crm_messages (contact_id, message_id)
                VALUES (?, ?)
            """, (contact_id, message_id))
            await self._connection.commit()
        except Exception as e:
            logger.warning(f"Error marking message as synced: {e}")

    # === Auto-Response Attempt Methods ===

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
        """Save an auto-response attempt.

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
        cursor = await self._connection.execute("""
            INSERT INTO auto_response_attempts
            (vacancy_id, contact_username, contact_user_id, agent_session, status, error_type, error_message, attempt_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (vacancy_id, contact_username, contact_user_id, agent_session, status, error_type, error_message, attempt_number))
        await self._connection.commit()
        return cursor.lastrowid

    async def get_auto_response_attempts(self, vacancy_id: int) -> List[Dict]:
        """Get all auto-response attempts for a vacancy."""
        cursor = await self._connection.execute("""
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
        cursor = await self._connection.execute("""
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


# Глобальный экземпляр базы данных
db = Database()

