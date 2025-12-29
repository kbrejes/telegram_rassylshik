"""Account status and health routes"""
from fastapi import APIRouter, HTTPException

import sys
sys.path.append(str(__file__).rsplit("/", 3)[0])

from core.telegram_client import get_client, get_account_status

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/status")
async def get_status():
    """
    Get current account status and health.

    Returns account info, block status, rate limit status, and session stats.
    """
    try:
        client = await get_client()
        status = client.status.to_dict()
        session_stats = client.get_session_stats()

        return {
            "account": status,
            "session": session_stats,
            "safety_config": {
                "max_searches_per_session": client.safety.max_searches_per_session,
                "max_channels_per_session": client.safety.max_channels_per_session,
                "min_delay_between_searches": client.safety.min_delay_between_searches,
                "min_delay_between_channel_fetches": client.safety.min_delay_between_channel_fetches,
            }
        }
    except Exception as e:
        return {
            "account": {
                "is_connected": False,
                "is_authorized": False,
                "is_blocked": False,
                "health": "error",
                "last_error": str(e)
            },
            "session": None,
            "safety_config": None
        }


@router.get("/health")
async def check_health():
    """
    Quick health check - verifies account is not blocked.

    Use this endpoint to monitor account status.
    """
    try:
        status = await get_account_status()

        if status.is_blocked:
            return {
                "healthy": False,
                "status": "blocked",
                "reason": status.block_reason,
                "username": status.username
            }

        if not status.is_authorized:
            return {
                "healthy": False,
                "status": "unauthorized",
                "reason": "Session not authorized"
            }

        if not status.is_connected:
            return {
                "healthy": False,
                "status": "disconnected",
                "reason": status.last_error
            }

        return {
            "healthy": True,
            "status": "ok",
            "username": status.username,
            "user_id": status.user_id
        }

    except Exception as e:
        return {
            "healthy": False,
            "status": "error",
            "reason": str(e)
        }


@router.post("/reset-session")
async def reset_session():
    """
    Reset session counters to allow more searches.

    Use after waiting the recommended cool-down period.
    """
    try:
        client = await get_client()
        client.reset_session_counters()
        return {
            "success": True,
            "message": "Session counters reset",
            "session": client.get_session_stats()
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to reset session: {e}")


@router.post("/disconnect")
async def disconnect():
    """Disconnect the Telegram client."""
    try:
        client = await get_client()
        await client.disconnect()
        return {"success": True, "message": "Disconnected"}
    except Exception as e:
        raise HTTPException(500, f"Failed to disconnect: {e}")
