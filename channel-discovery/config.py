"""Configuration for Channel Discovery Service"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).parent
PARENT_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Load .env from parent project
load_dotenv(PARENT_DIR / ".env")

# Telegram API credentials (reuse from main project)
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")

# Session for discovery bot (separate from main bot)
SESSION_PATH = DATA_DIR / "discovery_bot"

# Database
DATABASE_URL = os.getenv("DISCOVERY_DB_URL", f"sqlite:///{DATA_DIR}/discovery.db")

# API Server
API_HOST = os.getenv("DISCOVERY_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("DISCOVERY_API_PORT", "8081"))

# Rate limits (requests per minute)
RATE_LIMIT_SEARCH = 20
RATE_LIMIT_STATS = 30

# Discovery defaults
DEFAULT_SEARCH_LIMIT = 100
DEFAULT_MIN_SUBSCRIBERS = 500
DEFAULT_MAX_SUBSCRIBERS = 500000
DEFAULT_MIN_POSTS_PER_WEEK = 1

# Keywords for job/marketing niche (default, can be overridden per search)
DEFAULT_KEYWORDS = [
    "вакансии", "работа", "job", "jobs", "hiring",
    "маркетинг", "marketing", "smm", "таргет",
    "digital", "remote", "удаленка", "it вакансии",
    "developer jobs", "tech jobs"
]
