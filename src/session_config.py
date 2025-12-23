"""
Централизованная конфигурация путей сессий.

ВСЕ сессии должны использовать пути из этого модуля.
Это предотвращает проблемы с:
- Относительными путями (разные CWD = разные сессии)
- Дублированием сессий (Docker vs Host)
- AuthKeyDuplicatedError (копирование сессий)
"""
import os
from pathlib import Path

# Определяем базовую директорию проекта (родитель директории src/)
PROJECT_ROOT = Path(__file__).parent.parent.absolute()

# Директория для всех сессий (абсолютный путь!)
SESSIONS_DIR = PROJECT_ROOT / "sessions"

# Создаем директорию если не существует
SESSIONS_DIR.mkdir(exist_ok=True)


def get_bot_session_path() -> str:
    """
    Возвращает абсолютный путь к сессии основного бота.
    БЕЗ расширения .session (Telethon добавит сам).
    """
    return str(SESSIONS_DIR / "bot_session")


def get_agent_session_path(session_name: str) -> str:
    """
    Возвращает абсолютный путь к сессии агента.
    БЕЗ расширения .session (Telethon добавит сам).

    Args:
        session_name: Имя сессии агента (например "agent_1234")
    """
    if not session_name:
        raise ValueError("session_name is required")
    return str(SESSIONS_DIR / session_name)


def get_all_session_files() -> list:
    """Возвращает список всех файлов сессий"""
    return list(SESSIONS_DIR.glob("*.session"))


def delete_all_sessions() -> int:
    """
    Удаляет все сессии. Возвращает количество удаленных файлов.
    ВНИМАНИЕ: Используйте только когда бот остановлен!
    """
    deleted = 0
    for ext in ["*.session", "*.session-journal"]:
        for f in SESSIONS_DIR.glob(ext):
            try:
                f.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted


def delete_session(session_name: str) -> bool:
    """
    Удаляет конкретную сессию.

    Args:
        session_name: Имя сессии (без расширения)
    """
    deleted = False
    for ext in [".session", ".session-journal"]:
        path = SESSIONS_DIR / f"{session_name}{ext}"
        if path.exists():
            try:
                path.unlink()
                deleted = True
            except Exception:
                pass
    return deleted


def delete_session_file(session_path: str) -> bool:
    """
    Удаляет сессию по полному пути.

    Args:
        session_path: Полный путь к сессии (без или с расширением)
    """
    # Убираем расширение если есть
    path = Path(session_path)
    if path.suffix in [".session", ".session-journal"]:
        path = path.with_suffix("")

    deleted = False
    for ext in [".session", ".session-journal"]:
        file_path = Path(str(path) + ext)
        if file_path.exists():
            try:
                file_path.unlink()
                deleted = True
            except Exception:
                pass
    return deleted


# Для обратной совместимости
BOT_SESSION_PATH = get_bot_session_path()
