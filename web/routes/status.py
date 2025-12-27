"""
API endpoints for connection status and control.
"""
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.connection_status import status_manager
from src.command_queue import command_queue
from src.session_config import SESSIONS_DIR

router = APIRouter(prefix="/api/status", tags=["status"])


class CommandResponse(BaseModel):
    success: bool
    command_id: Optional[str] = None
    message: str


class StatusResponse(BaseModel):
    success: bool
    status: dict
    last_updated: str


# ============== Status Endpoints ==============

@router.get("")
async def get_all_status():
    """Get complete status of all connections."""
    status = status_manager.get_all_status()
    return StatusResponse(
        success=True,
        status=status,
        last_updated=status.get("last_updated", "")
    )


@router.get("/agents")
async def get_agents_status():
    """Get all agent statuses."""
    status = status_manager.get_all_status()
    return {
        "success": True,
        "agents": status.get("agents", {}),
        "last_updated": status.get("last_updated", "")
    }


@router.get("/agents/{session_name}")
async def get_agent_status(session_name: str):
    """Get status of a specific agent."""
    status = status_manager.get_all_status()
    agent = status.get("agents", {}).get(session_name)

    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {session_name} not found")

    return {
        "success": True,
        "agent": agent
    }


# ============== Agent Control Endpoints ==============

@router.post("/agents/{session_name}/connect")
async def connect_agent(session_name: str):
    """Queue a connect command for an agent."""
    # Verify session file exists
    session_file = SESSIONS_DIR / f"{session_name}.session"
    if not session_file.exists():
        raise HTTPException(status_code=404, detail=f"Session file not found: {session_name}")

    command_id = command_queue.add_command("connect_agent", session_name)
    return CommandResponse(
        success=True,
        command_id=command_id,
        message=f"Connect command queued for {session_name}"
    )


@router.post("/agents/{session_name}/disconnect")
async def disconnect_agent(session_name: str):
    """Queue a disconnect command for an agent."""
    command_id = command_queue.add_command("disconnect_agent", session_name)
    return CommandResponse(
        success=True,
        command_id=command_id,
        message=f"Disconnect command queued for {session_name}"
    )


@router.delete("/agents/{session_name}")
async def delete_agent(session_name: str):
    """Queue deletion of an agent (disconnect + delete session file)."""
    # Protect the main bot session from deletion
    if session_name == "bot_session":
        raise HTTPException(status_code=403, detail="Cannot delete the main bot session")

    command_id = command_queue.add_command("delete_agent", session_name)
    return CommandResponse(
        success=True,
        command_id=command_id,
        message=f"Delete command queued for {session_name}"
    )


@router.post("/agents/connect-all")
async def connect_all_agents():
    """Queue connect commands for all agents."""
    command_id = command_queue.add_command("connect_all", "all")
    return CommandResponse(
        success=True,
        command_id=command_id,
        message="Connect all command queued"
    )


@router.post("/agents/disconnect-all")
async def disconnect_all_agents():
    """Queue disconnect commands for all agents."""
    command_id = command_queue.add_command("disconnect_all", "all")
    return CommandResponse(
        success=True,
        command_id=command_id,
        message="Disconnect all command queued"
    )


# ============== Health Check ==============

@router.post("/health-check")
async def trigger_health_check():
    """Trigger immediate health check of all connections."""
    command_id = command_queue.add_command("health_check", "all")
    return CommandResponse(
        success=True,
        command_id=command_id,
        message="Health check command queued"
    )


# ============== Command Status ==============

@router.get("/commands/{command_id}")
async def get_command_status(command_id: str):
    """Get status of a queued command."""
    command = command_queue.get_command_status(command_id)
    if not command:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")

    return {
        "success": True,
        "command": command.to_dict()
    }


# ============== LLM Status ==============

@router.get("/llm")
async def get_llm_status():
    """Get LLM provider statuses."""
    status = status_manager.get_all_status()
    return {
        "success": True,
        "llm_providers": status.get("llm_providers", {}),
        "last_updated": status.get("last_updated", "")
    }


# ============== Available Sessions ==============

@router.get("/sessions")
async def get_available_sessions():
    """Get list of all available session files."""
    sessions = []
    if SESSIONS_DIR.exists():
        for session_file in SESSIONS_DIR.glob("*.session"):
            sessions.append({
                "session_name": session_file.stem,
                "path": str(session_file)
            })

    # Get current status for each
    status = status_manager.get_all_status()
    agents_status = status.get("agents", {})

    for session in sessions:
        agent_status = agents_status.get(session["session_name"], {})
        session["status"] = agent_status.get("status", "disconnected")
        session["user_info"] = agent_status.get("user_info")

    return {
        "success": True,
        "sessions": sessions
    }
