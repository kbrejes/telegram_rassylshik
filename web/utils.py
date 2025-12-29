"""Утилиты и общие функции для веб-интерфейса"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
TEMPLATES_FILE = BASE_DIR.parent / "configs" / "templates.json"
SOURCE_LISTS_FILE = BASE_DIR.parent / "configs" / "source_lists.json"
FILTER_PROMPT_FILE = BASE_DIR.parent / "configs" / "filter_prompt.json"


# ============== Telegram Client ==============

async def create_new_bot_client() -> "TelegramClient":
    """
    Создаёт клиент бота для веб-запросов.
    Читает auth_key из SQLite и создаёт StringSession.

    Returns:
        TelegramClient: новый подключенный клиент
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.crypto import AuthKey
    from src.config import config
    from src.session_config import get_bot_session_path
    import sqlite3

    session_path = get_bot_session_path()
    session_file = f"{session_path}.session"

    try:
        # Читаем данные сессии напрямую из SQLite
        conn = sqlite3.connect(session_file, timeout=5.0)
        cursor = conn.cursor()
        cursor.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise ValueError("Сессия бота пустая или повреждена")

        dc_id, server_address, port, auth_key_data = row

        if not auth_key_data:
            raise ValueError("Сессия бота не авторизована")

        # Создаём StringSession и устанавливаем данные
        string_session = StringSession()
        string_session.set_dc(dc_id, server_address, port)
        string_session._auth_key = AuthKey(auth_key_data)

        client = TelegramClient(string_session, config.API_ID, config.API_HASH)
        await client.connect()
        return client

    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            raise Exception("Сессия бота заблокирована. Попробуйте позже.")
        raise
    except Exception as e:
        logger.error(f"Ошибка создания клиента бота: {e}")
        raise


async def get_or_create_bot_client() -> Tuple["TelegramClient", bool]:
    """
    Создаёт новый клиент бота для веб-запросов.
    Использует StringSession чтобы избежать database locked и event loop конфликтов.

    Returns:
        tuple: (client, should_disconnect) - клиент и флаг нужно ли отключать после использования
    """
    # Всегда создаём новый клиент с StringSession (не шарим между event loops)
    client = await create_new_bot_client()
    return client, True


