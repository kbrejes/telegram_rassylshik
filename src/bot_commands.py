"""
Command handlers for bot operations.

This module handles commands from the web interface (connect/disconnect agents,
health checks, send messages, etc.)
"""
import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any, Callable, Awaitable, Tuple, Optional

from telethon import errors as telethon_errors

from src.agent_pool import get_or_create_agent, get_existing_agent, disconnect_all_global_agents
from src.agent_account import AgentAccount
from src.connection_status import status_manager
from src.command_queue import command_queue
from src.session_config import get_agent_session_path, SESSIONS_DIR

if TYPE_CHECKING:
    from bot_multi import MultiChannelJobMonitorBot

logger = logging.getLogger(__name__)


class CommandHandler:
    """
    Handles commands from the web interface.

    Commands are queued by the web interface and processed by the bot
    in its event loop (important for Telethon thread safety).
    """

    def __init__(self, bot: "MultiChannelJobMonitorBot"):
        self.bot = bot

    def get_handlers(self) -> Dict[str, Tuple[Callable, str]]:
        """
        Command registry mapping command types to (handler, success_message_template).

        This pattern makes it easy to:
        - Add new commands (just add to dict)
        - Test commands individually
        - See all available commands at a glance
        """
        return {
            "connect_agent": (self.connect_agent, "Agent {target} connected"),
            "disconnect_agent": (self.disconnect_agent, "Agent {target} disconnected"),
            "delete_agent": (self.delete_agent, "Agent {target} deleted"),
            "connect_all": (self.connect_all, "Connected {result} agents"),
            "disconnect_all": (self.disconnect_all, "Disconnected {result} agents"),
            "health_check": (self.health_check, "Health check completed"),
            "send_crm_message": (self.send_crm_message, "Message sent"),
        }

    async def process_commands(self) -> None:
        """Background task to process commands from web interface"""
        logger.info("Command processor started (checking every 2 seconds)")
        handlers = self.get_handlers()

        while True:
            try:
                await asyncio.sleep(2)

                # Cleanup old commands periodically
                command_queue.cleanup_old_commands(max_age_hours=1)

                # Get pending commands
                pending = command_queue.get_pending_commands()
                if not pending:
                    continue

                for cmd in pending:
                    command_queue.mark_processing(cmd.id)
                    logger.info(f"Processing command: {cmd.type} -> {cmd.target}")

                    try:
                        if cmd.type not in handlers:
                            command_queue.mark_completed(cmd.id, False, f"Unknown command: {cmd.type}")
                            continue

                        handler, success_template = handlers[cmd.type]
                        result = await handler(cmd.target)

                        # Format success message with target and result
                        success_msg = success_template.format(target=cmd.target, result=result)
                        command_queue.mark_completed(cmd.id, True, success_msg)

                    except Exception as e:
                        logger.error(f"Error executing command {cmd.id}: {e}")
                        command_queue.mark_completed(cmd.id, False, str(e))

            except Exception as e:
                logger.error(f"Error in command processor: {e}")

    async def connect_agent(self, session_name: str) -> None:
        """Connect a specific agent"""
        session_path = get_agent_session_path(session_name)
        if not Path(f"{session_path}.session").exists():
            raise FileNotFoundError(f"Session file not found: {session_name}")

        # Find phone from config
        phone = None
        for channel in self.bot.output_channels:
            if channel.crm_enabled:
                for agent in channel.agents:
                    if agent.session_name == session_name:
                        phone = agent.phone
                        break
                if phone:
                    break

        if not phone:
            # Try to get from existing agent status
            status = status_manager.get_all_status()
            agent_status = status.get("agents", {}).get(session_name, {})
            phone = agent_status.get("phone", "")

        agent = await get_or_create_agent(session_name, phone or "")
        if agent and agent.client.is_connected():
            user_info = None
            try:
                me = await agent.client.get_me()
                user_info = {
                    "id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone": me.phone
                }
            except Exception:
                pass
            status_manager.update_agent_status(session_name, "connected", phone or "", user_info=user_info)
            logger.info(f"Agent {session_name} connected successfully")
        else:
            error_msg = agent.last_connect_error if agent else "Failed to connect"
            status_manager.update_agent_status(session_name, "error", phone or "", error=error_msg)
            raise Exception(f"Failed to connect agent {session_name}: {error_msg}")

    async def disconnect_agent(self, session_name: str) -> None:
        """Disconnect a specific agent"""
        agent = await get_existing_agent(session_name)
        if agent:
            await agent.disconnect()
            status_manager.update_agent_status(session_name, "disconnected")
            logger.info(f"Agent {session_name} disconnected")
        else:
            status_manager.update_agent_status(session_name, "disconnected")
            logger.info(f"Agent {session_name} was not connected")

    async def delete_agent(self, session_name: str) -> None:
        """Disconnect and delete agent session file"""
        # First disconnect
        await self.disconnect_agent(session_name)

        # Remove from status tracking
        status_manager.remove_agent_status(session_name)

        # Delete session file
        session_file = SESSIONS_DIR / f"{session_name}.session"
        if session_file.exists():
            session_file.unlink()
            logger.info(f"Deleted session file: {session_file}")

    async def connect_all(self, _target: Any = None) -> int:
        """Connect all agents from configuration"""
        count = 0
        for channel in self.bot.output_channels:
            if channel.crm_enabled:
                for agent_config in channel.agents:
                    try:
                        await self.connect_agent(agent_config.session_name)
                        count += 1
                    except Exception as e:
                        logger.error(f"Failed to connect {agent_config.session_name}: {e}")
        return count

    async def disconnect_all(self, _target: Any = None) -> int:
        """Disconnect all agents"""
        count = await disconnect_all_global_agents()
        # Update status for all agents
        status = status_manager.get_all_status()
        for session_name in status.get("agents", {}).keys():
            status_manager.update_agent_status(session_name, "disconnected")
        return count

    async def health_check(self, _target: Any = None) -> None:
        """Check health of all connections"""
        # Bot status
        try:
            if self.bot.client.is_connected():
                me = await self.bot.client.get_me()
                user_info = {
                    "id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone": me.phone
                }
                status_manager.update_bot_status(True, True, user_info)
            else:
                status_manager.update_bot_status(False, False)
        except Exception as e:
            logger.error(f"Bot health check failed: {e}")
            status_manager.update_bot_status(False, False)

        # Agent statuses are updated by agent_pool callbacks
        logger.info("Health check completed")

    async def send_crm_message(self, target: dict) -> None:
        """Send a message to a CRM contact from web interface.

        Args:
            target: Dict with keys:
                - contact_id: Telegram user ID to send to (if CRM topic exists)
                - contact_username: @username (fallback when no CRM topic)
                - message: Message text
                - agent_session: (optional) Specific agent session to use
        """
        contact_id_str = target.get("contact_id")
        contact_id = int(contact_id_str) if contact_id_str else None
        contact_username = target.get("contact_username")
        message = target.get("message", "")
        requested_agent_session = target.get("agent_session")  # Optional manual selection

        if not message:
            raise ValueError("message is required")

        if not contact_id and not contact_username:
            raise ValueError("contact_id or contact_username is required")

        # Find which channel has this contact (or use first available for username-only)
        channel_id = None
        if contact_id:
            channel_id = self.bot.crm.contact_to_channel.get(contact_id)
            if not channel_id:
                # Try to find in conversation managers
                for ch_id, conv_manager in self.bot.crm.conversation_managers.items():
                    if contact_id in conv_manager._topic_cache:
                        channel_id = ch_id
                        self.bot.crm.contact_to_channel[contact_id] = channel_id
                        break

        # If still no channel, use first available agent pool (for username-only sends)
        if not channel_id:
            if self.bot.crm.agent_pools:
                channel_id = next(iter(self.bot.crm.agent_pools.keys()))
            else:
                raise ValueError("No agent pools available")

        conv_manager = self.bot.crm.conversation_managers.get(channel_id)
        # conv_manager may be None for username-only sends (no CRM topic yet)

        topic_id = None
        if conv_manager and contact_id:
            topic_id = conv_manager.get_topic_id(contact_id)

        # Get agent pool
        agent_pool = self.bot.crm.agent_pools.get(channel_id)
        if not agent_pool:
            raise ValueError(f"No agent pool for channel {channel_id}")

        # Select agent with priority:
        # 1. Manually requested agent (if specified and available)
        # 2. Previously assigned agent for this topic (if available)
        # 3. Any available agent from pool
        agent: Optional[AgentAccount] = None
        agent_source = ""

        if requested_agent_session:
            # User manually selected an agent
            agent = self._find_agent_by_session(agent_pool, requested_agent_session)
            if agent:
                if agent.is_available():
                    agent_source = "manual"
                else:
                    remaining = agent.flood_wait_remaining
                    raise ValueError(
                        f"Agent {requested_agent_session} is in flood wait "
                        f"({remaining}s remaining). Please select another agent."
                    )
            else:
                raise ValueError(f"Agent {requested_agent_session} not found in pool")

        if not agent and topic_id:
            # Try previously assigned agent for this topic
            assigned_agent = self.bot.crm.topic_to_agent.get(topic_id)
            if assigned_agent and assigned_agent.is_available():
                agent = assigned_agent
                agent_source = "assigned"

        if not agent:
            # Fall back to any available agent
            agent = agent_pool.get_available_agent()
            if agent:
                agent_source = "pool"

        if not agent or not agent.client:
            # Provide helpful error message about blocked agents
            blocked_agents = self._get_blocked_agents_info(agent_pool)
            if blocked_agents:
                raise ValueError(
                    f"No available agent. Blocked agents: {blocked_agents}"
                )
            raise ValueError("No available agent to send message")

        logger.info(f"Using agent {agent.session_name} ({agent_source}) to send message")

        # Determine send target: contact_id or username
        send_target = contact_id if contact_id else contact_username

        # Normalize username (add @ prefix if missing)
        if isinstance(send_target, str) and not send_target.startswith('@'):
            send_target = f"@{send_target}"

        # Record in AI context (only if we have contact_id)
        if contact_id:
            ai_handler = self.bot.crm.ai_handlers.get(channel_id)
            if ai_handler:
                ai_handler.add_operator_message(contact_id, message)

        # Send message to contact with proper error handling
        try:
            sent_message = await agent.client.send_message(send_target, message)
            if sent_message and conv_manager and contact_id:
                conv_manager.mark_agent_sent_message(sent_message.id)

        except telethon_errors.FloodWaitError as e:
            agent.handle_flood_wait(e.seconds)
            raise ValueError(
                f"Agent {agent.session_name} hit flood wait ({e.seconds}s). "
                "Please try with another agent."
            )

        except telethon_errors.PeerFloodError:
            agent.handle_flood_wait(3600)  # 1 hour block
            raise ValueError(
                f"Agent {agent.session_name} is spam-limited. "
                "Please try with another agent."
            )

        except telethon_errors.UserIsBlockedError:
            raise ValueError("User has blocked this agent. Cannot send message.")

        except telethon_errors.InputUserDeactivatedError:
            raise ValueError("User account is deactivated.")

        except Exception as e:
            error_msg = str(e).lower()
            if "flood" in error_msg or "spam" in error_msg:
                agent.handle_flood_wait(1800)  # 30 min default
                raise ValueError(f"Agent rate limited: {e}")
            raise

        # Mirror to CRM topic (only if we have a topic)
        if conv_manager and topic_id:
            try:
                operator_msg = f"👤 **Operator:**\n\n{message}"
                topic_sent = await agent.client.send_message(
                    entity=conv_manager.group_id,
                    message=operator_msg,
                    reply_to=topic_id
                )
                if topic_sent:
                    conv_manager.save_message_to_topic(topic_sent.id, topic_id)
            except Exception as e:
                logger.warning(f"Failed to mirror operator message to CRM topic: {e}")

        logger.info(f"Sent message to {send_target} via {agent.session_name}")

    def _find_agent_by_session(self, agent_pool, session_name: str) -> Optional[AgentAccount]:
        """Find an agent in the pool by session name."""
        for agent in agent_pool.agents:
            if agent.session_name == session_name:
                return agent
        return None

    def _get_blocked_agents_info(self, agent_pool) -> str:
        """Get info about blocked agents for error message."""
        blocked = []
        for agent in agent_pool.agents:
            if not agent.is_available():
                remaining = agent.flood_wait_remaining
                blocked.append(f"{agent.session_name} ({remaining}s)")
        return ", ".join(blocked) if blocked else ""
