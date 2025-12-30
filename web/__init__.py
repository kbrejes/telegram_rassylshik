"""Web interface for Job Notification Bot

Shared dependencies for web routes are provided here to avoid
creating multiple instances in each route module.
"""

from src.config_manager import ConfigManager

# Shared ConfigManager instance for all web routes
config_manager = ConfigManager()