async def get_agent_client(session_name: str) -> Tuple["TelegramClient", bool]:
    """
    Создаёт клиент для агента используя StringSession.

    ВАЖНО: Используем StringSession чтобы не блокировать SQLite файл.
    Бот может держать SQLite сессию открытой, поэтому web должен
    использовать StringSession (см. CLAUDE.md).

    Args:
        session_name: Имя сессии агента (без расширения .session)

    Returns:
        tuple: (client, should_disconnect)
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession, SQLiteSession
    from telethon.crypto import AuthKey
    from src.config import config
    from src.session_config import get_agent_session_path
    import sqlite3
    import struct

    # Получаем путь к SQLite сессии
    session_path = get_agent_session_path(session_name)
    session_file = f"{session_path}.session"

    # Читаем auth_key из SQLite и создаём StringSession
    # Это кратковременное чтение, не держим файл открытым
    try:
        conn = sqlite3.connect(session_file, timeout=5.0)
        cursor = conn.cursor()

        # Читаем данные сессии
        cursor.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise ValueError(f"Сессия {session_name} пустая или повреждена")

        dc_id, server_address, port, auth_key_data = row

        if not auth_key_data:
            raise ValueError(f"Сессия {session_name} не авторизована")

        # Создаём StringSession и устанавливаем данные
        string_session = StringSession()
        string_session.set_dc(dc_id, server_address, port)
        # Создаём AuthKey из бинарных данных
        string_session._auth_key = AuthKey(auth_key_data)

        client = TelegramClient(string_session, config.API_ID, config.API_HASH)
        await client.connect()
        return client, True

    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            raise Exception(f"Сессия {session_name} заблокирована. Попробуйте позже.")
        raise
    except Exception as e:
        raise Exception(f"Не удалось загрузить сессию {session_name}: {e}")


# ============== Templates & Source Lists ==============

def load_templates() -> List[Dict[str, str]]:
    """Load saved auto-response templates"""
    if TEMPLATES_FILE.exists():
        try:
            with open(TEMPLATES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return [
        {"id": "default", "name": "Стандартный отклик", "text": "Здравствуйте! Меня заинтересовала ваша вакансия. Буду рад обсудить детали!"},
        {"id": "detailed", "name": "Подробный отклик", "text": "Здравствуйте!\n\nМеня заинтересовала ваша вакансия. Имею релевантный опыт и готов обсудить условия сотрудничества.\n\nБуду рад ответить на ваши вопросы!"}
    ]


def save_templates(templates: List[Dict[str, str]]) -> None:
    """Save templates to file"""
    TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TEMPLATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


def load_source_lists() -> List[Dict[str, Any]]:
    """Load saved source channel lists"""
    if SOURCE_LISTS_FILE.exists():
        try:
            with open(SOURCE_LISTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_source_lists(lists: List[Dict[str, Any]]) -> None:
    """Save source lists to file"""
    SOURCE_LISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SOURCE_LISTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(lists, f, ensure_ascii=False, indent=2)


def load_filter_prompt() -> Optional[str]:
    """Load custom filter prompt, returns None if not set (use default)"""
    if FILTER_PROMPT_FILE.exists():
        try:
            with open(FILTER_PROMPT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("custom_prompt")
        except Exception:
            pass
    return None


def save_filter_prompt(prompt: str) -> None:
    """Save custom filter prompt"""
    FILTER_PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FILTER_PROMPT_FILE, 'w', encoding='utf-8') as f:
        json.dump({"custom_prompt": prompt}, f, ensure_ascii=False, indent=2)


def reset_filter_prompt() -> None:
    """Delete custom prompt file to reset to default"""
    if FILTER_PROMPT_FILE.exists():
        FILTER_PROMPT_FILE.unlink()


def get_available_agents() -> List[Dict[str, str]]:
    """Get list of all authorized agent sessions with user info from status"""
    from src.session_config import SESSIONS_DIR
    from src.connection_status import status_manager

    # Get status data for user info
    status = status_manager.get_all_status()
    agents_status = status.get("agents", {})

    agents = []
    if SESSIONS_DIR.exists():
        for session_file in SESSIONS_DIR.glob("agent_*.session"):
            session_name = session_file.stem

            # Get user info from status if available
            agent_data = agents_status.get(session_name, {})
            user_info = agent_data.get("user_info") or {}

            # Build display name from user_info
            if user_info:
                first_name = user_info.get("first_name", "")
                last_name = user_info.get("last_name", "")
                full_name = f"{first_name} {last_name}".strip()
                display_name = full_name if full_name else session_name
                phone = user_info.get("phone", "")
                username = user_info.get("username")
            else:
                display_name = session_name.replace("agent_", "Агент ")
                phone = ""
                username = None

            agents.append({
                "session_name": session_name,
                "phone": phone,
                "name": display_name,
                "username": username,
                "status": agent_data.get("status", "disconnected")
            })
    return agents


# ============== Pydantic Models ==============

class AgentRequest(BaseModel):
    phone: str
    session_name: str


class PromptsRequest(BaseModel):
    base_context: str = ""
    discovery: str = ""
    engagement: str = ""
    call_ready: str = ""
    call_pending: str = ""
    call_declined: str = ""


class ChannelCreateRequest(BaseModel):
    name: str
    telegram_id: int
    input_sources: List[str]
    include_keywords: List[str]
    exclude_keywords: List[str] = []
    enabled: bool = True
    crm_enabled: bool = False
    crm_group_id: int = 0
    agents: List[AgentRequest] = []
    auto_response_enabled: bool = False
    auto_response_template: str = ""
    instant_response: bool = False
    prompts: Optional[PromptsRequest] = None


class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    telegram_id: Optional[int] = None
    input_sources: Optional[List[str]] = None
    include_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    enabled: Optional[bool] = None
    crm_enabled: Optional[bool] = None
    crm_group_id: Optional[int] = None
    agents: Optional[List[AgentRequest]] = None
    auto_response_enabled: Optional[bool] = None
    auto_response_template: Optional[str] = None
    instant_response: Optional[bool] = None
    ai_conversation_enabled: Optional[bool] = None
    prompts: Optional[PromptsRequest] = None


class BotAuthInitRequest(BaseModel):
    phone: str


class BotAuthVerifyCodeRequest(BaseModel):
    code: str


class BotAuthVerifyPasswordRequest(BaseModel):
    password: str


class AgentAuthInitRequest(BaseModel):
    phone: str
    session_name: str


class AgentAuthVerifyRequest(BaseModel):
    code: str
    session_name: str


class AgentAuthPasswordRequest(BaseModel):
    password: str
    session_name: str


class SaveTemplateRequest(BaseModel):
    name: str
    text: str


class SaveSourceListRequest(BaseModel):
    name: str
    sources: List[str]


class CreateChannelFullRequest(BaseModel):
    """Request для создания полного канала с CRM"""
    name: str
    input_sources: List[str]
    include_keywords: List[str] = []
    exclude_keywords: List[str] = []
    agents: List[str] = []  # session names
    auto_response_template: str = ""
