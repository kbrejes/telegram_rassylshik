"""Database models for Channel Discovery"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime,
    Text, JSON, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class DiscoveredChannel(Base):
    """Discovered Telegram channels"""
    __tablename__ = "discovered_channels"

    id = Column(Integer, primary_key=True)

    # Channel info
    telegram_id = Column(Integer, unique=True, nullable=True)
    username = Column(String(255), unique=True, index=True)
    title = Column(String(500))
    description = Column(Text, nullable=True)

    # Stats
    subscribers = Column(Integer, default=0)
    posts_per_week = Column(Float, default=0)
    avg_views = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0)  # views/subscribers ratio

    # Activity
    last_post_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

    # Discovery metadata
    discovery_source = Column(String(50))  # 'keyword', 'forward', 'mention', 'seed'
    discovered_from = Column(String(255), nullable=True)  # seed channel username
    discovery_keywords = Column(JSON, nullable=True)  # keywords that found this

    # Scoring
    relevance_score = Column(Float, default=0)
    discovery_count = Column(Integer, default=1)  # times discovered from different sources

    # Management
    added_to_monitoring = Column(Boolean, default=False)
    ignored = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    # Timestamps
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_checked = Column(DateTime, default=datetime.utcnow)
    stats_updated = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "telegram_id": self.telegram_id,
            "username": self.username,
            "title": self.title,
            "description": self.description,
            "subscribers": self.subscribers,
            "posts_per_week": self.posts_per_week,
            "avg_views": self.avg_views,
            "engagement_rate": self.engagement_rate,
            "last_post_date": self.last_post_date.isoformat() if self.last_post_date else None,
            "is_active": self.is_active,
            "discovery_source": self.discovery_source,
            "discovered_from": self.discovered_from,
            "discovery_keywords": self.discovery_keywords,
            "relevance_score": self.relevance_score,
            "discovery_count": self.discovery_count,
            "added_to_monitoring": self.added_to_monitoring,
            "ignored": self.ignored,
            "notes": self.notes,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


class SeedChannel(Base):
    """Seed channels for discovery (your known good channels)"""
    __tablename__ = "seed_channels"

    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, index=True)
    title = Column(String(500), nullable=True)
    category = Column(String(100), nullable=True)  # e.g., 'jobs', 'marketing'
    is_active = Column(Boolean, default=True)
    last_scanned = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "title": self.title,
            "category": self.category,
            "is_active": self.is_active,
            "last_scanned": self.last_scanned.isoformat() if self.last_scanned else None,
        }


class SearchJob(Base):
    """Search/discovery job tracking"""
    __tablename__ = "search_jobs"

    id = Column(Integer, primary_key=True)
    job_id = Column(String(36), unique=True, index=True)  # UUID

    # Search parameters
    keywords = Column(JSON)  # list of keywords
    min_subscribers = Column(Integer, nullable=True)
    max_subscribers = Column(Integer, nullable=True)
    min_posts_per_week = Column(Float, nullable=True)
    use_seed_channels = Column(Boolean, default=False)
    seed_channel_ids = Column(JSON, nullable=True)  # list of seed channel IDs

    # Status
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    progress = Column(Integer, default=0)  # percentage
    current_step = Column(String(255), nullable=True)

    # Results
    channels_found = Column(Integer, default=0)
    channels_new = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "keywords": self.keywords,
            "min_subscribers": self.min_subscribers,
            "max_subscribers": self.max_subscribers,
            "min_posts_per_week": self.min_posts_per_week,
            "use_seed_channels": self.use_seed_channels,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "channels_found": self.channels_found,
            "channels_new": self.channels_new,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
