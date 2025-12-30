"""
Telegram userbot for job vacancy monitoring with multiple channel support
+ CRM functionality (auto-responses and message relay to topics)
"""
import asyncio
import logging
import os
from pathlib import Path
from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
from typing import List, Set, Dict, Optional
from src.config import config
from src.database import db
from src.message_processor import message_processor
from src.config_manager import ConfigManager, ChannelConfig
from src.agent_pool import disconnect_all_global_agents, get_or_create_agent, get_existing_agent
from src.crm_handler import CRMHandler
from src.session_config import get_bot_session_path, get_agent_session_path, SESSIONS_DIR
from src.connection_status import status_manager
from src.command_queue import command_queue
from src.job_analyzer import JobAnalyzer, JobAnalysisResult

logger = logging.getLogger(__name__)


class NeedsAuthenticationError(Exception):
    """Exception: authorization required via web interface"""
    pass


class ChannelNameLogFilter(logging.Filter):
    """Filter for replacing channel IDs with names in logs"""

    def __init__(self, channel_map: Dict[int, str]):
        super().__init__()
        self.channel_map = channel_map
        self.unknown_channels = set()

    def filter(self, record):
        """Replaces channel IDs with names in log messages"""
        try:
            if record.args:
                try:
                    formatted_message = record.msg % record.args
                except Exception:
                    return True
            else:
                formatted_message = str(record.msg)
            
            import re
            pattern = r'channel (\d+)'
            
            def replace_channel_id(match):
                channel_id = int(match.group(1))
                
                if channel_id in self.channel_map:
                    return f'"{self.channel_map[channel_id]}" (ID: {channel_id})'
                
                if channel_id not in self.unknown_channels:
                    self.unknown_channels.add(channel_id)
                
                return f'[Unknown Channel] (ID: {channel_id})'
            
            formatted_message = re.sub(pattern, replace_channel_id, formatted_message)
            record.msg = formatted_message
            record.args = ()
            
        except Exception:
            pass
        
        return True


