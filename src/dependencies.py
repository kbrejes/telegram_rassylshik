"""
Dependency Container

Central location for application dependencies.
Allows explicit dependency injection for testing while
providing convenient defaults for production code.

Usage in production:
    from src.dependencies import deps
    db = deps.db
    config_manager = deps.config_manager

Usage in tests:
    from src.dependencies import Dependencies
    test_deps = Dependencies(db=mock_db, config_manager=mock_config)
    # inject test_deps into code under test
"""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.database import Database
    from src.config_manager import ConfigManager
    from src.connection_status import StatusManager


class Dependencies:
    """
    Container for application dependencies.

    Supports lazy initialization - dependencies are created on first access
    if not explicitly provided.
    """

    def __init__(
        self,
        db: Optional["Database"] = None,
        config_manager: Optional["ConfigManager"] = None,
        status_manager: Optional["StatusManager"] = None,
    ):
        self._db = db
        self._config_manager = config_manager
        self._status_manager = status_manager

    @property
    def db(self) -> "Database":
        """Get database instance (lazy initialization)."""
        if self._db is None:
            from src.database import db as default_db
            self._db = default_db
        return self._db

    @property
    def config_manager(self) -> "ConfigManager":
        """Get config manager instance (lazy initialization)."""
        if self._config_manager is None:
            from src.config_manager import config_manager as default_cm
            self._config_manager = default_cm
        return self._config_manager

    @property
    def status_manager(self) -> "StatusManager":
        """Get status manager instance (lazy initialization)."""
        if self._status_manager is None:
            from src.connection_status import status_manager as default_sm
            self._status_manager = default_sm
        return self._status_manager

    def override(
        self,
        db: Optional["Database"] = None,
        config_manager: Optional["ConfigManager"] = None,
        status_manager: Optional["StatusManager"] = None,
    ) -> "Dependencies":
        """
        Create a new Dependencies instance with overridden values.
        Useful for testing.
        """
        return Dependencies(
            db=db if db is not None else self._db,
            config_manager=config_manager if config_manager is not None else self._config_manager,
            status_manager=status_manager if status_manager is not None else self._status_manager,
        )


# Global default instance for production use
deps = Dependencies()
