"""HTML страницы"""
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
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
    """Main page with channels list"""
    import json

    channels = config_manager.load()

    # Load source_channels mapping to get titles for input sources
    source_titles_map = {}
    connection_status_path = Path(__file__).parent.parent.parent / "configs" / "connection_status.json"
    if connection_status_path.exists():
        try:
            with open(connection_status_path) as f:
                conn_status = json.load(f)
            for username, info in conn_status.get("source_channels", {}).items():
                if info and info.get("title"):
                    # Store both with and without @ for matching
                    source_titles_map[username.lower()] = info["title"]
                    source_titles_map[username.lower().lstrip("@")] = info["title"]
        except Exception:
            pass

    # Add source_titles to each channel for frontend filtering
    channels_with_titles = []
    for ch in channels:
        ch_dict = ch.to_dict()
        source_titles = []
        for src in ch_dict.get("input_sources", []):
            src_lower = src.lower().lstrip("@")
            if src_lower in source_titles_map:
                source_titles.append(source_titles_map[src_lower])
            elif f"@{src_lower}" in source_titles_map:
                source_titles.append(source_titles_map[f"@{src_lower}"])
        ch_dict["source_titles"] = source_titles
        channels_with_titles.append(ch_dict)

    return templates.TemplateResponse(
        "channels_list_new.html",
        {"request": request, "channels": channels_with_titles}
    )


@router.get("/channel/new")
async def new_channel_page(request: Request):
    """Create new channel page"""
    return templates.TemplateResponse(
        "channel_create_new.html",
        {"request": request}
    )


@router.get("/channel/{channel_id}")
async def edit_channel_page(request: Request, channel_id: str):
    """Edit channel page"""
    config_manager.load()
    channel = config_manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(404, "Channel not found")
    return templates.TemplateResponse(
        "channel_edit_new.html",
        {
            "request": request,
            "channel": channel,
            "is_new": False
        }
    )


@router.get("/auth")
async def auth_page(request: Request):
    """Bot authorization page"""
    return templates.TemplateResponse(
        "bot_auth_new.html",
        {"request": request}
    )


@router.get("/agents")
async def agents_page(request: Request):
    """Agents management dashboard"""
    return templates.TemplateResponse(
        "status_new.html",
        {"request": request}
    )


# Redirect old /status URL to /agents
@router.get("/status")
async def status_redirect():
    """Redirect /status to /agents"""
    return RedirectResponse(url="/agents", status_code=301)


@router.get("/ai-stats")
async def ai_stats_page(request: Request):
    """AI self-correction stats dashboard"""
    return templates.TemplateResponse(
        "ai_stats_new.html",
        {"request": request}
    )


@router.get("/vacancy-log")
async def vacancy_log_page(request: Request):
    """Vacancy log with AI analysis outcomes"""
    return templates.TemplateResponse(
        "vacancy_log_new.html",
        {"request": request}
    )


@router.get("/candidate")
async def candidate_page(request: Request):
    """Candidate profile management"""
    return templates.TemplateResponse(
        "candidate_profile_new.html",
        {"request": request}
    )


@router.get("/vacancy/{vacancy_id}")
async def vacancy_detail_page(request: Request, vacancy_id: int):
    """Vacancy detail full page"""
    return templates.TemplateResponse(
        "vacancy_detail_new.html",
        {"request": request, "vacancy_id": vacancy_id}
    )
