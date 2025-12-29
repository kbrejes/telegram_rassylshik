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

logger = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """A message waiting to be sent."""
    contact: str  # @username
    text: str
    channel_id: str
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_error: Optional[str] = None

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

    MAX_RETRIES = 5
    MAX_AGE_HOURS = 24  # Messages older than this are dropped
    RETRY_INTERVAL_SECONDS = 60  # Check queue every minute

    def __init__(self):
        self._queue: Dict[str, QueuedMessage] = {}  # key -> message
        self._lock = asyncio.Lock()
        self._retry_task: Optional[asyncio.Task] = None
        self._send_callback: Optional[Callable[[str, str, str], Awaitable[bool]]] = None

    def set_send_callback(
        self,
        callback: Callable[[str, str, str], Awaitable[bool]]
    ):
        """
        Set the callback for sending messages.

        Args:
            callback: async function(contact, text, channel_id) -> bool
        """
        self._send_callback = callback

    async def add(
        self,
        contact: str,
        text: str,
        channel_id: str,
        error: Optional[str] = None
    ) -> bool:
        """
        Add a message to the queue.

        Args:
            contact: @username to send to
            text: Message text
            channel_id: Channel ID for routing
            error: Error message from failed attempt

        Returns:
            True if message was added (not duplicate)
        """
        msg = QueuedMessage(
            contact=contact,
            text=text,
            channel_id=channel_id,
            last_error=error
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

    async def _cleanup_old_messages(self):
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

    async def _process_queue(self):
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

        for msg in messages:
            # Skip if too many retries
            if msg.retry_count >= self.MAX_RETRIES:
                async with self._lock:
                    if msg.key in self._queue:
                        del self._queue[msg.key]
                        dropped += 1
                        logger.warning(
                            f"[QUEUE] Dropped message for {msg.contact} "
                            f"(max retries exceeded)"
                        )
                continue

            try:
                # Attempt to send
                success = await self._send_callback(
                    msg.contact,
                    msg.text,
                    msg.channel_id
                )

                if success:
                    # Remove from queue
                    async with self._lock:
                        if msg.key in self._queue:
                            del self._queue[msg.key]
                    successful += 1
                    logger.info(
                        f"[QUEUE] Successfully sent queued message to {msg.contact}"
                    )
                else:
                    # Increment retry count
                    async with self._lock:
                        if msg.key in self._queue:
                            self._queue[msg.key].retry_count += 1
                    failed += 1

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

        if successful > 0 or failed > 0 or dropped > 0:
            logger.info(
                f"[QUEUE] Processed: {successful} sent, {failed} failed, "
                f"{dropped} dropped"
            )

    async def _retry_loop(self):
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

    def start_retry_task(self):
        """Start the background retry task."""
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._retry_loop())
            logger.info("[QUEUE] Retry task started")

    def stop_retry_task(self):
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
