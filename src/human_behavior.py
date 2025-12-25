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
    - 70% instant responses (5-15 seconds)
    - 30% slower responses (30s - 3min)
    - Occasional longer delays (12-20min, twice per day)
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

        # Track last long delay per contact
        self._last_long_delay: Dict[int, float] = {}

        # Instant response (70% chance)
        self.INSTANT_RESPONSE_CHANCE = 0.70
        self.INSTANT_DELAY_MIN = 5  # 5 seconds
        self.INSTANT_DELAY_MAX = 15  # 15 seconds

        # Normal response (30% chance)
        self.NORMAL_DELAY_MIN = 30  # 30 seconds
        self.NORMAL_DELAY_MAX = 180  # 3 minutes

        # Long delay (twice per day per contact)
        self.LONG_DELAY_MIN = 720  # 12 minutes
        self.LONG_DELAY_MAX = 1200  # 20 minutes
        self.LONG_DELAY_INTERVAL = 43200  # 12 hours between long delays (twice a day)

        # Typing speed (chars per second) - slower for more realistic feel
        self.TYPING_SPEED_MIN = 2  # slow typer (~25 WPM)
        self.TYPING_SPEED_MAX = 4  # normal typer (~50 WPM)

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

        Long delays happen twice per day per contact (every 12 hours).
        """
        now = time.time()
        last_long = self._last_long_delay.get(contact_id, 0)

        # Check if enough time has passed since last long delay (12 hours)
        return now - last_long >= self.LONG_DELAY_INTERVAL

    def _calculate_delay(self, message_length: int, contact_id: int) -> float:
        """
        Calculate delay before responding.

        - 70% chance: instant response (5-15 seconds)
        - 30% chance: normal response (30s - 3min)
        - If eligible: long delay (12-20 min, twice per day)
        """
        # First check for long delay (twice per day)
        if self._should_long_delay(contact_id):
            delay = random.uniform(self.LONG_DELAY_MIN, self.LONG_DELAY_MAX)
            self._last_long_delay[contact_id] = time.time()
            logger.info(f"[HUMAN] Long delay triggered for {contact_id}: {delay/60:.1f} min")
            return delay

        # 70% instant, 30% normal
        if random.random() < self.INSTANT_RESPONSE_CHANCE:
            # Instant response: 5-15 seconds
            delay = random.uniform(self.INSTANT_DELAY_MIN, self.INSTANT_DELAY_MAX)
            logger.debug(f"[HUMAN] Instant response for {contact_id}: {delay:.0f}s")
        else:
            # Normal response: 30s - 3min
            delay = random.uniform(self.NORMAL_DELAY_MIN, self.NORMAL_DELAY_MAX)
            # Add small complexity factor for longer messages
            complexity_delay = (message_length / 100) * random.uniform(1, 5)
            delay += complexity_delay
            delay = min(delay, self.NORMAL_DELAY_MAX)
            logger.debug(f"[HUMAN] Normal delay for {contact_id}: {delay:.0f}s")

        return delay

    def _calculate_typing_duration(self, message_length: int) -> float:
        """
        Calculate how long to show typing indicator.

        Based on realistic typing speed.
        """
        # Random typing speed for this message
        chars_per_second = random.uniform(self.TYPING_SPEED_MIN, self.TYPING_SPEED_MAX)

        # Base typing time
        typing_time = message_length / chars_per_second

        # Add some "thinking" pauses (20-50% extra for pauses/corrections)
        thinking_factor = random.uniform(1.2, 1.5)
        typing_time *= thinking_factor

        # Minimum 3s, maximum 45s
        return max(3.0, min(typing_time, 45.0))

    async def simulate_typing(
        self,
        client: Any,
        contact: Union[str, int],
        message_length: int
    ) -> None:
        """
        Show typing indicator for realistic duration.

        Keeps refreshing typing action every 4s since Telegram expires it after ~5s.
        Message should be sent immediately after this returns.

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

            # Keep sending typing action every 4 seconds (Telegram expires after ~5s)
            elapsed = 0
            typing_interval = 4.0

            while elapsed < duration:
                # Send typing action
                await client(SetTypingRequest(
                    peer=contact,
                    action=SendMessageTypingAction()
                ))

                # Wait for next interval or remaining time
                wait_time = min(typing_interval, duration - elapsed)
                await asyncio.sleep(wait_time)
                elapsed += wait_time

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
