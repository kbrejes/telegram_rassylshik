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


# ============== Telegram Client ==============

async def create_new_bot_client() -> "TelegramClient":
    """
    Создаёт клиент бота для веб-запросов.
    Использует StringSession чтобы избежать database is locked.
    Читает auth_key из SQLite файла и создаёт in-memory сессию.

    Returns:
        TelegramClient: новый подключенный клиент
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from config import config
    from session_config import get_bot_session_path
    import sqlite3

    bot_session_path = get_bot_session_path()
    session_file = f"{bot_session_path}.session"

    # Читаем auth_key из SQLite файла (только чтение, без блокировки)
    try:
        conn = sqlite3.connect(f"file:{session_file}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
        row = cursor.fetchone()
        conn.close()

        if row:
            dc_id, server_address, port, auth_key = row
            # Создаём StringSession с auth_key
            string_session = StringSession()
            string_session.set_dc(dc_id, server_address, port)
            string_session.auth_key = type('AuthKey', (), {'key': auth_key})()
            client = TelegramClient(string_session, config.API_ID, config.API_HASH)
        else:
            # Fallback: пустая сессия
            client = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
    except Exception as e:
        logger.warning(f"Не удалось прочитать сессию: {e}, создаём новую")
        client = TelegramClient(StringSession(), config.API_ID, config.API_HASH)

    await client.connect()
    return client


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
    Создаёт клиент для агента.

    Args:
        session_name: Имя сессии агента (без расширения .session)

    Returns:
        tuple: (client, should_disconnect)
    """
    from auth.base import TimeoutSQLiteSession
    from telethon import TelegramClient
    from config import config
    from session_config import get_agent_session_path

    # Используем абсолютный путь к сессии агента
    session_path = get_agent_session_path(session_name)
    session = TimeoutSQLiteSession(session_path)
    client = TelegramClient(session, config.API_ID, config.API_HASH)
    await client.connect()
    return client, True


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


def get_available_agents() -> List[Dict[str, str]]:
    """Get list of all authorized agent sessions"""
    from session_config import SESSIONS_DIR

    agents = []
    if SESSIONS_DIR.exists():
        for session_file in SESSIONS_DIR.glob("agent_*.session"):
            session_name = session_file.stem
            agents.append({
                "session_name": session_name,
                "phone": "",
                "name": session_name.replace("agent_", "Агент ")
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
