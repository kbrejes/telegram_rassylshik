"""
Agent account management for Telegram user accounts
Adapted from crm_response_bot for job_notification_bot
"""
import asyncio
import logging
import time
from typing import Any, Optional, Union
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.tl.types import User, InputPeerUser
from src.config import config
from utils.retry import FloodWaitTracker, format_wait_time
from src.session_config import get_agent_session_path, delete_session_file
from auth.base import TimeoutSQLiteSession
from src.connection_status import status_manager

logger = logging.getLogger(__name__)


class AgentAccount:
    """Represents a Telegram agent account for auto-responses"""

    def __init__(
        self,
        session_name: str,
        phone: Optional[str] = None
    ):
        """
        Initialize agent

        Args:
            session_name: Session file name (without path, name only)
            phone: Phone number (required for first login)
        """
        # Use absolute path from session_config
        self.session_name = get_agent_session_path(session_name)
        self.phone = phone
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._flood_tracker = FloodWaitTracker()
        # Store event loop where client was connected
        self._connected_loop: Optional[asyncio.AbstractEventLoop] = None
        # Last connection error for status reporting
        self.last_connect_error: Optional[str] = None
    
    async def connect(self) -> bool:
        """
        Connect to Telegram

        Returns:
            True if connection successful
        """
        try:
            # Use TimeoutSQLiteSession to avoid "database is locked"
            session = TimeoutSQLiteSession(self.session_name)
            self.client = TelegramClient(
                session,
                config.API_ID,
                config.API_HASH
            )

            await self.client.connect()

            if not await self.client.is_user_authorized():
                if not self.phone:
                    logger.error(f"Agent {self.session_name}: Phone number required for first login")
                    return False

                logger.info(f"Agent {self.session_name}: Starting authentication...")
                await self.client.send_code_request(self.phone)
                logger.info(f"Agent {self.session_name}: Code sent to {self.phone}")

                # Will prompt for code in terminal
                await self.client.start(phone=self.phone)

            self._is_connected = True
            # Save the event loop where we connected
            self._connected_loop = asyncio.get_running_loop()
            me = await self.client.get_me()
            username = f"@{me.username}" if me.username else "no username"
            logger.info(f"Agent {self.session_name} connected: {me.first_name} ({username})")

            # Important: request updates to receive messages
            try:
                await self.client.catch_up()
                logger.debug(f"Agent {self.session_name}: catch_up completed")
            except Exception as e:
                logger.warning(f"Agent {self.session_name}: catch_up error: {e}")

            return True

        except errors.AuthKeyDuplicatedError:
            # Session used from different IP - need to recreate
            logger.error(f"Agent {self.session_name}: AuthKeyDuplicatedError - session corrupted, deleting")
            delete_session_file(self.session_name)
            self._is_connected = False
            self.last_connect_error = "Session corrupted (AuthKeyDuplicated). Re-add the agent."
            return False

        except Exception as e:
            error_str = str(e)
            error_lower = error_str.lower()
            if "database is locked" in error_lower:
                logger.warning(f"Agent {self.session_name}: Session locked by another process")
                self.last_connect_error = "Session file locked by another process"
            elif "all available options" in error_lower or "resendcoderequest" in error_lower:
                logger.error(f"Agent {self.session_name}: Connection error: {e}")
                self.last_connect_error = "Telegram rate-limited code requests. Wait 30-60 minutes."
            else:
                logger.error(f"Agent {self.session_name}: Connection error: {e}")
                self.last_connect_error = error_str
            self._is_connected = False
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from Telegram"""
        if self.client:
            await self.client.disconnect()
            self._is_connected = False
            self._connected_loop = None
            logger.info(f"Agent {self.session_name} disconnected")

    def is_valid_loop(self) -> bool:
        """
        Checks that we are in the same event loop where client was connected.

        IMPORTANT: Does not attempt to reconnect! Reconnecting from another thread
        will break the agent for the main bot thread.

        Returns:
            True if current loop matches the connection loop
        """
        if not self._is_connected or not self.client:
            return False

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return False

        if self._connected_loop is current_loop:
            return True

        # Loop changed - this means call from wrong thread
        logger.error(
            f"Agent {self.session_name}: Attempt to use from wrong event loop! "
            f"Agents from agent_pool can only be used from bot thread."
        )
        return False

    async def send_message(
        self,
        user: Union[str, int, User, InputPeerUser, Any],
        text: str
    ) -> bool:
        """
        Send message to user

        Args:
            user: Username (with or without @), user ID, User object, or InputPeerUser
            text: Message text

        Returns:
            True if message sent successfully
        """
        if not self._is_connected or not self.client:
            logger.error(f"Agent {self.session_name}: Not connected")
            return False

        if not self.is_available():
            logger.warning(f"Agent {self.session_name}: Unavailable (FloodWait)")
            return False

        # Check that we are in the correct event loop
        if not self.is_valid_loop():
            return False

        try:
            # Normalize username only for strings (not for InputPeerUser or int)
            target = user
            if isinstance(user, str) and not user.startswith('@'):
                target = f"@{user}"

            # If InputPeerUser is passed, resolve the entity ourselves
            # because access_hash is session-specific (bot's hash won't work for agent)
            if isinstance(user, InputPeerUser):
                logger.debug(f"Agent {self.session_name}: InputPeerUser received, resolving user_id={user.user_id}")
                try:
                    # Try to get entity by user_id using agent's own client
                    target = await self.client.get_entity(user.user_id)
                    logger.debug(f"Agent {self.session_name}: Successfully resolved to {target}")
                except Exception as resolve_err:
                    logger.debug(f"Agent {self.session_name}: Failed to resolve: {resolve_err}")
                    # Fall back to the original InputPeerUser
                    target = user

            await self.client.send_message(target, text)
            logger.info(f"Agent {self.session_name}: Message sent to {user}")
            return True

        except errors.FloodWaitError as e:
            logger.warning(f"Agent {self.session_name}: FloodWait {e.seconds} seconds")
            self.handle_flood_wait(e.seconds)
            return False

        except errors.PeerFloodError:
            # Spam limitation from Telegram - treat as 1 hour block
            logger.warning(f"Agent {self.session_name}: PeerFlood (spam limitation), blocked for 1 hour")
            self.handle_flood_wait(3600)  # 1 hour
            return False

        except errors.UserIsBlockedError:
            logger.error(f"Agent {self.session_name}: User {user} blocked the account")
            return False

        except errors.UserPrivacyRestrictedError:
            logger.error(f"Agent {self.session_name}: Cannot message {user} due to privacy settings")
            return False

        except Exception as e:
            error_str = str(e).lower()
            # Check for spam-related errors in exception message
            if "spam" in error_str or "flood" in error_str or "limit" in error_str:
                logger.warning(f"Agent {self.session_name}: Possible spam limitation: {e}")
                self.handle_flood_wait(1800)  # 30 min
                return False
            logger.error(f"Agent {self.session_name}: Error sending to {user}: {e}")
            return False
    
    @property
    def flood_wait_until(self) -> Optional[float]:
        """Time until which FloodWait is active (for AgentPool compatibility)"""
        return self._flood_tracker.flood_wait_until

    def is_available(self) -> bool:
        """
        Check agent availability for sending messages

        Returns:
            True if agent is not in FloodWait and connected
        """
        if not self._is_connected:
            return False
        return not self._flood_tracker.is_blocked

    def handle_flood_wait(self, seconds: int) -> None:
        """
        Handle FloodWait error

        Args:
            seconds: Number of seconds to wait
        """
        self._flood_tracker.set_flood_wait(seconds)
        flood_wait_until = time.time() + seconds

        # Update status with flood wait info, preserving user_info
        session_name_only = Path(self.session_name).name

        # Get existing user_info from status to preserve it
        existing_status = status_manager.get_all_status()
        existing_agent = existing_status.get("agents", {}).get(session_name_only, {})
        user_info = existing_agent.get("user_info")

        status_manager.update_agent_status(
            session_name_only,
            "flood_wait",
            self.phone or "",
            flood_wait_until=flood_wait_until,
            user_info=user_info
        )

        logger.warning(
            f"Agent {self.session_name}: Unavailable {format_wait_time(seconds)}"
        )

    async def get_me(self):
        """Get current user information"""
        if not self._is_connected or not self.client:
            return None
        return await self.client.get_me()

    async def health_check(self) -> bool:
        """
        Check that agent session is valid and working

        Returns:
            True if agent is connected and authorized
        """
        if not self._is_connected or not self.client:
            return False
        try:
            await self.client.get_me()
            return True
        except Exception as e:
            logger.warning(f"Agent {self.session_name}: health check failed: {e}")
            self._is_connected = False
            return False

    def get_remaining_flood_wait(self) -> int:
        """Returns remaining FloodWait time in seconds"""
        return self._flood_tracker.remaining_seconds

