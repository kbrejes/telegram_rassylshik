"""
Модуль для работы с базой данных SQLite
"""
import aiosqlite
import logging
from datetime import datetime
from typing import Optional, List, Dict
from config import config

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
    
    async def _create_tables(self):
        """Создание таблиц в базе данных"""
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
        
        await self._connection.commit()
        logger.info("Таблицы созданы/проверены")
    
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
        status: str = 'processed'
    ) -> int:
        """
        Сохраняет информацию об обработанной вакансии
        
        Returns:
            ID созданной записи
        """
        skills_str = ','.join(skills) if skills else None
        
        cursor = await self._connection.execute("""
            INSERT INTO processed_jobs 
            (message_id, chat_id, chat_title, message_text, position, skills, is_relevant, ai_reason, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message_id, chat_id, chat_title, message_text, position, skills_str, is_relevant, ai_reason, status))
        
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


# Глобальный экземпляр базы данных
db = Database()

