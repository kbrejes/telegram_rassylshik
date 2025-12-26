"""
Bot Conversation Manager - handles interactions with Telegram bots for job applications
"""
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

from telethon import TelegramClient, events
from telethon.tl.types import (
    ReplyInlineMarkup, ReplyKeyboardMarkup,
    KeyboardButtonRow, KeyboardButton,
    Message, User, DocumentAttributeFilename
)
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
from telethon.errors import (
    UserIsBlockedError, UserNotMutualContactError,
    FloodWaitError, BotResponseTimeoutError
)

from src.database import db
from src.candidate_profile import CandidateProfile, load_candidate_profile

logger = logging.getLogger(__name__)


# Constants
MAX_MESSAGES = 15  # Max messages per bot conversation
CONVERSATION_TIMEOUT = 300  # 5 minutes
RESPONSE_TIMEOUT = 60  # Wait 60s for bot response

# Success indicators (bot confirmed application)
SUCCESS_INDICATORS = [
    "заявка принята", "application received", "резюме принято",
    "мы свяжемся", "we will contact", "свяжемся с вами",
    "спасибо за отклик", "thank you for applying", "благодарим",
    "ваша анкета", "your application", "анкета принята",
    "успешно", "successfully", "отправлено", "submitted",
    "получили ваше", "we received your", "зарегистрирован",
]

# Failure indicators
FAILURE_INDICATORS = [
    "попробуйте позже", "try later",
    "ошибка", "error",
    "не удалось", "failed",
    "заблокирован", "blocked",
    "недоступен", "unavailable",
    "технические работы", "maintenance",
]

# Question type detection patterns
QUESTION_PATTERNS = {
    "name": [r"имя", r"name", r"как вас зовут", r"представьтесь", r"ваше имя"],
    "phone": [r"телефон", r"phone", r"номер", r"контакт.*тел"],
    "email": [r"почт[аы]", r"email", r"e-mail", r"mail"],
    "position": [r"должность", r"position", r"вакансия", r"позиция", r"на какую"],
    "experience": [r"опыт", r"experience", r"сколько лет", r"стаж"],
    "salary": [r"зарплат", r"salary", r"ожидани", r"оклад", r"сколько.*хотите"],
    "resume": [r"резюме", r"resume", r"cv", r"файл", r"документ", r"прикрепи"],
    "portfolio": [r"портфолио", r"portfolio", r"примеры.*работ", r"работы"],
    "about": [r"о себе", r"about yourself", r"расскажите", r"опишите себя"],
    "location": [r"город", r"location", r"где.*живете", r"регион", r"локаци"],
    "skills": [r"навыки", r"skills", r"умеете", r"технологии", r"стек"],
}

# Button patterns for "Apply" action
APPLY_BUTTON_PATTERNS = [
    r"откликнуться", r"подать", r"apply", r"отправить.*резюме",
    r"хочу.*работать", r"заинтересован", r"интересует", r"связаться",
    r"отправить.*заявку", r"submit", r"записаться", r"зарегистрироваться",
]


