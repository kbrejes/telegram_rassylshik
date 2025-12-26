"""
Telegram userbot для мониторинга вакансий с поддержкой множественных каналов
+ CRM функциональность (автоответы и трансляция в топики)
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
from src.config_manager import ConfigManager, ChannelConfig, AIConfig
from src.agent_pool import disconnect_all_global_agents, get_or_create_agent, get_existing_agent
from src.crm_handler import CRMHandler
from src.session_config import get_bot_session_path, get_agent_session_path, SESSIONS_DIR
from src.connection_status import status_manager
from src.command_queue import command_queue
from src.job_analyzer import JobAnalyzer, JobAnalysisResult

logger = logging.getLogger(__name__)


class NeedsAuthenticationError(Exception):
    """Исключение: требуется авторизация через веб-интерфейс"""
    pass


class ChannelNameLogFilter(logging.Filter):
    """Фильтр для замены ID каналов на их имена в логах"""
    
    def __init__(self, channel_map: Dict[int, str]):
        super().__init__()
        self.channel_map = channel_map
        self.unknown_channels = set()
    
    def filter(self, record):
        """Заменяет ID каналов на имена в сообщениях логов"""
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
    """Класс для мониторинга вакансий с поддержкой множественных output каналов"""

    def __init__(self):
        # Используем абсолютный путь к сессии из session_config
        self.client = TelegramClient(
            get_bot_session_path(),
            config.API_ID,
            config.API_HASH
        )
        
        self.monitored_sources: Set[int] = set()  # ID источников для мониторинга
        self.channel_names: Dict[int, str] = {}  # ID -> название
        
        # Config manager для работы с output каналами
        self.config_manager = ConfigManager()
        self.output_channels: List[ChannelConfig] = []

        # CRM функциональность (вынесено в отдельный модуль)
        self.crm = CRMHandler(self)

        # Job analyzer (LLM-based filtering)
        self.job_analyzer: Optional[JobAnalyzer] = None

        # Для отслеживания изменений конфигурации
        self.config_file_path = Path("configs/channels_config.json")
        self.last_config_mtime = None
        
        self.is_running = False

    async def check_session_valid(self) -> bool:
        """Проверяет существует ли валидная сессия"""
        session_path = Path(f"{get_bot_session_path()}.session")
        if not session_path.exists():
            return False

        try:
            if not self.client.is_connected():
                await self.client.connect()
            return await self.client.is_user_authorized()
        except Exception as e:
            logger.debug(f"Ошибка проверки сессии: {e}")
            return False

    async def start(self, wait_for_auth: bool = True):
        """
        Запуск бота с проверкой сессии

        Args:
            wait_for_auth: Если True и нет сессии - ждать авторизации через веб.
                          Если False - пытаться авторизоваться автоматически.
        """
        logger.info("Запуск Multi-Channel Telegram userbot...")

        # Устанавливаем главный поток для агентов
        # Агенты должны подключаться только из этого потока
        from src.agent_pool import set_main_thread
        set_main_thread()

        if not self.client.is_connected():
            await self.client.connect()

        # Если уже авторизованы - не нужно отправлять код
        if await self.client.is_user_authorized():
            logger.info("Найдена существующая сессия, используем её")
        else:
            # Сессии нет - нужна авторизация
            if wait_for_auth:
                # НЕ пытаемся автоматически авторизоваться
                # Ждём пока пользователь авторизуется через веб-интерфейс
                logger.info("Сессия не найдена. Ожидание авторизации через веб-интерфейс...")
                raise NeedsAuthenticationError("Требуется авторизация через веб-интерфейс")
            else:
                # Старое поведение - автоматическая авторизация (может вызвать FloodWait)
                logger.info("Сессия не найдена, попытка авторизации...")
                await self.client.start(phone=config.PHONE)

        # Проверка авторизации
        me = await self.client.get_me()
        logger.info(f"Бот авторизован как: {me.first_name} ({me.phone})")
        
        # Подключение к базе данных
        await db.connect()
        
        # Загрузка конфигурации output каналов
        await self.load_output_channels()

        # Загрузка всех уникальных input источников
        await self.load_input_sources()

        # Инициализация LLM job analyzer
        await self._init_job_analyzer()

        # Инициализация CRM агентов и conversation managers
        await self.crm.setup_agents(self.output_channels, self.config_manager)

        # Ensure all agents are in their CRM groups
        await self._ensure_agents_in_crm_groups()

        # Настройка фильтра логов
        self._setup_log_filter()
        
        # Регистрация обработчиков событий
        self.register_handlers()
        
        # Сохраняем время модификации конфига при старте
        if self.config_file_path.exists():
            self.last_config_mtime = os.path.getmtime(self.config_file_path)
    
    async def load_output_channels(self):
        """Загружает конфигурацию output каналов из ConfigManager"""
        try:
            self.output_channels = self.config_manager.load()
            
            enabled_channels = [ch for ch in self.output_channels if ch.enabled]
            
            if not enabled_channels:
                logger.warning("Нет активных output каналов в конфигурации")
            else:
                logger.info(f"Загружено {len(enabled_channels)} активных output каналов:")
                for ch in enabled_channels:
                    logger.info(f"  - {ch.name} (ID: {ch.telegram_id})")
        
        except Exception as e:
            logger.error(f"Ошибка загрузки output каналов: {e}")
            self.output_channels = []
    
    async def load_input_sources(self):
        """Загружает все уникальные input источники из output каналов"""
        try:
            # Собираем все уникальные источники
            all_sources = self.config_manager.get_all_input_sources()
            
            if not all_sources:
                logger.warning("Не найдено источников для мониторинга")
                return
            
            logger.info(f"Загрузка {len(all_sources)} input источников...")
            
            for source in all_sources:
                try:
                    # Если это ID (число), преобразуем в int
                    if source.lstrip('-').isdigit():
                        channel_id = int(source)
                        entity = await self.client.get_entity(channel_id)
                    else:
                        # Иначе это username, получаем entity
                        entity = await self.client.get_entity(source)
                        channel_id = entity.id

                    # Получаем название канала
                    channel_title = self._get_chat_title(entity)

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
                    logger.error(f"  ✗ Ошибка загрузки источника '{source}': {e}")
                    # Update source status as inaccessible
                    status_manager.update_source_status(
                        source,
                        accessible=False,
                        is_member=False,
                        error=str(e)
                    )
            
            logger.info(f"Всего загружено {len(self.monitored_sources)} источников для мониторинга")

        except Exception as e:
            logger.error(f"Ошибка при загрузке input источников: {e}")

    async def _init_job_analyzer(self):
        """Initialize LLM-based job analyzer."""
        try:
            self.job_analyzer = JobAnalyzer(
                providers_config=self.config_manager.llm_providers,
                min_salary_rub=70_000,
                provider_name="groq",
            )
            await self.job_analyzer.initialize()
            logger.info("Job analyzer initialized (LLM-based filtering enabled)")
        except Exception as e:
            logger.warning(f"Job analyzer init failed, will use regex only: {e}")
            self.job_analyzer = None

    async def _ensure_agents_in_crm_groups(self):
        """Ensure all linked agents are members of their CRM groups."""
        from telethon.tl.functions.channels import InviteToChannelRequest

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
                    except Exception as invite_err:
                        err_str = str(invite_err)
                        if "USER_ALREADY_PARTICIPANT" in err_str or "already" in err_str.lower():
                            logger.debug(f"  Agent {agent_session} already in CRM group")
                        else:
                            logger.warning(f"  Failed to add {agent_session}: {invite_err}")
                except Exception as e:
                    logger.warning(f"  Error processing agent {agent_session}: {e}")

    def _setup_log_filter(self):
        """Настраивает фильтр для замены ID каналов на имена в логах"""
        telethon_logger = logging.getLogger('telethon.client.updates')
        log_filter = ChannelNameLogFilter(self.channel_names)
        telethon_logger.addFilter(log_filter)
        
        root_telethon = logging.getLogger('telethon')
        root_telethon.addFilter(log_filter)
    
    def register_handlers(self):
        """Регистрирует обработчики событий"""
        
        @self.client.on(events.NewMessage())
        async def handle_new_message(event):
            """Обработчик новых сообщений"""
            try:
                message = event.message
                chat = await event.get_chat()

                # Проверяем, нужно ли мониторить этот чат
                if chat.id not in self.monitored_sources:
                    return

                # Игнорируем собственные сообщения
                if message.out:
                    return

                await self.process_message(message, chat)
            
            except Exception as e:
                logger.error(f"Ошибка при обработке нового сообщения: {e}", exc_info=True)
        
        logger.info("Обработчики событий зарегистрированы")
    
    async def watch_config_changes(self):
        """Фоновая задача для отслеживания изменений конфигурации"""
        logger.info("Запущен мониторинг изменений конфигурации (проверка каждые 30 сек)")
        
        while True:
            try:
                await asyncio.sleep(30)  # Проверка каждые 30 секунд
                
                if not self.config_file_path.exists():
                    continue
                
                # Получаем время модификации файла
                current_mtime = os.path.getmtime(self.config_file_path)
                
                # Если файл изменился
                if self.last_config_mtime and current_mtime != self.last_config_mtime:
                    logger.info("Обнаружены изменения в конфигурации! Перезагрузка...")

                    # Small delay to ensure file write is complete (atomic replace should be instant, but just in case)
                    await asyncio.sleep(0.5)

                    # Перезагружаем конфигурацию
                    await self.reload_configuration()
                    
                    logger.info("Конфигурация перезагружена успешно")
                
                self.last_config_mtime = current_mtime
                
            except Exception as e:
                logger.error(f"Ошибка при проверке конфигурации: {e}")
    
    async def process_commands(self):
        """Background task to process commands from web interface"""
        logger.info("Command processor started (checking every 2 seconds)")

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
                        if cmd.type == "connect_agent":
                            await self._cmd_connect_agent(cmd.target)
                            command_queue.mark_completed(cmd.id, True, f"Agent {cmd.target} connected")

                        elif cmd.type == "disconnect_agent":
                            await self._cmd_disconnect_agent(cmd.target)
                            command_queue.mark_completed(cmd.id, True, f"Agent {cmd.target} disconnected")

                        elif cmd.type == "delete_agent":
                            await self._cmd_delete_agent(cmd.target)
                            command_queue.mark_completed(cmd.id, True, f"Agent {cmd.target} deleted")

                        elif cmd.type == "connect_all":
                            count = await self._cmd_connect_all()
                            command_queue.mark_completed(cmd.id, True, f"Connected {count} agents")

                        elif cmd.type == "disconnect_all":
                            count = await self._cmd_disconnect_all()
                            command_queue.mark_completed(cmd.id, True, f"Disconnected {count} agents")

                        elif cmd.type == "health_check":
                            await self._cmd_health_check()
                            command_queue.mark_completed(cmd.id, True, "Health check completed")

                        else:
                            command_queue.mark_completed(cmd.id, False, f"Unknown command: {cmd.type}")

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
            status_manager.update_agent_status(session_name, "error", phone or "", error="Failed to connect")
            raise Exception(f"Failed to connect agent {session_name}")

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

    async def reload_configuration(self):
        """Перезагрузка конфигурации без перезапуска бота"""
        try:
            # Загружаем output каналы
            await self.load_output_channels()
            
            # Получаем новый список источников
            new_sources = self.config_manager.get_all_input_sources()
            new_sources_str = {str(s) for s in new_sources}
            
            # Добавляем новые источники (которых еще нет)
            for source in new_sources:
                source_str = str(source)
                
                # Проверяем есть ли уже этот источник
                already_monitored = False
                
                if source.lstrip('-').isdigit():
                    # Это ID
                    source_id = int(source)
                    if source_id in self.monitored_sources:
                        already_monitored = True
                else:
                    # Это username - проверяем по имени
                    for monitored_id in self.monitored_sources:
                        if self.channel_names.get(monitored_id, '').lower() == source.lower():
                            already_monitored = True
                            break
                
                if not already_monitored:
                    try:
                        # Загружаем entity для нового источника
                        if source.lstrip('-').isdigit():
                            channel_id = int(source)
                            entity = await self.client.get_entity(channel_id)
                        else:
                            entity = await self.client.get_entity(source)
                            channel_id = entity.id
                        
                        channel_title = self._get_chat_title(entity)
                        self.monitored_sources.add(channel_id)
                        self.channel_names[channel_id] = channel_title
                        
                        logger.info(f"  ➕ Добавлен новый источник: {source} → {channel_title}")
                    
                    except Exception as e:
                        logger.error(f"  ✗ Ошибка загрузки нового источника '{source}': {e}")
            
            # Удаляем источники, которых больше нет в конфигурации
            sources_to_remove = []
            
            for monitored_id in list(self.monitored_sources):
                # Проверяем есть ли этот ID в новой конфигурации
                found = False
                
                # Проверка по ID
                if str(monitored_id) in new_sources_str or str(-monitored_id) in new_sources_str:
                    found = True
                else:
                    # Проверка по username
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
                logger.info(f"  ➖ Удален источник: {channel_name}")

            logger.info(f"Мониторится: {len(self.monitored_sources)} источников, {len(self.output_channels)} output каналов")

            # Переинициализируем CRM агентов для новых каналов
            await self.crm.setup_agents(self.output_channels, self.config_manager)

            # Ensure all agents are in their CRM groups
            await self._ensure_agents_in_crm_groups()

        except Exception as e:
            logger.error(f"Ошибка перезагрузки конфигурации: {e}", exc_info=True)
    
    async def process_message(self, message, chat):
        """
        Обрабатывает сообщение из отслеживаемого чата для всех output каналов

        Args:
            message: Объект сообщения Telethon
            chat: Объект чата
        """
        # Получаем название чата
        chat_title = self._get_chat_title(chat)

        logger.info(f"Получено сообщение {message.id} из чата '{chat_title}'")

        # Первичная фильтрация
        if not message_processor.should_process_message(message):
            return

        # Проверка на дубликат
        is_duplicate = await db.check_duplicate(message.id, chat.id)
        if is_duplicate:
            logger.debug(f"Сообщение {message.id} уже обрабатывалось ранее")
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
        
        # Определяем в какие output каналы нужно отправить это сообщение
        matching_outputs = self._find_matching_outputs(chat, message.text, keywords)
        
        if not matching_outputs:
            logger.debug("Сообщение не подходит ни под один output канал")
            # Сохраняем как нерелевантное
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
        
        # Сохраняем в базу данных
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
        
        # Отправляем уведомления во все matching output каналы
        await self.send_notifications(
            message=message,
            chat=chat,
            chat_title=chat_title,
            keywords=keywords,
            contacts=contacts,
            payment_info=payment_info,
            output_channels=matching_outputs
        )
        
        # CRM workflow: автоответ + создание топика
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
        Находит output каналы, которым подходит данное сообщение
        
        Args:
            chat: Объект чата источника
            text: Текст сообщения
            keywords: Найденные ключевые слова
        
        Returns:
            Список подходящих output каналов
        """
        matching = []
        text_lower = text.lower()
        
        # Получаем все output каналы, которые мониторят этот источник
        source_id = str(chat.id)
        potential_outputs = self.config_manager.get_output_channels_for_source(source_id)
        
        # Если нет по ID, пробуем по username
        if not potential_outputs and hasattr(chat, 'username') and chat.username:
            potential_outputs = self.config_manager.get_output_channels_for_source(f"@{chat.username}")
        
        # Проверяем фильтры для каждого output канала
        for output in potential_outputs:
            if self._check_filters(text_lower, keywords, output.filters):
                matching.append(output)
        
        return matching
    
    def _check_filters(self, text_lower: str, keywords: List[str], filters) -> bool:
        """
        Проверка фильтров для канала
        
        Args:
            text_lower: Текст сообщения в нижнем регистре
            keywords: Найденные ключевые слова
            filters: Объект FilterConfig
        
        Returns:
            True если сообщение проходит фильтры
        """
        # Проверка включающих ключевых слов
        if filters.include_keywords:
            include_lower = [kw.lower() for kw in filters.include_keywords]
            
            if filters.require_all_includes:
                # Требуются ВСЕ ключевые слова
                if not all(kw in text_lower for kw in include_lower):
                    return False
            else:
                # Требуется ХОТЯ БЫ одно ключевое слово
                if not any(kw in text_lower for kw in include_lower):
                    return False
        
        # Проверка исключающих ключевых слов
        if filters.exclude_keywords:
            exclude_lower = [kw.lower() for kw in filters.exclude_keywords]
            
            # Если есть хотя бы одно исключающее слово - отклоняем
            if any(kw in text_lower for kw in exclude_lower):
                logger.debug(f"Сообщение содержит исключающие слова: {[kw for kw in exclude_lower if kw in text_lower]}")
                return False
        
        return True
    
    async def send_notifications(
        self,
        message,
        chat,
        chat_title: str,
        keywords: List[str],
        contacts: dict,
        payment_info: dict,
        output_channels: List[ChannelConfig]
    ):
        """Отправляет уведомления во все подходящие output каналы"""
        logger.info(f"Отправка уведомлений в {len(output_channels)} output каналов...")
        
        # Получаем информацию об отправителе
        sender_info = message_processor.get_sender_info(message)
        
        # Формируем ссылку на сообщение
        message_link = message_processor.get_message_link(message, chat)
        
        # Форматируем уведомление
        lines = []
        lines.append("🎯 **Новая вакансия!**")
        lines.append("")
        lines.append(f"📍 **Чат:** {chat_title}")
        
        if keywords:
            lines.append(f"🛠 **Навыки:** {', '.join(keywords[:5])}")
        
        lines.append("")
        lines.append(f"🔗 **Перейти:** {message_link}")
        
        # Контакты
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
            lines.append("**Контакты:**")
            for contact in contacts_list:
                lines.append(f"   {contact}")
        
        notification_text = '\n'.join(lines)
        
        # Отправляем во все output каналы
        success_count = 0
        for output in output_channels:
            try:
                # Получаем entity канала чтобы Telethon знал о нём
                try:
                    entity = await self.client.get_entity(output.telegram_id)
                    entity_title = self._get_chat_title(entity)
                    logger.info(f"  📤 Отправка в '{output.name}' → Telegram: '{entity_title}' (ID: {output.telegram_id})")
                except Exception as entity_error:
                    logger.error(f"  ✗ Не удалось получить entity для '{output.name}' (ID: {output.telegram_id}): {entity_error}")
                    logger.info(f"  💡 Убедитесь что бот имеет доступ к этому каналу/группе")
                    continue
                
                # Отправляем сообщение
                sent_message = await self.client.send_message(
                    entity,
                    notification_text
                )
                success_count += 1
            
            except Exception as e:
                logger.error(f"  ✗ Ошибка отправки в '{output.name}': {e}")
        
        if success_count > 0:
            logger.info(f"Успешно отправлено {success_count}/{len(output_channels)} уведомлений")
    
    def _get_chat_title(self, chat) -> str:
        """Получает название чата"""
        if isinstance(chat, User):
            return f"{chat.first_name} {chat.last_name or ''}".strip()
        elif isinstance(chat, (Chat, Channel)):
            return chat.title or f"Chat {chat.id}"
        else:
            return f"Unknown chat {chat.id}"
    
    async def run(self):
        """Основной цикл работы бота"""
        logger.info("Бот начал мониторинг сообщений...")
        logger.info("Нажмите Ctrl+C для остановки")

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

        # Запускаем фоновые задачи
        config_watcher = asyncio.create_task(self.watch_config_changes())
        command_processor = asyncio.create_task(self.process_commands())

        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("Получен сигнал остановки")
        finally:
            config_watcher.cancel()
            command_processor.cancel()
            await self.stop()
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Остановка бота...")
        self.is_running = False

        # Update bot status
        status_manager.update_bot_status(False, False)

        # Очищаем CRM ресурсы
        await self.crm.cleanup()

        # Отключаем всех глобальных агентов
        await disconnect_all_global_agents()

        # Update all agents to disconnected
        status = status_manager.get_all_status()
        for session_name in status.get("agents", {}).keys():
            status_manager.update_agent_status(session_name, "disconnected")

        # Закрываем соединение с БД
        await db.close()

        if self.client.is_connected():
            await self.client.disconnect()

        logger.info("Бот остановлен")


# Глобальный экземпляр бота
bot = MultiChannelJobMonitorBot()


def get_bot_client():
    """Возвращает клиент бота если он подключён, иначе None"""
    if bot and bot.client and bot.client.is_connected():
        return bot.client
    return None


if __name__ == "__main__":
    # Настройка логирования
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
            logger.info("Запустите веб-интерфейс: python3 -m uvicorn web.app:app --port 8080")
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C")
        finally:
            await bot.stop()

    asyncio.run(main())