class MultiChannelJobMonitorBot:
    """Bot for monitoring job vacancies with multiple output channel support."""

    def __init__(self):
        # Use absolute path from session_config
        self.client = TelegramClient(
            get_bot_session_path(),
            config.API_ID,
            config.API_HASH
        )

        self.monitored_sources: Set[int] = set()  # Source IDs to monitor
        self.channel_names: Dict[int, str] = {}  # ID -> channel name

        # Config manager for output channels
        self.config_manager = ConfigManager()
        self.output_channels: List[ChannelConfig] = []

        # CRM functionality (extracted to separate module)
        self.crm = CRMHandler(self)

        # Job analyzer (LLM-based filtering)
        self.job_analyzer: Optional[JobAnalyzer] = None

        # For tracking config file changes
        self.config_file_path = Path("configs/channels_config.json")
        self.last_config_mtime = None
        
        self.is_running = False

    async def check_session_valid(self) -> bool:
        """Check if valid session exists"""
        session_path = Path(f"{get_bot_session_path()}.session")
        if not session_path.exists():
            return False

        try:
            if not self.client.is_connected():
                await self.client.connect()
            return await self.client.is_user_authorized()
        except Exception as e:
            logger.debug(f"Error checking session: {e}")
            return False

    async def start(self, wait_for_auth: bool = True):
        """
        Start the bot with session verification.

        Args:
            wait_for_auth: If True and no session - wait for web auth.
                          If False - try automatic authorization.
        """
        logger.info("Starting Multi-Channel Telegram userbot...")

        # Set main thread for agents
        # Agents must connect only from this thread
        from src.agent_pool import set_main_thread
        set_main_thread()

        if not self.client.is_connected():
            await self.client.connect()

        # If already authorized - no need to send code
        if await self.client.is_user_authorized():
            logger.info("Found existing session, using it")
        else:
            # No session - need authorization
            if wait_for_auth:
                # DO NOT try automatic authorization
                # Wait for user to authorize via web interface
                logger.info("Session not found. Waiting for web interface authorization...")
                raise NeedsAuthenticationError("Authorization required via web interface")
            else:
                # Legacy behavior - automatic auth (may cause FloodWait)
                logger.info("Session not found, attempting authorization...")
                await self.client.start(phone=config.PHONE)

        # Verify authorization
        me = await self.client.get_me()
        logger.info(f"Bot authorized as: {me.first_name} ({me.phone})")

        # Connect to database
        await db.connect()

        # Load output channel configuration
        await self.load_output_channels()

        # Load all unique input sources
        await self.load_input_sources()

        # Initialize LLM job analyzer
        await self._init_job_analyzer()

        # Initialize CRM agents and conversation managers
        await self.crm.setup_agents(self.output_channels, self.config_manager)

        # Ensure all agents are in their CRM groups
        await self._ensure_agents_in_crm_groups()

        # Refresh CRM entity cache now that agents are in groups
        await self.crm.refresh_crm_entities()

        # Sync missed messages from while bot was offline
        try:
            await self.crm.sync_missed_messages(lookback_hours=2)
        except Exception as e:
            logger.warning(f"Error syncing missed messages: {e}")

        # Setup log filter
        self._setup_log_filter()

        # Register event handlers
        self.register_handlers()

        # Save config modification time at startup
        if self.config_file_path.exists():
            self.last_config_mtime = os.path.getmtime(self.config_file_path)
    
    async def load_output_channels(self) -> None:
        """Load output channel configuration from ConfigManager."""
        try:
            self.output_channels = self.config_manager.load()
            
            enabled_channels = [ch for ch in self.output_channels if ch.enabled]
            
            if not enabled_channels:
                logger.warning("No active output channels in configuration")
            else:
                logger.info(f"Loaded {len(enabled_channels)} active output channels:")
                for ch in enabled_channels:
                    logger.info(f"  - {ch.name} (ID: {ch.telegram_id})")

        except Exception as e:
            logger.error(f"Error loading output channels: {e}")
            self.output_channels = []
    
    async def load_input_sources(self) -> None:
        """Load all unique input sources from output channels."""
        try:
            # Collect all unique sources
            all_sources = self.config_manager.get_all_input_sources()

            if not all_sources:
                logger.warning("No sources found for monitoring")
                return

            logger.info(f"Loading {len(all_sources)} input sources...")

            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.errors import UserAlreadyParticipantError, ChannelPrivateError

            for source in all_sources:
                try:
                    # If it's an ID (number), convert to int
                    if source.lstrip('-').isdigit():
                        channel_id = int(source)
                        entity = await self.client.get_entity(channel_id)
                    else:
                        # Otherwise it's a username, get entity
                        entity = await self.client.get_entity(source)
                        channel_id = entity.id

                    # Get channel title
                    channel_title = self._get_chat_title(entity)

                    # Ensure bot is subscribed to the channel
                    try:
                        await self.client(JoinChannelRequest(entity))
                        logger.info(f"  ✓ Joined '{source}' → ID={channel_id}, title='{channel_title}'")
                    except UserAlreadyParticipantError:
                        logger.info(f"  ✓ Already member of '{source}' → ID={channel_id}")
                    except ChannelPrivateError:
                        logger.warning(f"  ⚠ Private channel '{source}', need invite link")
                    except Exception as join_err:
                        if "USER_ALREADY_PARTICIPANT" in str(join_err):
                            logger.info(f"  ✓ Already member of '{source}' → ID={channel_id}")
                        else:
                            logger.warning(f"  ⚠ Could not join '{source}': {join_err}")

                    self.monitored_sources.add(channel_id)
                    self.channel_names[channel_id] = channel_title

                    # Update source status
                    status_manager.update_source_status(
                        source,
                        channel_id=channel_id,
                        accessible=True,
                        is_member=True,
                        title=channel_title
                    )

                except Exception as e:
                    logger.error(f"  ✗ Error loading source '{source}': {e}")
                    # Update source status as inaccessible
                    status_manager.update_source_status(
                        source,
                        accessible=False,
                        is_member=False,
                        error=str(e)
                    )
            
            logger.info(f"Total loaded {len(self.monitored_sources)} sources for monitoring")
            logger.info(f"[DEBUG] monitored_sources IDs: {sorted(self.monitored_sources)[:10]}...")

        except Exception as e:
            logger.error(f"Error loading input sources: {e}")

    async def _init_job_analyzer(self):
        """Initialize LLM-based job analyzer."""
        try:
            job_config = self.config_manager.job_analyzer
            self.job_analyzer = JobAnalyzer(
                providers_config=self.config_manager.llm_providers,
                min_salary_rub=job_config.min_salary_rub,
                provider_name=job_config.llm_provider,
                model=job_config.llm_model if job_config.llm_model else None,
                require_tg_contact=job_config.require_tg_contact,
            )
            await self.job_analyzer.initialize()
            logger.info(
                f"Job analyzer initialized (LLM-based filtering enabled, "
                f"require_tg_contact={job_config.require_tg_contact})"
            )
        except Exception as e:
            logger.warning(f"Job analyzer init failed, will use regex only: {e}")
            self.job_analyzer = None

    async def _ensure_agents_in_crm_groups(self):
        """Ensure all linked agents are members of their CRM groups."""
        from telethon.tl.functions.channels import InviteToChannelRequest
        from src.connection_status import status_manager

        for channel in self.output_channels:
            if not channel.crm_enabled or not channel.crm_group_id or not channel.agents:
                continue

            logger.info(f"Checking agents for CRM group of '{channel.name}'...")

            try:
                crm_group = await self.client.get_entity(channel.crm_group_id)
            except Exception as e:
                logger.warning(f"  Cannot access CRM group {channel.crm_group_id}: {e}")
                continue

            for agent_config in channel.agents:
                agent_session = agent_config.session_name
                try:
                    agent = await get_existing_agent(agent_session)
                    if not agent or not agent.client:
                        logger.warning(f"  Agent {agent_session} not available")
                        continue

                    agent_me = await agent.client.get_me()
                    # Use username if available, otherwise try ID
                    user_to_invite = f"@{agent_me.username}" if agent_me.username else agent_me.id
                    try:
                        await self.client(InviteToChannelRequest(
                            channel=crm_group,
                            users=[user_to_invite]
                        ))
                        logger.info(f"  ✅ Added {agent_session} to CRM group")
                        # Agent can join groups
                        status_manager.update_agent_status(
                            agent_session, "connected",
                            can_join_groups=True
                        )
                    except Exception as invite_err:
                        err_str = str(invite_err)
                        if "USER_ALREADY_PARTICIPANT" in err_str or "already" in err_str.lower():
                            logger.debug(f"  Agent {agent_session} already in CRM group")
                            status_manager.update_agent_status(
                                agent_session, "connected",
                                can_join_groups=True
                            )
                        elif "Invalid object ID" in err_str or "USER_PRIVACY_RESTRICTED" in err_str:
                            # Agent is restricted from joining groups (Telegram limitation)
                            logger.warning(f"  ⚠️ Agent {agent_session} RESTRICTED from joining groups: {invite_err}")
                            status_manager.update_agent_status(
                                agent_session, "connected",
                                can_join_groups=False,
                                error=f"Group restricted: {err_str[:100]}"
                            )
                        else:
                            logger.warning(f"  Failed to add {agent_session}: {invite_err}")

                    # IMPORTANT: Make agent's client cache the CRM group entity
                    # This runs whether agent was just added or was already a member
                    try:
                        await agent.client.get_dialogs(limit=100)  # Refresh dialog list
                        entity = await agent.client.get_entity(channel.crm_group_id)
                        logger.info(f"  ✅ Agent {agent_session} cached CRM entity: {getattr(entity, 'title', 'N/A')}")
                        # Agent has CRM access
                        status_manager.update_agent_status(
                            agent_session, "connected",
                            crm_access=True
                        )
                    except Exception as cache_err:
                        logger.warning(f"  ⚠️ Agent {agent_session} can't cache CRM entity: {cache_err}")
                        status_manager.update_agent_status(
                            agent_session, "connected",
                            crm_access=False
                        )
                except Exception as e:
                    logger.warning(f"  Error processing agent {agent_session}: {e}")

    def _setup_log_filter(self):
        """Setup filter for replacing channel IDs with names in logs"""
        telethon_logger = logging.getLogger('telethon.client.updates')
        log_filter = ChannelNameLogFilter(self.channel_names)
        telethon_logger.addFilter(log_filter)
        
        root_telethon = logging.getLogger('telethon')
        root_telethon.addFilter(log_filter)
    
    def register_handlers(self):
        """Register event handlers"""

        @self.client.on(events.NewMessage())
        async def handle_new_message(event):
            """Handler for new messages"""
            try:
                message = event.message
                chat = await event.get_chat()

                # DEBUG: log all incoming messages
                logger.info(f"[DEBUG] Message from chat_id={chat.id}, monitored={chat.id in self.monitored_sources}, title={getattr(chat, 'title', 'N/A')}")

                # Check if we should monitor this chat
                if chat.id not in self.monitored_sources:
                    return

                # Ignore our own messages
                if message.out:
                    return

                await self.process_message(message, chat)

            except Exception as e:
                logger.error(f"Error processing new message: {e}", exc_info=True)

        logger.info("Event handlers registered")

    async def watch_config_changes(self) -> None:
        """Background task to watch for configuration changes."""
        logger.info("Started config change monitoring (checking every 30 sec)")

        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                if not self.config_file_path.exists():
                    continue

                # Get file modification time
                current_mtime = os.path.getmtime(self.config_file_path)

                # If file changed
                if self.last_config_mtime and current_mtime != self.last_config_mtime:
                    logger.info("Config changes detected! Reloading...")

                    # Small delay to ensure file write is complete (atomic replace should be instant, but just in case)
                    await asyncio.sleep(0.5)

                    # Reload configuration
                    await self.reload_configuration()

                    logger.info("Configuration reloaded successfully")
                
                self.last_config_mtime = current_mtime
                
            except Exception as e:
                logger.error(f"Error checking configuration: {e}")
    
    def _get_command_handlers(self) -> dict:
        """
        Command registry mapping command types to (handler, success_message_template).

        This pattern makes it easy to:
        - Add new commands (just add to dict)
        - Test commands individually
        - See all available commands at a glance
        """
        return {
            "connect_agent": (self._cmd_connect_agent, "Agent {target} connected"),
            "disconnect_agent": (self._cmd_disconnect_agent, "Agent {target} disconnected"),
            "delete_agent": (self._cmd_delete_agent, "Agent {target} deleted"),
            "connect_all": (self._cmd_connect_all, "Connected {result} agents"),
            "disconnect_all": (self._cmd_disconnect_all, "Disconnected {result} agents"),
            "health_check": (self._cmd_health_check, "Health check completed"),
            "send_crm_message": (self._cmd_send_crm_message, "Message sent"),
        }

    async def process_commands(self):
        """Background task to process commands from web interface"""
        logger.info("Command processor started (checking every 2 seconds)")
        handlers = self._get_command_handlers()

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

    async def _cmd_connect_agent(self, session_name: str):
        """Connect a specific agent"""
        session_path = get_agent_session_path(session_name)
        if not Path(f"{session_path}.session").exists():
            raise FileNotFoundError(f"Session file not found: {session_name}")

        # Find phone from config
        phone = None
        for channel in self.output_channels:
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

    async def _cmd_disconnect_agent(self, session_name: str):
        """Disconnect a specific agent"""
        agent = await get_existing_agent(session_name)
        if agent:
            await agent.disconnect()
            status_manager.update_agent_status(session_name, "disconnected")
            logger.info(f"Agent {session_name} disconnected")
        else:
            status_manager.update_agent_status(session_name, "disconnected")
            logger.info(f"Agent {session_name} was not connected")

    async def _cmd_delete_agent(self, session_name: str):
        """Disconnect and delete agent session file"""
        # First disconnect
        await self._cmd_disconnect_agent(session_name)

        # Remove from status tracking
        status_manager.remove_agent_status(session_name)

        # Delete session file
        session_file = SESSIONS_DIR / f"{session_name}.session"
        if session_file.exists():
            session_file.unlink()
            logger.info(f"Deleted session file: {session_file}")

    async def _cmd_connect_all(self) -> int:
        """Connect all agents from configuration"""
        count = 0
        for channel in self.output_channels:
            if channel.crm_enabled:
                for agent_config in channel.agents:
                    try:
                        await self._cmd_connect_agent(agent_config.session_name)
                        count += 1
                    except Exception as e:
                        logger.error(f"Failed to connect {agent_config.session_name}: {e}")
        return count

    async def _cmd_disconnect_all(self) -> int:
        """Disconnect all agents"""
        count = await disconnect_all_global_agents()
        # Update status for all agents
        status = status_manager.get_all_status()
        for session_name in status.get("agents", {}).keys():
            status_manager.update_agent_status(session_name, "disconnected")
        return count

    async def _cmd_health_check(self):
        """Check health of all connections"""
        # Bot status
        try:
            if self.client.is_connected():
                me = await self.client.get_me()
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

    async def _cmd_send_crm_message(self, target: dict):
        """Send a message to a CRM contact from web interface"""
        contact_id = int(target.get("contact_id"))
        message = target.get("message", "")

        if not contact_id or not message:
            raise ValueError("contact_id and message are required")

        # Find which channel has this contact
        channel_id = self.crm.contact_to_channel.get(contact_id)
        if not channel_id:
            # Try to find in conversation managers
            for ch_id, conv_manager in self.crm.conversation_managers.items():
                if contact_id in conv_manager._topic_cache:
                    channel_id = ch_id
                    self.crm.contact_to_channel[contact_id] = channel_id
                    break

        if not channel_id:
            raise ValueError(f"No channel found for contact {contact_id}")

        conv_manager = self.crm.conversation_managers.get(channel_id)
        if not conv_manager:
            raise ValueError(f"No conversation manager for channel {channel_id}")

        topic_id = conv_manager.get_topic_id(contact_id)
        if not topic_id:
            raise ValueError(f"No topic found for contact {contact_id}")

        # Get an available agent
        agent_pool = self.crm.agent_pools.get(channel_id)
        if not agent_pool:
            raise ValueError(f"No agent pool for channel {channel_id}")

        agent = self.crm.topic_to_agent.get(topic_id)
        if not agent:
            agent = agent_pool.get_available_agent()

        if not agent or not agent.client:
            raise ValueError("No available agent to send message")

        # Record in AI context
        ai_handler = self.crm.ai_handlers.get(channel_id)
        if ai_handler:
            ai_handler.add_operator_message(contact_id, message)

        # Send message to contact
        sent_message = await agent.client.send_message(contact_id, message)
        if sent_message:
            conv_manager.mark_agent_sent_message(sent_message.id)

        # Mirror to CRM topic
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

        logger.info(f"Sent CRM message to contact {contact_id} from web interface")

    async def reload_configuration(self) -> None:
        """Reload configuration without restarting the bot."""
        try:
            # Load output channels
            await self.load_output_channels()

            # Get new list of sources
            new_sources = self.config_manager.get_all_input_sources()
            new_sources_str = {str(s) for s in new_sources}

            # Add new sources (that aren't already monitored)
            for source in new_sources:
                # Check if source is already monitored
                already_monitored = False

                if source.lstrip('-').isdigit():
                    # This is an ID
                    source_id = int(source)
                    if source_id in self.monitored_sources:
                        already_monitored = True
                else:
                    # This is a username - check by name
                    for monitored_id in self.monitored_sources:
                        if self.channel_names.get(monitored_id, '').lower() == source.lower():
                            already_monitored = True
                            break

                if not already_monitored:
                    try:
                        # Load entity for new source
                        if source.lstrip('-').isdigit():
                            channel_id = int(source)
                            entity = await self.client.get_entity(channel_id)
                        else:
                            entity = await self.client.get_entity(source)
                            channel_id = entity.id

                        channel_title = self._get_chat_title(entity)
                        self.monitored_sources.add(channel_id)
                        self.channel_names[channel_id] = channel_title

                        # Update connection_status.json so web UI knows about new sources
                        status_manager.update_source_status(
                            source,
                            channel_id=channel_id,
                            accessible=True,
                            is_member=True,
                            title=channel_title
                        )

                        logger.info(f"  ➕ Added new source: {source} → {channel_title}")

                    except Exception as e:
                        logger.error(f"  ✗ Error loading new source '{source}': {e}")
                        # Update source status as inaccessible
                        status_manager.update_source_status(
                            source,
                            accessible=False,
                            is_member=False,
                            error=str(e)
                        )

            # Remove sources that are no longer in configuration
            sources_to_remove = []

            for monitored_id in list(self.monitored_sources):
                # Check if this ID is in new configuration
                found = False

                # Check by ID
                if str(monitored_id) in new_sources_str or str(-monitored_id) in new_sources_str:
                    found = True
                else:
                    # Check by username
                    for source in new_sources:
                        if not source.lstrip('-').isdigit():
                            try:
                                entity = await self.client.get_entity(source)
                                if entity.id == monitored_id:
                                    found = True
                                    break
                            except Exception:
                                pass

                if not found:
                    sources_to_remove.append(monitored_id)

            for source_id in sources_to_remove:
                channel_name = self.channel_names.get(source_id, str(source_id))
                self.monitored_sources.remove(source_id)
                if source_id in self.channel_names:
                    del self.channel_names[source_id]
                logger.info(f"  ➖ Removed source: {channel_name}")

            logger.info(f"Monitoring: {len(self.monitored_sources)} sources, {len(self.output_channels)} output channels")

            # Re-initialize CRM agents for new channels
            await self.crm.setup_agents(self.output_channels, self.config_manager)

            # Ensure all agents are in their CRM groups
            await self._ensure_agents_in_crm_groups()

        except Exception as e:
            logger.error(f"Error reloading configuration: {e}", exc_info=True)

    async def process_message(self, message, chat) -> None:
        """
        Process a message from a monitored chat for all output channels.

        Args:
            message: Telethon message object
            chat: Chat object
        """
        # Get chat title
        chat_title = self._get_chat_title(chat)

        logger.info(f"Received message {message.id} from chat '{chat_title}'")

        # Initial filtering
        if not message_processor.should_process_message(message):
            return

        # Check for duplicate
        is_duplicate = await db.check_duplicate(message.id, chat.id)
        if is_duplicate:
            logger.debug(f"Message {message.id} was already processed")
            return

        # === LLM Job Analysis ===
        analysis: Optional[JobAnalysisResult] = None
        if self.job_analyzer:
            try:
                analysis = await self.job_analyzer.analyze(message.text)

                if not analysis.is_relevant:
                    # Save as filtered by AI
                    await db.save_job(
                        message_id=message.id,
                        chat_id=chat.id,
                        chat_title=chat_title,
                        message_text=message.text,
                        position=None,
                        skills=[],
                        is_relevant=False,
                        ai_reason=analysis.rejection_reason or "Filtered by AI",
                        status='filtered_by_ai',
                        contact_username=analysis.contact_username
                    )
                    logger.info(f"Filtered by AI: {analysis.rejection_reason}")
                    return

                logger.debug(f"AI analysis passed: {analysis.analysis_summary}")

            except Exception as e:
                logger.warning(f"Job analysis error, continuing with regex: {e}")
                # Continue with traditional flow on error

        # Use LLM-extracted contact or fall back to regex
        if analysis and analysis.contact_username:
            contacts = {
                'telegram': analysis.contact_username,
                'email': None,
                'phone': None
            }
            # Still extract email/phone with regex
            regex_contacts = message_processor.extract_contact_info(message.text)
            contacts['email'] = regex_contacts.get('email')
            contacts['phone'] = regex_contacts.get('phone')
        else:
            contacts = message_processor.extract_contact_info(message.text)

        keywords = message_processor.extract_keywords(message.text)
        payment_info = message_processor.extract_payment_info(message.text)

        # Determine which output channels should receive this message
        matching_outputs = self._find_matching_outputs(chat, message.text, keywords)

        if not matching_outputs:
            logger.debug("Message doesn't match any output channel")
            # Save as not relevant
            await db.save_job(
                message_id=message.id,
                chat_id=chat.id,
                chat_title=chat_title,
                message_text=message.text,
                position=None,
                skills=keywords,
                is_relevant=False,
                ai_reason="No matching output channels",
                status='not_relevant',
                contact_username=contacts.get('telegram')
            )
            return

        # Save to database
        await db.save_job(
            message_id=message.id,
            chat_id=chat.id,
            chat_title=chat_title,
            message_text=message.text,
            position=None,
            skills=keywords,
            is_relevant=True,
            ai_reason=f"Matches {len(matching_outputs)} output channels",
            status='relevant',
            contact_username=contacts.get('telegram')
        )

        # Send notifications to all matching output channels
        await self.send_notifications(
            message=message,
            chat=chat,
            chat_title=chat_title,
            keywords=keywords,
            contacts=contacts,
            payment_info=payment_info,
            output_channels=matching_outputs
        )

        # CRM workflow: auto-response + topic creation
        await self.crm.handle_crm_workflow(
            message=message,
            chat=chat,
            chat_title=chat_title,
            matching_outputs=matching_outputs,
            contacts=contacts,
            message_processor=message_processor
        )
    
    def _find_matching_outputs(
        self,
        chat,
        text: str,
        keywords: List[str]
    ) -> List[ChannelConfig]:
        """
        Find output channels that match the given message

        Args:
            chat: Source chat object
            text: Message text
            keywords: Found keywords

        Returns:
            List of matching output channels
        """
        matching = []
        text_lower = text.lower()

        # Get all output channels that monitor this source
        source_id = str(chat.id)
        potential_outputs = self.config_manager.get_output_channels_for_source(source_id)

        # If not found by ID, try by username
        if not potential_outputs and hasattr(chat, 'username') and chat.username:
            potential_outputs = self.config_manager.get_output_channels_for_source(f"@{chat.username}")

        # Check filters for each output channel
        for output in potential_outputs:
            if self._check_filters(text_lower, keywords, output.filters):
                matching.append(output)

        return matching

    def _check_filters(self, text_lower: str, _keywords: List[str], filters) -> bool:
        """
        Check filters for a channel.

        Args:
            text_lower: Message text in lowercase
            _keywords: Extracted keywords (reserved for future use)
            filters: FilterConfig object

        Returns:
            True if message passes filters
        """
        # Check include keywords
        if filters.include_keywords:
            include_lower = [kw.lower() for kw in filters.include_keywords]

            if filters.require_all_includes:
                # ALL keywords required
                if not all(kw in text_lower for kw in include_lower):
                    return False
            else:
                # AT LEAST ONE keyword required
                if not any(kw in text_lower for kw in include_lower):
                    return False

        # Check exclude keywords
        if filters.exclude_keywords:
            exclude_lower = [kw.lower() for kw in filters.exclude_keywords]

            # If any exclude keyword found - reject
            if any(kw in text_lower for kw in exclude_lower):
                logger.debug(f"Message contains exclude words: {[kw for kw in exclude_lower if kw in text_lower]}")
                return False

        return True

    async def send_notifications(
        self,
        message,
        chat,
        chat_title: str,
        keywords: List[str],
        contacts: dict,
        _payment_info: dict,
        output_channels: List[ChannelConfig]
    ) -> None:
        """Send notifications to all matching output channels.

        Note: _payment_info is extracted but not yet used in notification format.
        """
        logger.info(f"Sending notifications to {len(output_channels)} output channels...")

        # Get sender info
        sender_info = message_processor.get_sender_info(message)

        # Build message link
        message_link = message_processor.get_message_link(message, chat)

        # Format notification
        lines = []
        lines.append("🎯 **New vacancy!**")
        lines.append("")
        lines.append(f"📍 **Chat:** {chat_title}")

        if keywords:
            lines.append(f"🛠 **Skills:** {', '.join(keywords[:5])}")
        
        lines.append("")
        lines.append(f"🔗 **Link:** {message_link}")
        
        # Contacts
        contacts_list = []

        if sender_info.get('username'):
            contacts_list.append(f"✉️ {sender_info['username']}")
        elif sender_info.get('full_name'):
            contacts_list.append(f"👤 {sender_info['full_name']}")

        if contacts.get('telegram') and contacts['telegram'] != sender_info.get('username'):
            contacts_list.append(f"✉️ {contacts['telegram']}")
        if contacts.get('email'):
            contacts_list.append(f"📧 {contacts['email']}")
        if contacts.get('phone'):
            contacts_list.append(f"📞 {contacts['phone']}")

        if contacts_list:
            lines.append("")
            lines.append("**Contacts:**")
            for contact in contacts_list:
                lines.append(f"   {contact}")

        notification_text = '\n'.join(lines)

        # Send to all output channels
        success_count = 0
        for output in output_channels:
            try:
                # Get channel entity so Telethon knows about it
                try:
                    entity = await self.client.get_entity(output.telegram_id)
                    entity_title = self._get_chat_title(entity)
                    logger.info(f"  📤 Sending to '{output.name}' → Telegram: '{entity_title}' (ID: {output.telegram_id})")
                except Exception as entity_error:
                    logger.error(f"  ✗ Failed to get entity for '{output.name}' (ID: {output.telegram_id}): {entity_error}")
                    logger.info(f"  💡 Make sure bot has access to this channel/group")
                    continue

                # Send message
                await self.client.send_message(
                    entity,
                    notification_text
                )
                success_count += 1

            except Exception as e:
                logger.error(f"  ✗ Error sending to '{output.name}': {e}")

        if success_count > 0:
            logger.info(f"Successfully sent {success_count}/{len(output_channels)} notifications")

    def _get_chat_title(self, chat) -> str:
        """Get chat title."""
        if isinstance(chat, User):
            return f"{chat.first_name} {chat.last_name or ''}".strip()
        elif isinstance(chat, (Chat, Channel)):
            return chat.title or f"Chat {chat.id}"
        else:
            return f"Unknown chat {chat.id}"

    async def run(self) -> None:
        """Main bot loop."""
        logger.info("Bot started monitoring messages...")
        logger.info("Press Ctrl+C to stop")

        # Update bot status
        try:
            me = await self.client.get_me()
            user_info = {
                "id": me.id,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "username": me.username,
                "phone": me.phone
            }
            status_manager.update_bot_status(True, True, user_info)
        except Exception as e:
            logger.error(f"Failed to update bot status: {e}")
            status_manager.update_bot_status(True, False)

        # Start background tasks
        config_watcher = asyncio.create_task(self.watch_config_changes())
        command_processor = asyncio.create_task(self.process_commands())

        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("Stop signal received")
        finally:
            config_watcher.cancel()
            command_processor.cancel()
            await self.stop()

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("Stopping bot...")
        self.is_running = False

        # Update bot status
        status_manager.update_bot_status(False, False)

        # Clean up CRM resources
        await self.crm.cleanup()

        # Disconnect all global agents
        await disconnect_all_global_agents()

        # Update all agents to disconnected
        status = status_manager.get_all_status()
        for session_name in status.get("agents", {}).keys():
            status_manager.update_agent_status(session_name, "disconnected")

        # Close database connection
        await db.close()

        if self.client.is_connected():
            await self.client.disconnect()

        logger.info("Bot stopped")


# Global bot instance
bot = MultiChannelJobMonitorBot()


def get_bot_client():
    """Return bot client if connected, otherwise None."""
    if bot and bot.client and bot.client.is_connected():
        return bot.client
    return None


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    async def main():
        try:
            await bot.start()
            await bot.run()
        except NeedsAuthenticationError as e:
            logger.error(f"❌ {e}")
            logger.info("Start web interface: python3 -m uvicorn web.app:app --port 8080")
        except KeyboardInterrupt:
            logger.info("Stopped by Ctrl+C")
        finally:
            await bot.stop()

    asyncio.run(main())