class BotConversationManager:
    """Manages conversations with Telegram bots for job applications"""

    def __init__(
        self,
        client: TelegramClient,
        profile: Optional[CandidateProfile] = None,
        vacancy_text: Optional[str] = None,
    ):
        self.client = client
        self.profile = profile or load_candidate_profile()
        self.vacancy_text = vacancy_text or ""

        # Conversation state
        self.interaction_id: Optional[int] = None
        self.bot_username: str = ""
        self.messages_sent: int = 0
        self.messages_received: int = 0
        self.conversation_started: Optional[datetime] = None
        self.is_complete: bool = False
        self.status: str = "pending"
        self.last_bot_message: Optional[Message] = None

        # For waiting for bot responses
        self._response_event = asyncio.Event()
        self._response_handler = None

    async def start_conversation(
        self,
        bot_username: str,
        vacancy_id: Optional[int] = None,
        channel_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start a conversation with a bot.

        Returns:
            Dict with status, success, error_reason
        """
        self.bot_username = bot_username.lstrip("@")
        self.conversation_started = datetime.now()

        logger.info(f"Starting bot conversation with @{self.bot_username}")

        try:
            # Check if already contacted
            if await db.check_bot_already_contacted(self.bot_username):
                logger.info(f"Bot @{self.bot_username} already contacted in last 24h, skipping")
                return {
                    "status": "skipped",
                    "success": False,
                    "error_reason": "already_contacted"
                }

            # Create interaction record
            self.interaction_id = await db.create_bot_interaction(
                self.bot_username, vacancy_id, channel_id
            )

            # Get bot entity
            try:
                bot_entity = await self.client.get_entity(self.bot_username)
            except Exception as e:
                logger.error(f"Cannot find bot @{self.bot_username}: {e}")
                await self._finish("failed", error_reason="bot_not_found")
                return {"status": "failed", "success": False, "error_reason": "bot_not_found"}

            # Register response handler
            self._register_response_handler(bot_entity.id)

            # Send /start
            await self._send_message("/start")

            # Wait for response
            response = await self._wait_for_response()
            if not response:
                await self._finish("failed", error_reason="no_response")
                return {"status": "failed", "success": False, "error_reason": "no_response"}

            # Run conversation loop
            result = await self._conversation_loop()

            return result

        except UserIsBlockedError:
            logger.warning(f"Bot @{self.bot_username} blocked us")
            await self._finish("failed", error_reason="blocked_by_bot")
            return {"status": "failed", "success": False, "error_reason": "blocked_by_bot"}

        except FloodWaitError as e:
            logger.warning(f"Flood wait {e.seconds}s for bot @{self.bot_username}")
            await self._finish("failed", error_reason=f"flood_wait_{e.seconds}s")
            return {"status": "failed", "success": False, "error_reason": f"flood_wait"}

        except Exception as e:
            logger.error(f"Error in bot conversation: {e}", exc_info=True)
            await self._finish("failed", error_reason=str(e)[:200])
            return {"status": "failed", "success": False, "error_reason": str(e)[:200]}

        finally:
            self._unregister_response_handler()

    def _register_response_handler(self, bot_id: int):
        """Register handler for bot responses"""
        @self.client.on(events.NewMessage(from_users=bot_id))
        async def handle_bot_response(event):
            self.last_bot_message = event.message
            self.messages_received += 1
            self._response_event.set()

            # Save message to DB
            if self.interaction_id:
                has_buttons = bool(
                    event.message.reply_markup and
                    isinstance(event.message.reply_markup, (ReplyInlineMarkup, ReplyKeyboardMarkup))
                )
                await db.save_bot_message(
                    self.interaction_id,
                    direction="received",
                    message_text=event.message.text or "",
                    has_buttons=has_buttons
                )

        self._response_handler = handle_bot_response

    def _unregister_response_handler(self):
        """Unregister the response handler"""
        if self._response_handler:
            self.client.remove_event_handler(self._response_handler)
            self._response_handler = None

    async def _wait_for_response(self, timeout: int = RESPONSE_TIMEOUT) -> Optional[Message]:
        """Wait for bot response with timeout"""
        self._response_event.clear()
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            return self.last_bot_message
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for bot @{self.bot_username} response")
            return None

    async def _send_message(self, text: str, file: Optional[str] = None):
        """Send a message to the bot"""
        try:
            if file and Path(file).exists():
                await self.client.send_file(self.bot_username, file, caption=text or None)
                file_name = Path(file).name
            else:
                await self.client.send_message(self.bot_username, text)
                file_name = None

            self.messages_sent += 1

            # Save to DB
            if self.interaction_id:
                await db.save_bot_message(
                    self.interaction_id,
                    direction="sent",
                    message_text=text,
                    file_sent=file_name
                )

            # Small delay to seem human
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Error sending message to bot: {e}")
            raise

    async def _click_button(self, message: Message, button_data: bytes, button_text: str):
        """Click an inline button"""
        try:
            await self.client(GetBotCallbackAnswerRequest(
                peer=self.bot_username,
                msg_id=message.id,
                data=button_data
            ))

            # Save to DB
            if self.interaction_id:
                await db.save_bot_message(
                    self.interaction_id,
                    direction="sent",
                    message_text=f"[Clicked button: {button_text}]",
                    button_clicked=button_text
                )

            self.messages_sent += 1
            await asyncio.sleep(0.5)

        except BotResponseTimeoutError:
            # Button click registered but bot didn't send callback
            pass
        except Exception as e:
            logger.warning(f"Error clicking button: {e}")

    async def _conversation_loop(self) -> Dict[str, Any]:
        """Main conversation loop"""
        while self.messages_sent < MAX_MESSAGES and not self.is_complete:
            # Check timeout
            elapsed = (datetime.now() - self.conversation_started).seconds
            if elapsed > CONVERSATION_TIMEOUT:
                logger.info(f"Conversation timeout with @{self.bot_username}")
                await self._finish("timeout", error_reason="conversation_timeout")
                return {"status": "timeout", "success": False, "error_reason": "timeout"}

            if not self.last_bot_message:
                break

            message = self.last_bot_message
            text = message.text or ""

            # Check for success
            if self._is_success(text):
                logger.info(f"Success! Bot @{self.bot_username} confirmed application")
                await self._finish("success", success_message=text[:500])
                return {"status": "success", "success": True, "message": text[:500]}

            # Check for failure
            if self._is_failure(text):
                logger.info(f"Bot @{self.bot_username} indicated failure: {text[:100]}")
                await self._finish("failed", error_reason=text[:200])
                return {"status": "failed", "success": False, "error_reason": text[:200]}

            # Handle the message
            handled = await self._handle_bot_message(message)

            if not handled:
                # Don't know how to respond, finish
                logger.info(f"Don't know how to respond to bot @{self.bot_username}")
                await self._finish("stuck", error_reason="unknown_question")
                return {"status": "stuck", "success": False, "error_reason": "unknown_question"}

            # Wait for next response
            response = await self._wait_for_response()
            if not response:
                # No more responses, check if we were successful
                if self.messages_sent > 2:
                    # We exchanged messages, maybe it was successful
                    await self._finish("completed", success_message="No confirmation, but messages exchanged")
                    return {"status": "completed", "success": True, "message": "completed_no_confirmation"}
                else:
                    await self._finish("failed", error_reason="no_response")
                    return {"status": "failed", "success": False, "error_reason": "no_response"}

        # Max messages reached
        if self.messages_sent >= MAX_MESSAGES:
            await self._finish("max_messages", error_reason="max_messages_reached")
            return {"status": "max_messages", "success": False, "error_reason": "max_messages"}

        await self._finish("completed")
        return {"status": "completed", "success": True}

    async def _handle_bot_message(self, message: Message) -> bool:
        """
        Handle a bot message - decide what to do next.

        Returns:
            True if handled, False if don't know what to do
        """
        text = (message.text or "").lower()
        reply_markup = message.reply_markup

        # Handle inline buttons
        if isinstance(reply_markup, ReplyInlineMarkup):
            button = self._find_apply_button(reply_markup)
            if button:
                await self._click_button(message, button.data, button.text)
                return True

            # If has buttons but none match "apply", try first button
            if reply_markup.rows and reply_markup.rows[0].buttons:
                first_button = reply_markup.rows[0].buttons[0]
                if hasattr(first_button, 'data') and first_button.data:
                    await self._click_button(message, first_button.data, first_button.text)
                    return True

        # Handle reply keyboard
        if isinstance(reply_markup, ReplyKeyboardMarkup):
            button_text = self._find_apply_keyboard_button(reply_markup)
            if button_text:
                await self._send_message(button_text)
                return True

        # Handle text questions
        question_type = self._detect_question_type(text)
        if question_type:
            answer = self._get_answer(question_type)
            if answer:
                # Special case: resume request
                if question_type == "resume":
                    if self.profile.resume_file_path and Path(self.profile.resume_file_path).exists():
                        await self._send_message("", file=self.profile.resume_file_path)
                    elif self.profile.resume_text:
                        await self._send_message(self.profile.resume_text[:4000])
                    else:
                        await self._send_message(self._generate_mini_resume())
                else:
                    await self._send_message(answer)
                return True

        # If no question detected but we just started, maybe bot wants resume
        if self.messages_received <= 2 and "резюме" not in text and "cv" not in text:
            # Send a brief introduction
            intro = self._generate_intro()
            if intro:
                await self._send_message(intro)
                return True

        return False

    def _find_apply_button(self, markup: ReplyInlineMarkup):
        """Find an 'apply' button in inline markup"""
        for row in markup.rows:
            for button in row.buttons:
                button_text = (button.text or "").lower()
                for pattern in APPLY_BUTTON_PATTERNS:
                    if re.search(pattern, button_text):
                        if hasattr(button, 'data') and button.data:
                            return button
        return None

    def _find_apply_keyboard_button(self, markup: ReplyKeyboardMarkup) -> Optional[str]:
        """Find an 'apply' button in reply keyboard"""
        for row in markup.rows:
            for button in row.buttons:
                button_text = button.text or ""
                for pattern in APPLY_BUTTON_PATTERNS:
                    if re.search(pattern, button_text.lower()):
                        return button_text
        return None

    def _detect_question_type(self, text: str) -> Optional[str]:
        """Detect what the bot is asking for"""
        text_lower = text.lower()
        for q_type, patterns in QUESTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return q_type
        return None

    def _get_answer(self, question_type: str) -> Optional[str]:
        """Get answer from candidate profile"""
        return self.profile.get_answer(question_type)

    def _generate_intro(self) -> str:
        """Generate a brief introduction"""
        parts = []
        if self.profile.name:
            parts.append(f"Здравствуйте! Меня зовут {self.profile.name}.")
        if self.profile.position:
            parts.append(f"Интересует позиция: {self.profile.position}.")
        if self.profile.experience_years:
            parts.append(f"Опыт: {self.profile.experience_years} лет.")
        if not parts:
            parts.append("Здравствуйте! Хочу откликнуться на вакансию.")
        return " ".join(parts)

    def _generate_mini_resume(self) -> str:
        """Generate a mini resume from profile data"""
        parts = []
        if self.profile.name:
            parts.append(f"Имя: {self.profile.name}")
        if self.profile.position:
            parts.append(f"Позиция: {self.profile.position}")
        if self.profile.experience_years:
            parts.append(f"Опыт: {self.profile.experience_years} лет")
        if self.profile.skills:
            parts.append(f"Навыки: {self.profile.skills}")
        if self.profile.phone:
            parts.append(f"Телефон: {self.profile.phone}")
        if self.profile.email:
            parts.append(f"Email: {self.profile.email}")
        if self.profile.about:
            parts.append(f"\n{self.profile.about}")
        return "\n".join(parts) if parts else "Резюме не заполнено"

    def _is_success(self, text: str) -> bool:
        """Check if bot indicated success"""
        text_lower = text.lower()
        return any(indicator in text_lower for indicator in SUCCESS_INDICATORS)

    def _is_failure(self, text: str) -> bool:
        """Check if bot indicated failure"""
        text_lower = text.lower()
        return any(indicator in text_lower for indicator in FAILURE_INDICATORS)

    async def _finish(
        self,
        status: str,
        error_reason: Optional[str] = None,
        success_message: Optional[str] = None
    ):
        """Finish the conversation and update DB"""
        self.is_complete = True
        self.status = status

        if self.interaction_id:
            await db.update_bot_interaction(
                self.interaction_id,
                status=status,
                error_reason=error_reason,
                success_message=success_message,
                messages_sent=self.messages_sent,
                messages_received=self.messages_received
            )

        logger.info(
            f"Bot conversation @{self.bot_username} finished: "
            f"status={status}, sent={self.messages_sent}, received={self.messages_received}"
        )


def extract_bot_username(text: str) -> Optional[str]:
    """Extract bot username from text (t.me/botname or @botname_bot)"""
    # Pattern for t.me links
    tme_match = re.search(r't\.me/([a-zA-Z][a-zA-Z0-9_]{4,}(?:bot|Bot|BOT)?)', text)
    if tme_match:
        username = tme_match.group(1)
        # Check if it looks like a bot (ends with bot or has bot in name)
        if 'bot' in username.lower():
            return username

    # Pattern for @username that looks like a bot
    at_match = re.search(r'@([a-zA-Z][a-zA-Z0-9_]*(?:bot|Bot|BOT))', text)
    if at_match:
        return at_match.group(1)

    return None
