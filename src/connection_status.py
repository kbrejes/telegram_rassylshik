"""
Connection status storage and management.
Thread-safe JSON-based status tracking for all connections.
"""
import json
import os
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Status file path
STATUS_FILE = Path(__file__).parent.parent / "configs" / "connection_status.json"


@dataclass
class AgentStatus:
    """Status of a single agent."""
    session_name: str
    status: str  # connected | disconnected | flood_wait | auth_expired | error
    phone: str = ""
    error_message: Optional[str] = None
    flood_wait_until: Optional[float] = None
    last_health_check: Optional[str] = None
    user_info: Optional[Dict[str, Any]] = None
    can_join_groups: Optional[bool] = None  # None = unknown, True = can, False = restricted
    crm_access: Optional[bool] = None  # Can this agent access CRM groups?

    def to_dict(self) -> dict:
        return {
            "session_name": self.session_name,
            "status": self.status,
            "phone": self.phone,
            "error_message": self.error_message,
            "flood_wait_until": self.flood_wait_until,
            "last_health_check": self.last_health_check,
            "user_info": self.user_info,
            "can_join_groups": self.can_join_groups,
            "crm_access": self.crm_access
        }


@dataclass
class CRMGroupStatus:
    """Status of CRM group access."""
    group_id: int
    channel_id: str
    accessible: bool
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceChannelStatus:
    """Status of source channel membership."""
    source_id: str
    channel_id: Optional[int] = None
    title: Optional[str] = None
    accessible: bool = False
    is_member: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LLMProviderStatus:
    """Status of LLM provider."""
    name: str
    reachable: bool
    last_check: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class StatusManager:
    """
    Manages connection status storage.
    Thread-safe operations on JSON status file.
    """

    def __init__(self, status_file: Path = STATUS_FILE):
        self.status_file = status_file
        self._lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create status file with default structure if it doesn't exist."""
        if not self.status_file.exists():
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            default_status = {
                "last_updated": datetime.now().isoformat(),
                "bot": {
                    "connected": False,
                    "authorized": False,
                    "user_info": None
                },
                "agents": {},
                "crm_groups": {},
                "source_channels": {},
                "llm_providers": {}
            }
            self._write_status(default_status)

    def _read_status(self) -> dict:
        """Read current status from file."""
        try:
            with open(self.status_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {
                "last_updated": datetime.now().isoformat(),
                "bot": {"connected": False, "authorized": False, "user_info": None},
                "agents": {},
                "crm_groups": {},
                "source_channels": {},
                "llm_providers": {}
            }

    def _write_status(self, status: dict):
        """Write status to file atomically."""
        status["last_updated"] = datetime.now().isoformat()
        temp_file = self.status_file.with_suffix('.tmp')
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        temp_file.replace(self.status_file)

    def get_all_status(self) -> dict:
        """Get complete status of all connections."""
        with self._lock:
            return self._read_status()

    def update_bot_status(
        self,
        connected: bool,
        authorized: bool = False,
        user_info: Optional[dict] = None
    ):
        """Update bot connection status."""
        with self._lock:
            status = self._read_status()
            status["bot"] = {
                "connected": connected,
                "authorized": authorized,
                "user_info": user_info
            }
            self._write_status(status)
            logger.debug(f"Bot status updated: connected={connected}, authorized={authorized}")

    def update_agent_status(
        self,
        session_name: str,
        status: str,
        phone: str = "",
        error: Optional[str] = None,
        flood_wait_until: Optional[float] = None,
        user_info: Optional[dict] = None,
        can_join_groups: Optional[bool] = None,
        crm_access: Optional[bool] = None
    ):
        """Update agent connection status."""
        with self._lock:
            current = self._read_status()
            # Preserve existing values for fields not being updated
            existing = current.get("agents", {}).get(session_name, {})
            agent_status = AgentStatus(
                session_name=session_name,
                status=status,
                phone=phone,
                error_message=error,
                flood_wait_until=flood_wait_until,
                last_health_check=datetime.now().isoformat(),
                user_info=user_info,
                can_join_groups=can_join_groups if can_join_groups is not None else existing.get("can_join_groups"),
                crm_access=crm_access if crm_access is not None else existing.get("crm_access")
            )
            current["agents"][session_name] = agent_status.to_dict()
            self._write_status(current)
            logger.debug(f"Agent {session_name} status updated: {status}")

    def remove_agent_status(self, session_name: str):
        """Remove agent from status tracking."""
        with self._lock:
            current = self._read_status()
            if session_name in current["agents"]:
                del current["agents"][session_name]
                self._write_status(current)
                logger.debug(f"Agent {session_name} removed from status")

    def update_crm_status(
        self,
        channel_id: str,
        group_id: int,
        accessible: bool,
        error: Optional[str] = None
    ):
        """Update CRM group access status."""
        with self._lock:
            current = self._read_status()
            crm_status = CRMGroupStatus(
                group_id=group_id,
                channel_id=channel_id,
                accessible=accessible,
                error=error
            )
            current["crm_groups"][channel_id] = crm_status.to_dict()
            self._write_status(current)
            logger.debug(f"CRM {channel_id} status updated: accessible={accessible}")

    def update_source_status(
        self,
        source_id: str,
        channel_id: Optional[int] = None,
        title: Optional[str] = None,
        accessible: bool = False,
        is_member: bool = False,
        error: Optional[str] = None
    ):
        """Update source channel status."""
        with self._lock:
            current = self._read_status()
            source_status = SourceChannelStatus(
                source_id=source_id,
                channel_id=channel_id,
                title=title,
                accessible=accessible,
                is_member=is_member,
                error=error
            )
            current["source_channels"][source_id] = source_status.to_dict()
            self._write_status(current)
            logger.debug(f"Source {source_id} status updated: accessible={accessible}, member={is_member}")

    def update_llm_status(
        self,
        provider: str,
        reachable: bool,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None
    ):
        """Update LLM provider status."""
        with self._lock:
            current = self._read_status()
            llm_status = LLMProviderStatus(
                name=provider,
                reachable=reachable,
                last_check=datetime.now().isoformat(),
                latency_ms=latency_ms,
                error=error
            )
            current["llm_providers"][provider] = llm_status.to_dict()
            self._write_status(current)
            logger.debug(f"LLM {provider} status updated: reachable={reachable}")

    def clear_all(self):
        """Clear all status data."""
        with self._lock:
            self._write_status({
                "last_updated": datetime.now().isoformat(),
                "bot": {"connected": False, "authorized": False, "user_info": None},
                "agents": {},
                "crm_groups": {},
                "source_channels": {},
                "llm_providers": {}
            })


# Global instance
status_manager = StatusManager()
