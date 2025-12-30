"""
Database repositories package.

This package contains domain-specific repository classes that handle
database operations. The main Database class in src/database.py acts
as a facade that provides access to all repositories.

Repositories:
- JobRepository: Job/vacancy operations
- TopicRepository: CRM topic-contact mapping
- PromptRepository: Self-correcting prompts and A/B testing
- AgentRepository: Bot interactions and auto-responses
"""
from src.db.migrations import MigrationRunner, MIGRATIONS
from src.db.base import BaseRepository
from src.db.jobs import JobRepository
from src.db.topics import TopicRepository
from src.db.prompts import PromptRepository
from src.db.agents import AgentRepository

__all__ = [
    "MigrationRunner",
    "MIGRATIONS",
    "BaseRepository",
    "JobRepository",
    "TopicRepository",
    "PromptRepository",
    "AgentRepository",
]
