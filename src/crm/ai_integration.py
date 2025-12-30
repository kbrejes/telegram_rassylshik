"""
AI Integration Module

Handles AI response processing and delivery to contacts.
Extracted from crm_handler.py for better maintainability.
"""

import asyncio
import logging
from typing import Optional

from telethon import TelegramClient

from src.conversation_manager import ConversationManager
from src.human_behavior import human_behavior
from ai_conversation import AIConversationHandler

logger = logging.getLogger(__name__)


async def handle_ai_response(
    agent_client: TelegramClient,
    contact_id: int,
    contact_name: str,
    message_text: str,
    ai_handler: AIConversationHandler,
    instant_response: bool = False,
    conv_manager: Optional[ConversationManager] = None,
    topic_id: Optional[int] = None
) -> None:
    """
    Handle AI response to contact message.

    This function:
    - Always processes AI response (core functionality)
    - CRM mirroring is optional and best-effort
    - Never fails due to CRM issues

    Args:
        agent_client: Telegram client to send messages
        contact_id: Contact's user ID
        contact_name: Contact's display name
        message_text: Message to respond to
        ai_handler: AI conversation handler
        instant_response: If True, skip typing simulation
        conv_manager: Optional conversation manager (for CRM mirroring)
        topic_id: Optional topic ID for CRM mirroring
    """
    # Get topic_id if not provided but conv_manager is available
    if not topic_id and conv_manager:
        topic_id = conv_manager.get_topic_id(contact_id)

    async def send_to_contact(cid: int, text: str) -> bool:
        """Send message to contact with optional CRM mirroring."""
        try:
            # Show typing indicator before sending (skip if instant_response)
            if not instant_response:
                await human_behavior.simulate_typing(
                    client=agent_client,
                    contact=cid,
                    message_length=len(text)
                )
            sent = await agent_client.send_message(cid, text)
            if sent:
                # Mark as agent-sent (if conv_manager available)
                if conv_manager:
                    conv_manager.mark_agent_sent_message(sent.id)

                # Best-effort: mirror AI response to CRM topic
                if conv_manager and topic_id:
                    try:
                        ai_msg = f"🤖 **AI:**\n\n{text}"
                        topic_sent = await agent_client.send_message(
                            entity=conv_manager.group_id,
                            message=ai_msg,
                            reply_to=topic_id
                        )
                        if topic_sent:
                            conv_manager.save_message_to_topic(topic_sent.id, topic_id)
                    except Exception as e:
                        logger.warning(f"[AI] CRM mirror failed (non-blocking): {e}")
            return True
        except Exception as e:
            logger.error(f"[AI] Error sending response: {e}")
            return False

    async def suggest_in_topic(cid: int, text: str, name: str) -> None:
        """Suggest response in CRM topic (best-effort)."""
        if not conv_manager or not topic_id:
            logger.debug("[AI] No conv_manager or topic_id, skipping suggestion")
            return

        try:
            suggest_msg = f"💡 **AI suggests response:**\n\n{text}\n\n_Send this text or write your own response_"
            await agent_client.send_message(
                entity=conv_manager.group_id,
                message=suggest_msg,
                reply_to=topic_id
            )
        except Exception as e:
            logger.warning(f"[AI] Failed to suggest in topic: {e}")

    # Process AI response asynchronously
    asyncio.create_task(
        ai_handler.handle_message(
            contact_id=contact_id,
            message=message_text,
            contact_name=contact_name,
            send_callback=send_to_contact,
            suggest_callback=suggest_in_topic,
        )
    )
    logger.info(f"[AI] Started async AI response for {contact_name}")
