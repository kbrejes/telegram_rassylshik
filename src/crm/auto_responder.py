"""
Auto-Response Module

Handles sending auto-responses to contacts via agent pool.
Extracted from crm_handler.py for better maintainability.
"""

import logging
from typing import Any, Dict, Optional, Set

from src.agent_pool import AgentPool
from src.config_manager import ChannelConfig
from src.database import db
from src.message_queue import message_queue

logger = logging.getLogger(__name__)


async def send_auto_response(
    channel: ChannelConfig,
    agent_pool: AgentPool,
    contacts: Dict[str, Optional[str]],
    contacted_users: Set[str],
    resolved_user_id: Optional[int] = None,
    resolved_access_hash: Optional[int] = None,
    vacancy_id: Optional[int] = None
) -> bool:
    """
    Send auto-response to a contact with fallback through agent pool.

    Args:
        channel: Channel configuration with auto_response settings
        agent_pool: Pool of agents for sending messages
        contacts: Dict with 'telegram' key containing username
        contacted_users: Set of already contacted usernames (modified in-place)
        resolved_user_id: Optional resolved Telegram user ID
        resolved_access_hash: Optional resolved access hash
        vacancy_id: Optional vacancy ID for logging

    Returns:
        True if message was sent successfully
    """
    telegram_contact = contacts.get('telegram')

    # Helper to log attempts
    async def log_attempt(
        status: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        agent_session: Optional[str] = None
    ) -> None:
        if vacancy_id:
            try:
                await db.save_auto_response_attempt(
                    vacancy_id=vacancy_id,
                    contact_username=telegram_contact,
                    contact_user_id=resolved_user_id,
                    agent_session=agent_session,
                    status=status,
                    error_type=error_type,
                    error_message=error_message
                )
            except Exception as e:
                logger.warning(f"[AUTO-RESPONSE] Failed to log attempt: {e}")

    # Validate auto-response is enabled
    if not channel.auto_response_enabled or not channel.auto_response_template:
        logger.debug(f"[AUTO-RESPONSE] Skipped: auto_response not enabled for channel '{channel.name}'")
        return False

    if not telegram_contact:
        logger.info("[AUTO-RESPONSE] Skipped: no Telegram contact extracted")
        await log_attempt('skipped', 'no_contact', 'No Telegram contact extracted from vacancy')
        return False

    if telegram_contact.lower() in contacted_users:
        logger.debug(f"[AUTO-RESPONSE] Skipped: {telegram_contact} already contacted")
        await log_attempt('skipped', 'already_contacted', f'{telegram_contact} already contacted in this batch')
        return False

    # Always use username for agents - they need to resolve with their own client
    # (access_hash from bot is session-specific and won't work for agents)
    target: Any = telegram_contact
    logger.info(f"[AUTO-RESPONSE] Using username {telegram_contact} (resolved_id={resolved_user_id})")

    try:
        # Use pool's send_message which has built-in agent rotation/fallback
        success = await agent_pool.send_message(
            target,
            channel.auto_response_template,
            max_retries=len(agent_pool.agents) if agent_pool.agents else 3
        )
        if success:
            contacted_users.add(telegram_contact.lower())
            logger.info(f"[AUTO-RESPONSE] ✅ Successfully sent to {telegram_contact}")
            await log_attempt('success')
            return True
        else:
            # All agents failed - queue for retry with resolved info
            logger.warning(f"[AUTO-RESPONSE] ❌ Failed to send to {telegram_contact} (all agents failed)")
            await log_attempt('failed', 'all_agents_failed', 'All agents failed (likely spam limit or invalid peer)')

            await message_queue.add(
                contact=telegram_contact,
                text=channel.auto_response_template,
                channel_id=channel.id,
                error="All agents failed (likely spam limit)",
                resolved_user_id=resolved_user_id,
                resolved_access_hash=resolved_access_hash
            )
            logger.info(f"[AUTO-RESPONSE] 📥 Queued message for {telegram_contact} for later retry")
            await log_attempt('queued', 'retry_scheduled', 'Added to message queue for later retry')

    except Exception as e:
        error_str = str(e)
        logger.error(f"[AUTO-RESPONSE] ❌ Error sending to {telegram_contact}: {e}")

        # Determine error type
        error_type = _classify_error(error_str)
        await log_attempt('failed', error_type, error_str[:500])

        # Queue if it's a rate limit error
        if error_type in ('flood_wait', 'spam_limit'):
            await message_queue.add(
                contact=telegram_contact,
                text=channel.auto_response_template,
                channel_id=channel.id,
                error=error_str,
                resolved_user_id=resolved_user_id,
                resolved_access_hash=resolved_access_hash
            )
            logger.info(f"[AUTO-RESPONSE] 📥 Queued message for {telegram_contact} for later retry")
            await log_attempt('queued', 'retry_scheduled', 'Added to message queue for later retry')

    return False


def _classify_error(error_str: str) -> str:
    """Classify error string into error type category."""
    error_lower = error_str.lower()
    if "invalid" in error_lower and "peer" in error_lower:
        return 'invalid_peer'
    elif "flood" in error_lower:
        return 'flood_wait'
    elif "spam" in error_lower:
        return 'spam_limit'
    return 'other'
