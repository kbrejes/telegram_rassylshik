"""
Command queue for web-to-bot communication.
Allows web interface to request actions that bot thread executes.
"""
import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

# Command queue file path
COMMAND_FILE = Path(__file__).parent.parent / "configs" / "connection_commands.json"


@dataclass
class Command:
    """A command to be executed by the bot."""
    id: str
    type: str  # connect_agent | disconnect_agent | delete_agent | connect_all | disconnect_all | health_check | send_crm_message
    target: any  # session_name, "all", or dict for complex commands
    created_at: str
    status: str = "pending"  # pending | processing | completed | failed
    result_message: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Command":
        return cls(
            id=data["id"],
            type=data["type"],
            target=data["target"],
            created_at=data["created_at"],
            status=data.get("status", "pending"),
            result_message=data.get("result_message")
        )


class CommandQueue:
    """
    Manages command queue for cross-thread communication.
    Web writes commands, bot polls and executes.
    """

    def __init__(self, command_file: Path = COMMAND_FILE):
        self.command_file = command_file
        self._lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create command file if it doesn't exist."""
        if not self.command_file.exists():
            self.command_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_commands([])

    def _read_commands(self) -> List[dict]:
        """Read commands from file."""
        try:
            with open(self.command_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("commands", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write_commands(self, commands: List[dict]):
        """Write commands to file atomically."""
        temp_file = self.command_file.with_suffix('.tmp')
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump({"commands": commands}, f, ensure_ascii=False, indent=2)
        temp_file.replace(self.command_file)

    def add_command(self, command_type: str, target: any = "all") -> str:
        """
        Add a command to the queue.

        Args:
            command_type: Type of command (connect_agent, disconnect_agent, send_crm_message, etc.)
            target: Target session name, "all", or dict for complex commands

        Returns:
            Command ID
        """
        with self._lock:
            commands = self._read_commands()

            command_id = f"cmd_{uuid.uuid4().hex[:8]}"
            command = Command(
                id=command_id,
                type=command_type,
                target=target,
                created_at=datetime.now().isoformat(),
                status="pending"
            )

            commands.append(command.to_dict())
            self._write_commands(commands)

            logger.info(f"Command queued: {command_type} -> {target} (id={command_id})")
            return command_id

    def get_pending_commands(self) -> List[Command]:
        """Get all pending commands."""
        with self._lock:
            commands = self._read_commands()
            pending = [
                Command.from_dict(cmd)
                for cmd in commands
                if cmd.get("status") == "pending"
            ]
            return pending

    def mark_processing(self, command_id: str):
        """Mark a command as being processed."""
        with self._lock:
            commands = self._read_commands()
            for cmd in commands:
                if cmd["id"] == command_id:
                    cmd["status"] = "processing"
                    break
            self._write_commands(commands)

    def mark_completed(self, command_id: str, success: bool, message: Optional[str] = None):
        """Mark a command as completed or failed."""
        with self._lock:
            commands = self._read_commands()
            for cmd in commands:
                if cmd["id"] == command_id:
                    cmd["status"] = "completed" if success else "failed"
                    cmd["result_message"] = message
                    break
            self._write_commands(commands)
            logger.info(f"Command {command_id} {'completed' if success else 'failed'}: {message}")

    def get_command_status(self, command_id: str) -> Optional[Command]:
        """Get status of a specific command."""
        with self._lock:
            commands = self._read_commands()
            for cmd in commands:
                if cmd["id"] == command_id:
                    return Command.from_dict(cmd)
            return None

    def cleanup_old_commands(self, max_age_hours: int = 24):
        """Remove completed/failed commands older than max_age_hours."""
        with self._lock:
            commands = self._read_commands()
            cutoff = datetime.now().timestamp() - (max_age_hours * 3600)

            filtered = []
            for cmd in commands:
                # Keep pending commands
                if cmd.get("status") == "pending":
                    filtered.append(cmd)
                    continue

                # Keep recent completed/failed commands
                try:
                    created = datetime.fromisoformat(cmd["created_at"]).timestamp()
                    if created > cutoff:
                        filtered.append(cmd)
                except (ValueError, KeyError):
                    pass

            if len(filtered) != len(commands):
                self._write_commands(filtered)
                logger.debug(f"Cleaned up {len(commands) - len(filtered)} old commands")

    def clear_all(self):
        """Clear all commands."""
        with self._lock:
            self._write_commands([])


# Global instance
command_queue = CommandQueue()
