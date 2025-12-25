"""
Human-like behavior simulation for Telegram agents.

Adds realistic delays and typing indicators to make bot responses
appear more human-like.

Usage:
    from src.human_behavior import human_behavior

    # Before sending message:
    await human_behavior.simulate_before_message(
        client=agent.client,
        contact=user,
        message=text,
        contact_id=user_id
    )

    # Disable for testing:
    human_behavior.enabled = False
"""

import asyncio
import random
import time
import logging
from typing import Optional, Dict, Any, Union

logger = logging.getLogger(__name__)


class HumanBehavior:
    """
    Simulates human-like behavior for message sending.

    Features:
    - Random delays between messages (30s - 3min)
    - Occasional longer delays (12-20min, once per hour)
    - Typing indicator before messages
    - Easy toggle for testing
    """

    def __init__(self, enabled: bool = True):
        """
        Initialize human behavior simulator.

        Args:
            enabled: Whether to simulate human behavior (set False for testing)
        """
        self.enabled = enabled

        # Track last long delay per contact to ensure once-per-hour
        self._last_long_delay: Dict[int, float] = {}

        # Configuration
        self.MIN_DELAY_SECONDS = 30
        self.MAX_DELAY_SECONDS = 180  # 3 minutes

        self.LONG_DELAY_MIN = 720  # 12 minutes
        self.LONG_DELAY_MAX = 1200  # 20 minutes
        self.LONG_DELAY_INTERVAL = 3600  # 1 hour between long delays
        self.LONG_DELAY_CHANCE = 0.15  # 15% chance when eligible

        # Typing speed (chars per second) - humans type 40-80 WPM
        self.TYPING_SPEED_MIN = 4  # slow typer
        self.TYPING_SPEED_MAX = 8  # fast typer

    def disable(self):
        """Disable human behavior simulation (for testing)."""
        self.enabled = False
        logger.info("[HUMAN] Behavior simulation DISABLED")

    def enable(self):
        """Enable human behavior simulation."""
        self.enabled = True
        logger.info("[HUMAN] Behavior simulation ENABLED")

    def _should_long_delay(self, contact_id: int) -> bool:
        """
        Check if we should do a long delay for this contact.

        Long delays happen:
        - At most once per hour per contact
        - With 15% probability when eligible
        """
        now = time.time()
        last_long = self._last_long_delay.get(contact_id, 0)

        # Check if enough time has passed since last long delay
        if now - last_long < self.LONG_DELAY_INTERVAL:
            return False

        # Random chance
        return random.random() < self.LONG_DELAY_CHANCE

    def _calculate_delay(self, message_length: int, contact_id: int) -> float:
        """
        Calculate delay before responding.

        Longer messages = slightly longer "thinking" time.
        """
        # Base delay: 30s - 3min
        base_delay = random.uniform(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)

        # Add time based on message complexity (longer = more thinking)
        # ~1-3 seconds per 50 chars
        complexity_delay = (message_length / 50) * random.uniform(1, 3)

        delay = base_delay + complexity_delay

        # Check for long delay (once per hour)
        if self._should_long_delay(contact_id):
            delay = random.uniform(self.LONG_DELAY_MIN, self.LONG_DELAY_MAX)
            self._last_long_delay[contact_id] = time.time()
            logger.info(f"[HUMAN] Long delay triggered for {contact_id}: {delay/60:.1f} min")

        # Cap at max
        return min(delay, self.LONG_DELAY_MAX)

    def _calculate_typing_duration(self, message_length: int) -> float:
        """
        Calculate how long to show typing indicator.

        Based on realistic typing speed.
        """
        # Random typing speed for this message
        chars_per_second = random.uniform(self.TYPING_SPEED_MIN, self.TYPING_SPEED_MAX)

        # Base typing time
        typing_time = message_length / chars_per_second

        # Add some "thinking" pauses (10-30% extra)
        thinking_factor = random.uniform(1.1, 1.3)
        typing_time *= thinking_factor

        # Minimum 2s, maximum 30s (Telegram limit)
        return max(2.0, min(typing_time, 30.0))

    async def simulate_typing(
        self,
        client: Any,
        contact: Union[str, int],
        message_length: int
    ) -> None:
        """
        Show typing indicator for realistic duration.

        Args:
            client: Telethon client
            contact: User to show typing to
            message_length: Length of message being "typed"
        """
        if not self.enabled:
            return

        try:
            from telethon.tl.functions.messages import SetTypingRequest
            from telethon.tl.types import SendMessageTypingAction

            duration = self._calculate_typing_duration(message_length)
            logger.debug(f"[HUMAN] Typing for {duration:.1f}s ({message_length} chars)")

            # Send typing action
            await client(SetTypingRequest(
                peer=contact,
                action=SendMessageTypingAction()
            ))

            # Wait for typing duration
            await asyncio.sleep(duration)

        except Exception as e:
            # Don't fail the message if typing fails
            logger.debug(f"[HUMAN] Typing indicator failed: {e}")

    async def simulate_delay(
        self,
        message_length: int,
        contact_id: int
    ) -> float:
        """
        Wait for human-like delay before responding.

        Args:
            message_length: Length of incoming message (affects "thinking" time)
            contact_id: Contact ID for tracking long delays

        Returns:
            Actual delay used in seconds
        """
        if not self.enabled:
            return 0.0

        delay = self._calculate_delay(message_length, contact_id)

        logger.debug(f"[HUMAN] Delay for {contact_id}: {delay:.0f}s")
        await asyncio.sleep(delay)

        return delay

    async def simulate_before_message(
        self,
        client: Any,
        contact: Union[str, int],
        message: str,
        contact_id: int,
        incoming_message_length: int = 0
    ) -> Dict[str, float]:
        """
        Full human-like simulation before sending a message.

        1. Wait for thinking delay
        2. Show typing indicator

        Args:
            client: Telethon client
            contact: Recipient (username or user_id)
            message: Message to be sent
            contact_id: Contact ID for tracking
            incoming_message_length: Length of message we're responding to

        Returns:
            Dict with 'delay' and 'typing' durations used
        """
        if not self.enabled:
            return {'delay': 0, 'typing': 0}

        # 1. Think about the response
        delay = await self.simulate_delay(incoming_message_length, contact_id)

        # 2. Type the response
        await self.simulate_typing(client, contact, len(message))
        typing_duration = self._calculate_typing_duration(len(message))

        return {'delay': delay, 'typing': typing_duration}


# Global singleton instance
human_behavior = HumanBehavior(enabled=True)
