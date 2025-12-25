"""API для управления агентами"""
import re
import uuid
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from auth import agent_auth_manager
from src.connection_status import status_manager
from web.utils import (
    AgentAuthInitRequest, AgentAuthVerifyRequest, AgentAuthPasswordRequest,
    SaveTemplateRequest, SaveSourceListRequest,
    get_available_agents, load_templates, save_templates,
    load_source_lists, save_source_lists
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def get_agents_list():
    """Получить список всех доступных агентов"""
    try:
        agents = get_available_agents()
        return {"success": True, "agents": agents}
    except Exception as e:
        logger.error(f"Ошибка получения списка агентов: {e}")
        return {"success": False, "message": str(e), "agents": []}


@router.post("/init")
async def init_agent_auth(request: AgentAuthInitRequest):
    """Инициирует аутентификацию агента"""
    try:
        logger.info(f"Инициализация аутентификации для {request.phone}")
        result = await agent_auth_manager.init_auth(request.phone, request.session_name)
        return result
    except Exception as e:
        logger.error(f"Ошибка инициализации аутентификации: {e}")
        raise HTTPException(500, str(e))


@router.post("/verify")
async def verify_agent_code(request: AgentAuthVerifyRequest):
    """Проверяет код подтверждения"""
    try:
        logger.info(f"Проверка кода для сессии {request.session_name}")
        result = await agent_auth_manager.verify_code(request.session_name, request.code)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки кода: {e}")
        raise HTTPException(500, str(e))


@router.post("/verify-password")
async def verify_agent_password(request: AgentAuthPasswordRequest):
    """Проверяет 2FA пароль"""
    try:
        logger.info(f"Проверка 2FA пароля для сессии {request.session_name}")
        result = await agent_auth_manager.verify_password(request.session_name, request.password)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки 2FA пароля: {e}")
        raise HTTPException(500, str(e))


@router.get("/{session_name}/status")
async def check_agent_status(session_name: str):
    """Проверяет статус сессии агента"""
    try:
        result = await agent_auth_manager.check_session_status(session_name)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки статуса агента: {e}")
        raise HTTPException(500, str(e))


@router.delete("/{session_name}/pending")
async def cleanup_pending_auth(session_name: str):
    """Очищает незавершенную аутентификацию"""
    try:
        await agent_auth_manager.cleanup_pending(session_name)
        return {"success": True, "message": "Pending auth cleaned up"}
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        raise HTTPException(500, str(e))


@router.post("/{session_name}/reset")
async def reset_agent_session(session_name: str):
    """
    Принудительно сбрасывает сессию агента.
    Используйте когда авторизация застряла или сессия недействительна.
    """
    try:
        logger.info(f"Принудительный сброс сессии агента: {session_name}")
        result = await agent_auth_manager.force_reset_session(session_name)
        return result
    except Exception as e:
        logger.error(f"Ошибка сброса сессии агента {session_name}: {e}")
        raise HTTPException(500, str(e))


# ==================== Alias endpoints for frontend compatibility ====================

class AgentAuthStartRequest(BaseModel):
    """Запрос на начало авторизации агента"""
    phone: str


@router.post("/auth/start")
async def agent_auth_start(request: AgentAuthStartRequest):
    """Начать авторизацию агента (генерирует session_name автоматически)"""
    try:
        phone_digits = re.sub(r'\D', '', request.phone)
        session_name = f"agent_{phone_digits[-4:]}"

        logger.info(f"Начало авторизации агента: phone={request.phone}, session={session_name}")
        result = await agent_auth_manager.init_auth(request.phone, session_name)
        logger.info(f"Init auth result for {session_name}: {result}")

        # If already authenticated, save status and return success immediately
        if result.get("already_authenticated"):
            user_info = result.get("user_info", {})
            status_manager.update_agent_status(
                session_name,
                status="disconnected",
                phone=user_info.get("phone", ""),
                user_info=_transform_user_info(user_info)
            )
            return {
                "success": True,
                "already_authenticated": True,
                "session_name": session_name,
                "name": user_info.get("name", "Agent")
            }

        if result.get("needs_code"):
            return {"success": True, "session_name": session_name, "message": "Код отправлен"}

        return result
    except Exception as e:
        logger.error(f"Ошибка инициализации авторизации агента: {e}")
        return {"success": False, "message": str(e)}


def _transform_user_info(auth_user_info: dict) -> dict:
    """Transform auth user_info format to status user_info format."""
    if not auth_user_info:
        return None

    # Auth returns: {name, username, id, phone?}
    # Status expects: {id, first_name, last_name, username, phone?}
    name = auth_user_info.get("name", "")
    name_parts = name.split(" ", 1)

    return {
        "id": auth_user_info.get("id"),
        "first_name": name_parts[0] if name_parts else "",
        "last_name": name_parts[1] if len(name_parts) > 1 else "",
        "username": auth_user_info.get("username"),
        "phone": auth_user_info.get("phone")
    }


@router.post("/auth/verify")
async def agent_auth_verify(request: AgentAuthVerifyRequest):
    """Проверить код авторизации агента"""
    try:
        logger.info(f"Проверка кода для агента: session={request.session_name}")
        result = await agent_auth_manager.verify_code(request.session_name, request.code)
        logger.info(f"Auth result for {request.session_name}: authenticated={result.get('authenticated')}, needs_password={result.get('needs_password')}, user_info={result.get('user_info')}")

        if result.get("authenticated"):
            user_info = result.get("user_info", {})
            # Save user_info to connection status (transformed format)
            status_manager.update_agent_status(
                request.session_name,
                status="disconnected",
                user_info=_transform_user_info(user_info)
            )
            return {"success": True, "name": user_info.get("name", "Агент")}
        elif result.get("needs_password"):
            return {"success": False, "requires_2fa": True, "message": "Требуется 2FA пароль"}
        return {"success": False, "message": result.get("message", "Неверный код")}
    except Exception as e:
        logger.error(f"Ошибка проверки кода: {e}")
        return {"success": False, "message": str(e)}


@router.post("/auth/2fa")
async def agent_auth_2fa(request: AgentAuthPasswordRequest):
    """Проверить 2FA пароль агента"""
    try:
        logger.info(f"Проверка 2FA для агента: session={request.session_name}")
        result = await agent_auth_manager.verify_password(request.session_name, request.password)
        logger.info(f"2FA result for {request.session_name}: authenticated={result.get('authenticated')}, user_info={result.get('user_info')}")

        if result.get("authenticated"):
            user_info = result.get("user_info", {})
            # Save user_info to connection status (transformed format)
            status_manager.update_agent_status(
                request.session_name,
                status="disconnected",
                user_info=_transform_user_info(user_info)
            )
            return {"success": True, "name": user_info.get("name", "Агент")}
        return {"success": False, "message": result.get("message", "Неверный пароль")}
    except Exception as e:
        logger.error(f"Ошибка проверки 2FA: {e}")
        return {"success": False, "message": str(e)}


# ==================== Templates API ====================

templates_router = APIRouter(prefix="/api/templates", tags=["templates"])


@templates_router.get("")
async def get_templates():
    """Получить список сохранённых шаблонов автоответов"""
    try:
        templates = load_templates()
        return {"success": True, "templates": templates}
    except Exception as e:
        logger.error(f"Ошибка загрузки шаблонов: {e}")
        return {"success": False, "message": str(e), "templates": []}


@templates_router.post("")
async def save_template_endpoint(request: SaveTemplateRequest):
    """Сохранить новый шаблон автоответа"""
    try:
        templates = load_templates()
        new_template = {
            "id": f"template_{uuid.uuid4().hex[:8]}",
            "name": request.name,
            "text": request.text
        }
        templates.append(new_template)
        save_templates(templates)
        return {"success": True, "template": new_template}
    except Exception as e:
        logger.error(f"Ошибка сохранения шаблона: {e}")
        return {"success": False, "message": str(e)}


@templates_router.delete("/{template_id}")
async def delete_template_endpoint(template_id: str):
    """Удалить шаблон автоответа"""
    try:
        templates = load_templates()
        templates = [t for t in templates if t.get("id") != template_id]
        save_templates(templates)
        return {"success": True}
    except Exception as e:
        logger.error(f"Ошибка удаления шаблона: {e}")
        return {"success": False, "message": str(e)}


# ==================== Source Lists API ====================

source_lists_router = APIRouter(prefix="/api/source-lists", tags=["source-lists"])


@source_lists_router.get("")
async def get_source_lists_endpoint():
    """Получить сохранённые списки каналов-источников"""
    try:
        lists = load_source_lists()
        return {"success": True, "lists": lists}
    except Exception as e:
        logger.error(f"Ошибка загрузки списков источников: {e}")
        return {"success": False, "message": str(e), "lists": []}


@source_lists_router.post("")
async def save_source_list_endpoint(request: SaveSourceListRequest):
    """Сохранить новый список каналов-источников"""
    try:
        lists = load_source_lists()
        new_list = {
            "id": f"list_{uuid.uuid4().hex[:8]}",
            "name": request.name,
            "sources": request.sources
        }
        lists.append(new_list)
        save_source_lists(lists)
        return {"success": True, "list": new_list}
    except Exception as e:
        logger.error(f"Ошибка сохранения списка источников: {e}")
        return {"success": False, "message": str(e)}
