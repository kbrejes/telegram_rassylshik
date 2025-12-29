"""Telegram client wrapper for discovery operations with safety measures"""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.account import GetAuthorizationsRequest
from telethon.tl.types import Channel
from telethon.errors import (
    FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError,
    UsernameInvalidError, AuthKeyUnregisteredError, UserDeactivatedError,
    UserDeactivatedBanError, SessionRevokedError, AuthKeyDuplicatedError
)

import sys
sys.path.append(str(__file__).rsplit("/", 2)[0])

from config import API_ID, API_HASH, SESSION_PATH

logger = logging.getLogger(__name__)


@dataclass
class AccountStatus:
    """Track account health and status"""
    is_connected: bool = False
    is_authorized: bool = False
    is_blocked: bool = False
    block_reason: Optional[str] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    user_id: Optional[int] = None
    last_check: Optional[datetime] = None
    flood_wait_until: Optional[datetime] = None
    total_requests: int = 0
    errors_count: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_connected": self.is_connected,
            "is_authorized": self.is_authorized,
            "is_blocked": self.is_blocked,
            "block_reason": self.block_reason,
            "username": self.username,
            "first_name": self.first_name,
            "user_id": self.user_id,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "flood_wait_until": self.flood_wait_until.isoformat() if self.flood_wait_until else None,
            "flood_wait_remaining": self._flood_wait_remaining(),
            "total_requests": self.total_requests,
            "errors_count": self.errors_count,
            "last_error": self.last_error,
            "health": self._health_status()
        }

    def _flood_wait_remaining(self) -> Optional[int]:
        if self.flood_wait_until:
            remaining = (self.flood_wait_until - datetime.utcnow()).total_seconds()
            return int(remaining) if remaining > 0 else None
        return None

    def _health_status(self) -> str:
        if self.is_blocked:
            return "blocked"
        if not self.is_connected:
            return "disconnected"
        if not self.is_authorized:
            return "unauthorized"
        if self._flood_wait_remaining():
            return "rate_limited"
        if self.errors_count > 10:
            return "degraded"
        return "healthy"


@dataclass
class SafetyConfig:
    """Safety configuration to prevent account blocks"""
    # Rate limits (much more conservative)
    min_delay_between_requests: float = 3.0  # seconds
    min_delay_between_searches: float = 5.0  # seconds
    min_delay_between_channel_fetches: float = 2.0  # seconds

    # Batch limits
    max_searches_per_session: int = 20  # max keyword searches
    max_channels_per_session: int = 50  # max channels to enrich
    max_seed_channels_per_session: int = 10  # max seed channels to analyze
    max_posts_per_channel: int = 30  # max posts to fetch per channel

    # Cool-down periods
    session_cooldown_minutes: int = 30  # pause between sessions
    flood_wait_multiplier: float = 1.5  # multiply flood wait time for safety

    # Error thresholds
    max_errors_before_pause: int = 5  # pause after this many errors
    error_pause_minutes: int = 10  # pause duration after errors


