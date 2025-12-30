"""
Project Constants

Centralized storage for constants to avoid duplication and magic numbers.
"""

# =============================================================================
# TIMEOUTS & INTERVALS (seconds)
# =============================================================================

# Spam/Flood handling
SPAM_BLOCK_DURATION_SECONDS = 3600  # 1 hour - default block after PeerFlood
FLOOD_WAIT_MAX_SECONDS = 30  # Max seconds to wait for FloodWait retry

# Polling/Check intervals
COMMAND_CHECK_INTERVAL_SECONDS = 30  # How often to check command queue
HEALTH_CHECK_INTERVAL_SECONDS = 300  # Agent health check (5 minutes)
MESSAGE_QUEUE_RETRY_INTERVAL_SECONDS = 60  # Queue retry check (1 minute)

# Connection/Session timeouts
SESSION_LOCK_TIMEOUT_SECONDS = 30  # SQLite session lock timeout
SQLITE_CONNECT_TIMEOUT_SECONDS = 5.0  # Web utils SQLite timeout

# Human behavior delays
INSTANT_DELAY_MAX_SECONDS = 10  # Max delay for "instant" responses
NORMAL_DELAY_MAX_SECONDS = 45  # Max delay for normal responses
LONG_DELAY_MAX_SECONDS = 1200  # Max delay (20 min) for long responses


# =============================================================================
# RETRY LIMITS
# =============================================================================

DEFAULT_MAX_RETRIES = 3  # Default retry count for operations
MESSAGE_QUEUE_MAX_RETRIES = 5  # Retries for queued messages
TOPIC_CREATION_MAX_RETRIES = 3  # Retries for creating forum topics


# =============================================================================
# MESSAGE/DATA LIMITS
# =============================================================================

MESSAGE_SYNC_LIMIT = 20  # Messages to sync per contact on startup
DIALOG_REFRESH_LIMIT = 100  # Dialogs to refresh for entity cache
DEFAULT_QUERY_LIMIT = 50  # Default DB query limit
LARGE_QUERY_LIMIT = 100  # Larger DB query limit


# =============================================================================
# AGE LIMITS (hours)
# =============================================================================

MAX_MESSAGE_AGE_HOURS = 24  # Drop messages older than this
COMMAND_CLEANUP_AGE_HOURS = 1  # Remove old commands after this
MESSAGE_SYNC_LOOKBACK_HOURS = 2  # Look back for missed messages on startup


# =============================================================================
# LLM DEFAULTS
# =============================================================================

LLM_MAX_TOKENS = 512  # Default max tokens for LLM responses
CONTEXT_WINDOW_MESSAGES = 24  # Messages to include in context


# =============================================================================
# SERVICE MESSAGE PREFIXES
# =============================================================================

# Prefixes for service messages in CRM topics
# Used to filter messages from bot/AI/agents
SERVICE_MESSAGE_PREFIXES = (
    "🤖 **Агент (",
    "🤖 **Агент:**",
    "🤖 **AI:",
    "📌 **Новый контакт:",
    "📋 **Вакансия из",
    "👤 **",
)


def is_service_message(text: str) -> bool:
    """
    Check if a message is a service message.

    Args:
        text: Message text

    Returns:
        True if message is a service message (from bot/AI/agents)
    """
    if not text:
        return False
    return any(text.startswith(prefix) for prefix in SERVICE_MESSAGE_PREFIXES)
