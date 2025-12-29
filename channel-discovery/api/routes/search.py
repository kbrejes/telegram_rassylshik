"""Search and discovery job routes"""
import uuid
import asyncio
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import sys
sys.path.append(str(__file__).rsplit("/", 3)[0])

from db.database import get_db
from db.models import SearchJob, SeedChannel
from core.discovery import discovery_service

router = APIRouter(prefix="/api/search", tags=["search"])

# Store running jobs for progress tracking
_running_jobs: dict = {}


class SearchRequest(BaseModel):
    """Request to start a discovery search"""
    keywords: List[str] = Field(..., min_length=1, description="Keywords to search for")
    min_subscribers: int = Field(500, ge=0, description="Minimum subscriber count")
    max_subscribers: int = Field(500000, ge=0, description="Maximum subscriber count")
    min_posts_per_week: float = Field(0, ge=0, description="Minimum posts per week")
    use_seed_channels: bool = Field(False, description="Analyze seed channels for forwards/mentions")
    seed_category: Optional[str] = Field(None, description="Category of seed channels to use")


class QuickSearchRequest(BaseModel):
    """Request for quick preview search"""
    keywords: List[str] = Field(..., min_length=1)
    min_subscribers: int = Field(1000, ge=0)
    max_subscribers: int = Field(100000, ge=0)
    limit_per_keyword: int = Field(30, ge=1, le=100)


@router.post("")
async def start_search(
    request: SearchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Start a new discovery search job"""
    job_id = str(uuid.uuid4())

    # Get seed channels if requested
    seed_usernames = []
    if request.use_seed_channels:
        query = db.query(SeedChannel).filter(SeedChannel.is_active == True)
        if request.seed_category:
            query = query.filter(SeedChannel.category == request.seed_category)
        seeds = query.all()
        seed_usernames = [s.username for s in seeds]

    # Create job record
    job = SearchJob(
        job_id=job_id,
        keywords=request.keywords,
        min_subscribers=request.min_subscribers,
        max_subscribers=request.max_subscribers,
        min_posts_per_week=request.min_posts_per_week,
        use_seed_channels=request.use_seed_channels,
        seed_channel_ids=[s.username for s in seeds] if request.use_seed_channels else None,
        status="pending"
    )
    db.add(job)
    db.commit()

    # Initialize progress tracking
    _running_jobs[job_id] = {"progress": 0, "step": "Starting..."}

    def progress_callback(progress: int, step: str):
        _running_jobs[job_id] = {"progress": progress, "step": step}

    def run_job_sync():
        """Background job runner (sync wrapper)"""
        async def run_job_async():
            from db.database import get_db_session
            try:
                # Update status
                with get_db_session() as db_session:
                    job_record = db_session.query(SearchJob).filter(SearchJob.job_id == job_id).first()
                    if job_record:
                        job_record.status = "running"
                        job_record.started_at = datetime.utcnow()

                # Run discovery
                result = await discovery_service.run_search_job(
                    job_id=job_id,
                    keywords=request.keywords,
                    min_subscribers=request.min_subscribers,
                    max_subscribers=request.max_subscribers,
                    min_posts_per_week=request.min_posts_per_week,
                    use_seed_channels=request.use_seed_channels,
                    seed_usernames=seed_usernames,
                    progress_callback=progress_callback
                )

                # Update job record with results
                with get_db_session() as db_session:
                    job_record = db_session.query(SearchJob).filter(SearchJob.job_id == job_id).first()
                    if job_record:
                        job_record.status = "completed"
                        job_record.completed_at = datetime.utcnow()
                        job_record.channels_found = result.get("channels_found", 0)
                        job_record.channels_new = result.get("channels_new", 0)

            except Exception as e:
                import logging
                logging.error(f"Search job failed: {e}")
                with get_db_session() as db_session:
                    job_record = db_session.query(SearchJob).filter(SearchJob.job_id == job_id).first()
                    if job_record:
                        job_record.status = "failed"
                        job_record.error_message = str(e)
                        job_record.completed_at = datetime.utcnow()

            finally:
                # Clean up progress tracking after a delay
                await asyncio.sleep(60)
                _running_jobs.pop(job_id, None)

        # Run the async function in a new event loop
        asyncio.run(run_job_async())

    # Start background task
    background_tasks.add_task(run_job_sync)

    return {
        "job_id": job_id,
        "status": "started",
        "message": f"Search started with {len(request.keywords)} keywords"
        + (f" and {len(seed_usernames)} seed channels" if seed_usernames else "")
    }


@router.get("/{job_id}")
async def get_search_status(job_id: str, db: Session = Depends(get_db)):
    """Get status of a search job"""
    job = db.query(SearchJob).filter(SearchJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    result = job.to_dict()

    # Add live progress if still running
    if job_id in _running_jobs:
        result["live_progress"] = _running_jobs[job_id]["progress"]
        result["live_step"] = _running_jobs[job_id]["step"]

    return result


@router.get("")
async def list_search_jobs(
    limit: int = 20,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List recent search jobs"""
    query = db.query(SearchJob).order_by(SearchJob.created_at.desc())

    if status:
        query = query.filter(SearchJob.status == status)

    jobs = query.limit(limit).all()
    return {"jobs": [j.to_dict() for j in jobs]}


@router.post("/quick")
async def quick_search(request: QuickSearchRequest):
    """
    Quick preview search - returns basic results without full enrichment.
    Useful for testing keywords before running a full search.
    """
    try:
        results = await discovery_service.quick_search(
            keywords=request.keywords,
            min_subscribers=request.min_subscribers,
            max_subscribers=request.max_subscribers,
            limit_per_keyword=request.limit_per_keyword
        )
        return {
            "count": len(results),
            "channels": results[:50]  # Limit response size
        }
    except Exception as e:
        raise HTTPException(500, f"Search failed: {str(e)}")
