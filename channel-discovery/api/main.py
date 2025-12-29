"""Channel Discovery Service - FastAPI Application"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent))

from db.models import Base
from db.database import engine
from api.routes import search_router, channels_router, seeds_router, account_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Channel Discovery Service...")

    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")

    yield

    # Shutdown
    logger.info("Shutting down Channel Discovery Service...")


# Create app
app = FastAPI(
    title="Channel Discovery Service",
    description="Autonomous Telegram channel discovery and analytics",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Include routers
app.include_router(search_router)
app.include_router(channels_router)
app.include_router(seeds_router)
app.include_router(account_router)


@app.get("/")
async def index(request: Request):
    """Main UI page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "channel-discovery"}


@app.get("/api")
async def api_info():
    """API information"""
    return {
        "service": "Channel Discovery Service",
        "version": "1.0.0",
        "endpoints": {
            "search": {
                "POST /api/search": "Start a discovery search job",
                "GET /api/search/{job_id}": "Get search job status",
                "GET /api/search": "List search jobs",
                "POST /api/search/quick": "Quick preview search"
            },
            "channels": {
                "GET /api/channels": "List discovered channels",
                "GET /api/channels/stats": "Get aggregate statistics",
                "GET /api/channels/{id}": "Get single channel",
                "PATCH /api/channels/{id}": "Update channel",
                "DELETE /api/channels/{id}": "Delete channel",
                "POST /api/channels/bulk": "Bulk actions",
                "POST /api/channels/{id}/refresh": "Refresh channel stats"
            },
            "seeds": {
                "GET /api/seeds": "List seed channels",
                "POST /api/seeds": "Add seed channel",
                "POST /api/seeds/bulk": "Bulk add seeds",
                "GET /api/seeds/categories": "List categories",
                "PATCH /api/seeds/{id}": "Update seed",
                "DELETE /api/seeds/{id}": "Delete seed"
            }
        }
    }


if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT

    uvicorn.run(
        "api.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=True
    )
