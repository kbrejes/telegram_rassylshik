"""
Message Queue for Rate-Limited Auto-Responses

Stores failed messages and retries them when agents recover from spam limits.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Callable, Awaitable
from datetime import datetime

from src.constants import (
    MESSAGE_QUEUE_MAX_RETRIES,
    MAX_MESSAGE_AGE_HOURS,
    MESSAGE_QUEUE_RETRY_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

# Send callback return values:
# - True: Message sent successfully
# - False: Send attempted but failed (counts toward retry limit)
# - None: No agent available, skip this cycle (does NOT count toward retry limit)
SendResult = Optional[bool]


@dataclass
class QueuedMessage:
    """A message waiting to be sent."""
    contact: str  # @username
    text: str
    channel_id: str
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_error: Optional[str] = None
    resolved_user_id: Optional[int] = None  # Pre-resolved user ID
    resolved_access_hash: Optional[int] = None  # Access hash for InputPeerUser

    # Unique key for deduplication
    @property
    def key(self) -> str:
        return f"{self.channel_id}:{self.contact.lower()}"


class MessageQueue:
    """
    Queue for storing and retrying failed auto-response messages.

    Messages are stored when all agents are in spam limit.
    A background task periodically checks if agents recovered
    and retries the messages.
    """

    MAX_RETRIES = MESSAGE_QUEUE_MAX_RETRIES
    MAX_AGE_HOURS = MAX_MESSAGE_AGE_HOURS
    RETRY_INTERVAL_SECONDS = MESSAGE_QUEUE_RETRY_INTERVAL_SECONDS

    def __init__(self) -> None:
        self._queue: Dict[str, QueuedMessage] = {}  # key -> message
        self._lock = asyncio.Lock()
        self._retry_task: Optional[asyncio.Task] = None
        # Callback signature: (contact, text, channel_id, user_id, access_hash) -> SendResult
        # Returns: True=success, False=failed (retry), None=no agent (skip, don't count)
        self._send_callback: Optional[Callable[[str, str, str, Optional[int], Optional[int]], Awaitable[SendResult]]] = None

    def set_send_callback(
        self,
        callback: Callable[[str, str, str, Optional[int], Optional[int]], Awaitable[SendResult]]
    ) -> None:
        """
        Set the callback for sending messages.

        Args:
            callback: async function(contact, text, channel_id, user_id, access_hash) -> SendResult
                      Returns True on success, False on failure (counts retry),
                      None if no agent available (skips, doesn't count retry)
        """
        self._send_callback = callback

    async def add(
        self,
        contact: str,
        text: str,
        channel_id: str,
        error: Optional[str] = None,
        resolved_user_id: Optional[int] = None,
        resolved_access_hash: Optional[int] = None
    ) -> bool:
        """
        Add a message to the queue.

        Args:
            contact: @username to send to
            text: Message text
            channel_id: Channel ID for routing
            error: Error message from failed attempt
            resolved_user_id: Pre-resolved user ID (if available)
            resolved_access_hash: Access hash for InputPeerUser (if available)

        Returns:
            True if message was added (not duplicate)
        """
        msg = QueuedMessage(
            contact=contact,
            text=text,
            channel_id=channel_id,
            last_error=error,
            resolved_user_id=resolved_user_id,
            resolved_access_hash=resolved_access_hash
        )

        async with self._lock:
            if msg.key in self._queue:
                # Update existing message with new error
                existing = self._queue[msg.key]
                existing.last_error = error
                logger.debug(f"[QUEUE] Updated existing message for {contact}")
                return False

            self._queue[msg.key] = msg
            logger.info(
                f"[QUEUE] Added message to queue: {contact} "
                f"(channel={channel_id}, error={error})"
            )
            return True

    async def remove(self, contact: str, channel_id: str) -> bool:
        """Remove a message from the queue (e.g., after successful send)."""
        key = f"{channel_id}:{contact.lower()}"
        async with self._lock:
            if key in self._queue:
                del self._queue[key]
                logger.debug(f"[QUEUE] Removed message for {contact}")
                return True
            return False

    async def get_pending_count(self) -> int:
        """Get number of messages in queue."""
        async with self._lock:
            return len(self._queue)

    async def get_pending_messages(self) -> List[QueuedMessage]:
        """Get all pending messages (copy)."""
        async with self._lock:
            return list(self._queue.values())

    async def _cleanup_old_messages(self) -> None:
        """Remove messages older than MAX_AGE_HOURS."""
        cutoff = time.time() - (self.MAX_AGE_HOURS * 3600)
        removed = 0

        async with self._lock:
            keys_to_remove = [
                key for key, msg in self._queue.items()
                if msg.created_at < cutoff
            ]
            for key in keys_to_remove:
                msg = self._queue.pop(key)
                removed += 1
                logger.warning(
                    f"[QUEUE] Dropped old message for {msg.contact} "
                    f"(age > {self.MAX_AGE_HOURS}h)"
                )

        if removed > 0:
            logger.info(f"[QUEUE] Cleaned up {removed} old messages")

    async def _process_queue(self) -> None:
        """Process messages in the queue, attempting to send them."""
        if not self._send_callback:
            logger.warning("[QUEUE] No send callback set, cannot process queue")
            return

        # Get messages to process
        async with self._lock:
            messages = list(self._queue.values())

        if not messages:
            return

        logger.info(f"[QUEUE] Processing {len(messages)} queued messages...")

        successful = 0
        failed = 0
        dropped = 0
        skipped = 0

        for msg in messages:
            # Skip if too many retries
            if msg.retry_count >= self.MAX_RETRIES:
                async with self._lock:
                    if msg.key in self._queue:
                        del self._queue[msg.key]
                        dropped += 1
                        logger.warning(
                            f"[QUEUE] Dropped message for {msg.contact} "
                            f"(max retries exceeded: {msg.retry_count})"
                        )
                continue

            try:
                # Attempt to send (with resolved user info if available)
                # Returns: True=success, False=failed, None=no agent available
                result = await self._send_callback(
                    msg.contact,
                    msg.text,
                    msg.channel_id,
                    msg.resolved_user_id,
                    msg.resolved_access_hash
                )

                if result is True:
                    # Remove from queue
                    async with self._lock:
                        if msg.key in self._queue:
                            del self._queue[msg.key]
                    successful += 1
                    logger.info(
                        f"[QUEUE] Successfully sent queued message to {msg.contact}"
                    )
                elif result is None:
                    # No agent available - skip this cycle, don't count as retry
                    skipped += 1
                    logger.debug(
                        f"[QUEUE] Skipped {msg.contact} - no agents available"
                    )
                else:
                    # result is False - actual send failure, increment retry count
                    async with self._lock:
                        if msg.key in self._queue:
                            self._queue[msg.key].retry_count += 1
                    failed += 1
                    logger.warning(
                        f"[QUEUE] Failed to send to {msg.contact} "
                        f"(retry {msg.retry_count + 1}/{self.MAX_RETRIES})"
                    )

            except Exception as e:
                # Update error and retry count
                async with self._lock:
                    if msg.key in self._queue:
                        self._queue[msg.key].retry_count += 1
                        self._queue[msg.key].last_error = str(e)
                failed += 1
                logger.error(
                    f"[QUEUE] Error sending to {msg.contact}: {e}"
                )

            # Small delay between messages to avoid rate limits
            await asyncio.sleep(2)

        if successful > 0 or failed > 0 or dropped > 0 or skipped > 0:
            logger.info(
                f"[QUEUE] Processed: {successful} sent, {failed} failed, "
                f"{skipped} skipped (no agents), {dropped} dropped"
            )

    async def _retry_loop(self) -> None:
        """Background task that periodically processes the queue."""
        logger.info(
            f"[QUEUE] Starting retry loop "
            f"(interval={self.RETRY_INTERVAL_SECONDS}s)"
        )

        while True:
            try:
                await asyncio.sleep(self.RETRY_INTERVAL_SECONDS)

                # Cleanup old messages first
                await self._cleanup_old_messages()

                # Process queue
                queue_size = await self.get_pending_count()
                if queue_size > 0:
                    await self._process_queue()

            except asyncio.CancelledError:
                logger.info("[QUEUE] Retry loop cancelled")
                break
            except Exception as e:
                logger.error(f"[QUEUE] Error in retry loop: {e}")
                await asyncio.sleep(10)  # Wait before retrying

    def start_retry_task(self) -> None:
        """Start the background retry task."""
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._retry_loop())
            logger.info("[QUEUE] Retry task started")

    def stop_retry_task(self) -> None:
        """Stop the background retry task."""
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            logger.info("[QUEUE] Retry task stopped")

    def get_status(self) -> Dict:
        """Get queue status for monitoring."""
        # Note: This is sync, so we access _queue directly (for status display)
        return {
            "pending_count": len(self._queue),
            "retry_task_running": (
                self._retry_task is not None and
                not self._retry_task.done()
            ),
            "messages": [
                {
                    "contact": msg.contact,
                    "channel_id": msg.channel_id,
                    "retry_count": msg.retry_count,
                    "age_minutes": int((time.time() - msg.created_at) / 60),
                    "last_error": msg.last_error,
                }
                for msg in self._queue.values()
            ]
        }


# Global singleton instance
message_queue = MessageQueue()
