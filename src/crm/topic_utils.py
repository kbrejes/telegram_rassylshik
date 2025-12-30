"""
Topic Management Utilities

Functions for creating and managing CRM forum topics.
Extracted from crm_handler.py for better maintainability.
"""

import logging
import random
from pathlib import Path
from typing import Optional, Dict, TYPE_CHECKING

from telethon.tl.types import User
from telethon.tl.functions.messages import CreateForumTopicRequest

from src.agent_account import AgentAccount
from src.agent_pool import AgentPool
from src.conversation_manager import ConversationManager, FrozenAccountError
from src.connection_status import status_manager
from src.database import db
from ai_conversation import AIConversationHandler

if TYPE_CHECKING:
    from src.config_manager import ChannelConfig

logger = logging.getLogger(__name__)


async def create_topic_with_fallback(
    channel_id: str,
    conv_manager: ConversationManager,
    title: str,
    contact_id: int,
    vacancy_id: Optional[int],
    primary_agent: AgentAccount,
    agent_pool: AgentPool
) -> Optional[int]:
    """
    Try to create a topic, falling back to other agents if the primary is frozen.

    Args:
        channel_id: Channel identifier
        conv_manager: Conversation manager for the CRM group
        title: Topic title (contact name)
        contact_id: Contact's user ID
        vacancy_id: Optional vacancy ID for linking
        primary_agent: Primary agent to try first
        agent_pool: Pool of agents for fallback

    Returns:
        Topic ID if created, None otherwise
    """
    # First try with the primary agent (conv_manager's client)
    try:
        topic_id = await conv_manager.create_topic(
            title=title,
            contact_id=contact_id,
            vacancy_id=vacancy_id
        )
        if topic_id:
            return topic_id
    except FrozenAccountError:
        agent_name = Path(primary_agent.session_name).stem
        logger.warning(f"Agent {agent_name} is frozen, trying other agents...")
        status_manager.update_agent_status(
            session_name=agent_name,
            status="frozen",
            phone=primary_agent.phone if hasattr(primary_agent, 'phone') else "",
            error="Account is frozen for forum operations"
        )

    # Try other agents from the pool
    for agent in agent_pool.agents:
        if agent == primary_agent:
            continue  # Skip the primary agent we already tried

        if not agent.client or not agent.client.is_connected():
            continue

        agent_name = Path(agent.session_name).stem
        logger.info(f"Trying to create topic with agent {agent_name}...")

        try:
            group_entity = await agent.client.get_entity(conv_manager.group_id)
            result = await agent.client(CreateForumTopicRequest(
                peer=group_entity,
                title=title[:128],
                random_id=random.randint(1, 2**31)
            ))

            topic_id = result.updates[0].id

            # Cache in conv_manager
            conv_manager._topic_cache[contact_id] = topic_id
            conv_manager._reverse_topic_cache[topic_id] = contact_id

            # Save to DB
            await db.save_topic_contact(
                group_id=conv_manager.group_id,
                topic_id=topic_id,
                contact_id=contact_id,
                contact_name=title,
                vacancy_id=vacancy_id
            )

            logger.info(f"Topic created successfully with agent {agent_name}")
            return topic_id

        except FrozenAccountError:
            logger.warning(f"Agent {agent_name} is also frozen")
            status_manager.update_agent_status(
                session_name=agent_name,
                status="frozen",
                error="Account is frozen for forum operations"
            )
        except Exception as e:
            if "frozen" in str(e).lower():
                logger.warning(f"Agent {agent_name} is frozen: {e}")
                status_manager.update_agent_status(
                    session_name=agent_name,
                    status="frozen",
                    error=str(e)
                )
            else:
                logger.error(f"Error creating topic with agent {agent_name}: {e}")

    logger.error("All agents failed to create topic (frozen or error)")
    return None


async def send_topic_info(
    conv_manager: ConversationManager,
    contact_user: User,
    chat_title: str,
    message,
    chat,
    topic_id: int,
    message_processor
):
    """
    Send informational message to a newly created topic.

    Args:
        conv_manager: Conversation manager
        contact_user: Telegram User object
        chat_title: Title of the source channel
        message: Original vacancy message
        chat: Source chat entity
        topic_id: Forum topic ID
        message_processor: Message processor instance for link generation
    """
    sender_info = f"{contact_user.first_name}"
    if contact_user.username:
        sender_info += f" (@{contact_user.username})"

    info_message = f"📌 **New contact: {sender_info}**\n\n"
    info_message += f"📍 **Vacancy channel:** {chat_title}\n"
    info_message += f"🔗 **Link:** {message_processor.get_message_link(message, chat)}"

    await conv_manager.send_to_topic(topic_id, info_message, link_preview=False)


async def mirror_auto_response(
    agent: AgentAccount,
    conv_manager: ConversationManager,
    crm_group_id: int,
    auto_response_template: str,
    topic_id: int
):
    """
    Mirror auto-response message to CRM topic.

    Args:
        agent: Agent that sent the auto-response
        conv_manager: Conversation manager
        crm_group_id: CRM group ID
        auto_response_template: Auto-response text
        topic_id: Forum topic ID
    """
    try:
        agent_message = f"🤖 **Agent:**\n\n{auto_response_template}"
        sent_msg = await agent.client.send_message(
            entity=crm_group_id,
            message=agent_message,
            reply_to=topic_id
        )
        if sent_msg and hasattr(sent_msg, 'id'):
            conv_manager.save_message_to_topic(sent_msg.id, topic_id)
    except Exception as e:
        logger.error(f"Error mirroring auto-response: {e}")


async def init_ai_context(
    ai_handler: AIConversationHandler,
    contact_id: int,
    auto_response_template: str,
    chat_title: str,
    message_text: str
):
    """
    Initialize AI context for a new contact.

    Args:
        ai_handler: AI conversation handler
        contact_id: Contact's user ID
        auto_response_template: Auto-response that was sent
        chat_title: Title of vacancy channel
        message_text: Vacancy message text (first 500 chars used)
    """
    try:
        job_info = f"Vacancy from channel: {chat_title}\n\n{message_text[:500]}..."
        await ai_handler.initialize_context(
            contact_id=contact_id,
            initial_message=auto_response_template,
            job_info=job_info,
        )
    except Exception as e:
        logger.warning(f"Error initializing AI context: {e}")