class DiscoveryClient:
    """Telegram client for channel discovery with safety measures"""

    def __init__(self, safety_config: Optional[SafetyConfig] = None):
        self.client: Optional[TelegramClient] = None
        self.status = AccountStatus()
        self.safety = safety_config or SafetyConfig()

        # Session tracking
        self._session_start: Optional[datetime] = None
        self._session_searches: int = 0
        self._session_channels: int = 0
        self._last_request_time: float = 0

    async def connect(self) -> bool:
        """Connect to Telegram and check account status"""
        try:
            self.client = TelegramClient(
                str(SESSION_PATH),
                API_ID,
                API_HASH,
                system_version="4.16.30-vxCUSTOM"
            )
            await self.client.connect()
            self.status.is_connected = True

            if not await self.client.is_user_authorized():
                self.status.is_authorized = False
                logger.warning("Client not authorized. Please run auth first.")
                return False

            self.status.is_authorized = True
            me = await self.client.get_me()

            self.status.username = me.username
            self.status.first_name = me.first_name
            self.status.user_id = me.id
            self.status.last_check = datetime.utcnow()
            self.status.is_blocked = False
            self.status.block_reason = None

            self._session_start = datetime.utcnow()

            logger.info(f"Connected as {me.first_name} (@{me.username})")
            return True

        except (UserDeactivatedError, UserDeactivatedBanError) as e:
            self.status.is_blocked = True
            self.status.block_reason = str(e)
            self.status.last_error = f"Account blocked: {e}"
            logger.error(f"ACCOUNT BLOCKED: {e}")
            return False

        except (AuthKeyUnregisteredError, SessionRevokedError, AuthKeyDuplicatedError) as e:
            self.status.is_authorized = False
            self.status.last_error = f"Session invalid: {e}"
            logger.error(f"Session error: {e}")
            return False

        except Exception as e:
            self.status.last_error = str(e)
            self.status.errors_count += 1
            logger.error(f"Failed to connect: {e}")
            return False

    async def check_account_status(self) -> AccountStatus:
        """Check current account status and health"""
        if not self.client or not self.status.is_connected:
            await self.connect()

        try:
            me = await self.client.get_me()
            self.status.is_authorized = True
            self.status.is_blocked = False
            self.status.username = me.username
            self.status.first_name = me.first_name
            self.status.user_id = me.id
            self.status.last_check = datetime.utcnow()

        except (UserDeactivatedError, UserDeactivatedBanError) as e:
            self.status.is_blocked = True
            self.status.block_reason = str(e)
            logger.error(f"Account blocked: {e}")

        except Exception as e:
            self.status.last_error = str(e)
            self.status.errors_count += 1

        return self.status

    async def disconnect(self):
        """Disconnect from Telegram"""
        if self.client:
            await self.client.disconnect()
            self.status.is_connected = False

    async def _safe_delay(self, min_delay: float):
        """Apply rate limiting with safety margin"""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time

        if elapsed < min_delay:
            wait_time = min_delay - elapsed
            logger.debug(f"Safety delay: {wait_time:.1f}s")
            await asyncio.sleep(wait_time)

        self._last_request_time = asyncio.get_event_loop().time()
        self.status.total_requests += 1

    def _check_session_limits(self) -> Optional[str]:
        """Check if session limits are reached"""
        if self._session_searches >= self.safety.max_searches_per_session:
            return f"Max searches ({self.safety.max_searches_per_session}) reached"
        if self._session_channels >= self.safety.max_channels_per_session:
            return f"Max channels ({self.safety.max_channels_per_session}) reached"
        if self.status.errors_count >= self.safety.max_errors_before_pause:
            return f"Too many errors ({self.status.errors_count})"
        return None

    async def _handle_flood_wait(self, seconds: int):
        """Handle flood wait with safety multiplier"""
        wait_time = int(seconds * self.safety.flood_wait_multiplier)
        self.status.flood_wait_until = datetime.utcnow() + timedelta(seconds=wait_time)
        logger.warning(f"FloodWait: waiting {wait_time}s (original: {seconds}s)")
        await asyncio.sleep(wait_time)
        self.status.flood_wait_until = None

    async def _handle_error(self, error: Exception, context: str) -> bool:
        """Handle errors and detect account issues. Returns True if should retry."""
        error_str = str(error)
        self.status.errors_count += 1
        self.status.last_error = f"{context}: {error_str}"

        # Check for block indicators
        block_indicators = [
            "account was blocked",
            "account is banned",
            "user is deactivated",
            "auth key unregistered",
            "session revoked"
        ]

        for indicator in block_indicators:
            if indicator.lower() in error_str.lower():
                self.status.is_blocked = True
                self.status.block_reason = error_str
                logger.error(f"ACCOUNT ISSUE DETECTED: {error_str}")
                return False

        # Pause if too many errors
        if self.status.errors_count >= self.safety.max_errors_before_pause:
            pause_time = self.safety.error_pause_minutes * 60
            logger.warning(f"Too many errors, pausing for {self.safety.error_pause_minutes} minutes")
            await asyncio.sleep(pause_time)
            self.status.errors_count = 0

        return True

    async def search_channels(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search for channels by keyword with safety measures"""
        # Check limits
        limit_msg = self._check_session_limits()
        if limit_msg:
            logger.warning(f"Session limit: {limit_msg}")
            return []

        if self.status.is_blocked:
            logger.error("Cannot search: account is blocked")
            return []

        if not self.status.is_connected:
            if not await self.connect():
                return []

        await self._safe_delay(self.safety.min_delay_between_searches)

        try:
            # Use smaller limit for safety
            safe_limit = min(limit, 50)
            result = await self.client(SearchRequest(q=query, limit=safe_limit))
            channels = []

            for chat in result.chats:
                if isinstance(chat, Channel) and chat.broadcast:
                    channels.append({
                        "telegram_id": chat.id,
                        "username": chat.username,
                        "title": chat.title,
                        "subscribers": getattr(chat, "participants_count", 0),
                    })

            self._session_searches += 1
            logger.info(f"Search '{query}' found {len(channels)} channels (session: {self._session_searches}/{self.safety.max_searches_per_session})")
            return channels

        except FloodWaitError as e:
            await self._handle_flood_wait(e.seconds)
            return []  # Don't retry, let caller decide

        except (UserDeactivatedError, UserDeactivatedBanError) as e:
            await self._handle_error(e, "search")
            return []

        except Exception as e:
            should_retry = await self._handle_error(e, f"search '{query}'")
            logger.error(f"Search error: {e}")
            return []

    async def get_channel_stats(self, username: str) -> Optional[Dict[str, Any]]:
        """Get detailed channel statistics with safety measures"""
        if self.status.is_blocked:
            return None

        limit_msg = self._check_session_limits()
        if limit_msg:
            logger.warning(f"Session limit: {limit_msg}")
            return None

        if not self.status.is_connected:
            if not await self.connect():
                return None

        await self._safe_delay(self.safety.min_delay_between_channel_fetches)

        try:
            entity = await self.client.get_entity(username)
            if not isinstance(entity, Channel):
                return None

            full = await self.client(GetFullChannelRequest(entity))

            # Get recent posts for activity analysis (limited for safety)
            posts = await self.client.get_messages(entity, limit=min(20, self.safety.max_posts_per_channel))

            posts_per_week = 0
            avg_views = 0
            last_post_date = None

            if posts:
                last_post_date = posts[0].date
                week_ago = datetime.utcnow() - timedelta(days=7)
                recent_posts = [p for p in posts if p.date.replace(tzinfo=None) > week_ago]
                posts_per_week = len(recent_posts)

                views = [p.views for p in posts if p.views]
                avg_views = sum(views) // len(views) if views else 0

            subscribers = full.full_chat.participants_count or 0
            engagement = (avg_views / subscribers * 100) if subscribers > 0 else 0

            self._session_channels += 1

            return {
                "telegram_id": entity.id,
                "username": entity.username,
                "title": entity.title,
                "description": full.full_chat.about,
                "subscribers": subscribers,
                "posts_per_week": posts_per_week,
                "avg_views": avg_views,
                "engagement_rate": round(engagement, 2),
                "last_post_date": last_post_date,
                "is_active": last_post_date and (
                    datetime.utcnow() - last_post_date.replace(tzinfo=None)
                ).days < 30
            }

        except (ChannelPrivateError, UsernameNotOccupiedError, UsernameInvalidError) as e:
            logger.warning(f"Cannot access @{username}: {e}")
            return None

        except FloodWaitError as e:
            await self._handle_flood_wait(e.seconds)
            return None

        except (UserDeactivatedError, UserDeactivatedBanError) as e:
            await self._handle_error(e, f"get_channel_stats @{username}")
            return None

        except Exception as e:
            await self._handle_error(e, f"stats @{username}")
            return None

    async def get_channel_posts(
        self, username: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get recent posts from a channel with safety measures"""
        if self.status.is_blocked:
            return []

        if not self.status.is_connected:
            if not await self.connect():
                return []

        await self._safe_delay(self.safety.min_delay_between_requests)

        try:
            entity = await self.client.get_entity(username)
            # Limit posts for safety
            safe_limit = min(limit, self.safety.max_posts_per_channel)
            messages = await self.client.get_messages(entity, limit=safe_limit)

            posts = []
            for msg in messages:
                if msg.text or msg.message:
                    posts.append({
                        "id": msg.id,
                        "date": msg.date,
                        "text": msg.text or msg.message,
                        "views": msg.views,
                        "forwards": msg.forwards,
                        "fwd_from": self._extract_forward_info(msg),
                        "mentions": self._extract_mentions(msg.text or msg.message or ""),
                    })

            return posts

        except FloodWaitError as e:
            await self._handle_flood_wait(e.seconds)
            return []

        except Exception as e:
            await self._handle_error(e, f"get_posts @{username}")
            return []

    def _extract_forward_info(self, msg) -> Optional[Dict[str, Any]]:
        """Extract forward source info from message"""
        if not msg.fwd_from:
            return None

        fwd = msg.fwd_from
        if hasattr(fwd, "from_id") and fwd.from_id:
            return {
                "channel_id": getattr(fwd.from_id, "channel_id", None),
                "from_name": fwd.from_name,
            }
        return None

    def _extract_mentions(self, text: str) -> List[str]:
        """Extract @username mentions from text"""
        pattern = r"@([a-zA-Z][a-zA-Z0-9_]{4,31})"
        return re.findall(pattern, text)

    async def resolve_channel_id(self, channel_id: int) -> Optional[str]:
        """Resolve channel ID to username"""
        if self.status.is_blocked:
            return None

        await self._safe_delay(self.safety.min_delay_between_requests)

        try:
            entity = await self.client.get_entity(channel_id)
            return entity.username
        except Exception:
            return None

    def reset_session_counters(self):
        """Reset session counters for a new session"""
        self._session_searches = 0
        self._session_channels = 0
        self._session_start = datetime.utcnow()
        self.status.errors_count = 0
        logger.info("Session counters reset")

    def get_session_stats(self) -> Dict[str, Any]:
        """Get current session statistics"""
        return {
            "session_start": self._session_start.isoformat() if self._session_start else None,
            "searches": self._session_searches,
            "searches_limit": self.safety.max_searches_per_session,
            "channels": self._session_channels,
            "channels_limit": self.safety.max_channels_per_session,
            "total_requests": self.status.total_requests,
            "errors": self.status.errors_count,
        }


# Singleton instance
_client: Optional[DiscoveryClient] = None


async def get_client() -> DiscoveryClient:
    """Get or create discovery client"""
    global _client
    if _client is None:
        _client = DiscoveryClient()
    if not _client.status.is_connected:
        await _client.connect()
    return _client


async def get_account_status() -> AccountStatus:
    """Get current account status"""
    global _client
    if _client is None:
        _client = DiscoveryClient()
    return await _client.check_account_status()
