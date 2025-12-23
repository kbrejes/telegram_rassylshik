"""HTML страницы"""
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.config_manager import ConfigManager

router = APIRouter()

BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
config_manager = ConfigManager()


@router.get("/")
async def index(request: Request):
    """Главная страница со списком каналов"""
    channels = config_manager.load()
    return templates.TemplateResponse(
        "channels_list.html",
        {"request": request, "channels": channels}
    )


@router.get("/channel/new")
async def new_channel_page(request: Request):
    """Страница создания нового канала"""
    return templates.TemplateResponse(
        "channel_create.html",
        {"request": request}
    )


@router.get("/channel/{channel_id}")
async def edit_channel_page(request: Request, channel_id: str):
    """Страница редактирования канала"""
    from fastapi import HTTPException
    config_manager.load()
    channel = config_manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(404, "Канал не найден")
    return templates.TemplateResponse(
        "channel_edit.html",
        {
            "request": request,
            "channel": channel,
            "is_new": False
        }
    )


@router.get("/auth")
async def auth_page(request: Request):
    """Страница авторизации бота"""
    return templates.TemplateResponse(
        "bot_auth.html",
        {"request": request}
    )
