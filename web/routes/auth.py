"""API для аутентификации основного бота"""
import time
import base64
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from auth import bot_auth_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bot", tags=["bot-auth"])

# Глобальное состояние бота (устанавливается из main_multi.py)
bot_state = {
    "status": "unknown",
    "error": None,
    "user_info": None,
    "flood_wait_until": None
}


class BotAuthInitRequest(BaseModel):
    """Запрос на инициализацию аутентификации бота"""
    phone: str


class BotAuthVerifyCodeRequest(BaseModel):
    """Запрос на проверку кода аутентификации"""
    code: str


class BotAuthVerifyPasswordRequest(BaseModel):
    """Запрос на проверку 2FA пароля"""
    password: str


@router.get("/status")
async def get_bot_status():
    """Получить статус бота"""
    result = {
        "status": bot_state.get("status", "unknown"),
        "error": bot_state.get("error"),
        "user_info": bot_state.get("user_info")
    }

    # Если FloodWait - показываем оставшееся время
    flood_until = bot_state.get("flood_wait_until")
    if flood_until:
        remaining = int(flood_until - time.time())
        if remaining > 0:
            result["flood_wait_remaining"] = remaining
            result["flood_wait_remaining_human"] = f"{remaining // 3600}ч {(remaining % 3600) // 60}м"
        else:
            result["flood_wait_remaining"] = 0

    return result


@router.post("/upload-session")
async def upload_session(request: Request):
    """Загрузить файл сессии (base64)"""
    try:
        data = await request.json()
        session_b64 = data.get("session_base64")

        if not session_b64:
            raise HTTPException(400, "session_base64 is required")

        session_data = base64.b64decode(session_b64)

        # Сохраняем сессию
        session_path = Path("bot_session.session")
        session_path.write_bytes(session_data)

        logger.info("Сессия загружена через API")

        return {"success": True, "message": "Сессия загружена. Бот перезапустится автоматически."}

    except Exception as e:
        logger.error(f"Ошибка загрузки сессии: {e}")
        raise HTTPException(500, str(e))


@router.post("/auth/init")
async def init_bot_auth(request: BotAuthInitRequest):
    """Инициирует аутентификацию основного бота"""
    try:
        logger.info(f"Инициализация аутентификации бота для {request.phone}")
        result = await bot_auth_manager.init_auth(request.phone)
        return result
    except Exception as e:
        logger.error(f"Ошибка инициализации аутентификации бота: {e}")
        raise HTTPException(500, str(e))


@router.post("/auth/verify-code")
async def verify_bot_code(request: BotAuthVerifyCodeRequest):
    """Проверяет код подтверждения"""
    try:
        logger.info("Проверка кода для основного бота")
        result = await bot_auth_manager.verify_code(request.code)

        # Если успешно - обновляем bot_state
        if result.get("authenticated"):
            bot_state["status"] = "authenticated"
            bot_state["error"] = None

        return result
    except Exception as e:
        logger.error(f"Ошибка проверки кода бота: {e}")
        raise HTTPException(500, str(e))


@router.post("/auth/verify-password")
async def verify_bot_password(request: BotAuthVerifyPasswordRequest):
    """Проверяет 2FA пароль"""
    try:
        logger.info("Проверка 2FA пароля для основного бота")
        result = await bot_auth_manager.verify_password(request.password)

        # Если успешно - обновляем bot_state
        if result.get("authenticated"):
            bot_state["status"] = "authenticated"
            bot_state["error"] = None

        return result
    except Exception as e:
        logger.error(f"Ошибка проверки 2FA пароля бота: {e}")
        raise HTTPException(500, str(e))


@router.get("/auth/status")
async def check_bot_auth_status():
    """Проверяет статус аутентификации основного бота"""
    try:
        result = await bot_auth_manager.check_session_status(quick_check=True)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки статуса бота: {e}")
        raise HTTPException(500, str(e))


@router.delete("/auth/pending")
async def cleanup_bot_pending_auth():
    """Очищает незавершенную аутентификацию бота"""
    try:
        await bot_auth_manager.cleanup()
        return {"success": True, "message": "Pending auth cleaned up"}
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        raise HTTPException(500, str(e))
