"""
API endpoints for candidate profile management
"""
import logging
from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from src.candidate_profile import (
    CandidateProfile,
    load_candidate_profile,
    save_candidate_profile,
    PROFILE_FILE
)
from src.database import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidate", tags=["candidate"])

# Resume upload directory
RESUME_DIR = Path(__file__).parent.parent.parent / "data" / "resumes"


class CandidateProfileRequest(BaseModel):
    name: str = ""
    phone: str = ""
    email: str = ""
    resume_text: str = ""
    position: str = ""
    experience_years: int = 0
    skills: str = ""
    portfolio_url: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    salary_expectation: str = ""
    work_format: str = ""
    location: str = ""
    about: str = ""


@router.get("/profile")
async def get_candidate_profile():
    """Get candidate profile"""
    try:
        profile = load_candidate_profile()
        return {
            "success": True,
            "profile": profile.to_dict(),
            "is_complete": profile.is_complete()
        }
    except Exception as e:
        logger.error(f"Error loading candidate profile: {e}")
        return {"success": False, "error": str(e)}


@router.put("/profile")
async def update_candidate_profile(request: CandidateProfileRequest):
    """Update candidate profile"""
    try:
        # Load existing to preserve resume_file_path
        existing = load_candidate_profile()

        profile = CandidateProfile(
            name=request.name,
            phone=request.phone,
            email=request.email,
            resume_text=request.resume_text,
            resume_file_path=existing.resume_file_path,  # Keep existing file
            position=request.position,
            experience_years=request.experience_years,
            skills=request.skills,
            portfolio_url=request.portfolio_url,
            linkedin_url=request.linkedin_url,
            github_url=request.github_url,
            salary_expectation=request.salary_expectation,
            work_format=request.work_format,
            location=request.location,
            about=request.about,
        )

        save_candidate_profile(profile)

        return {
            "success": True,
            "message": "Profile saved",
            "is_complete": profile.is_complete()
        }
    except Exception as e:
        logger.error(f"Error saving candidate profile: {e}")
        return {"success": False, "error": str(e)}


@router.post("/resume")
async def upload_resume(file: UploadFile = File(...)):
    """Upload resume file (PDF)"""
    try:
        # Validate file type
        if not file.filename.lower().endswith(('.pdf', '.doc', '.docx')):
            return {"success": False, "error": "Only PDF, DOC, DOCX files are allowed"}

        # Create directory if not exists
        RESUME_DIR.mkdir(parents=True, exist_ok=True)

        # Save file
        file_path = RESUME_DIR / f"resume_{file.filename}"
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Update profile with file path
        profile = load_candidate_profile()
        profile.resume_file_path = str(file_path)
        save_candidate_profile(profile)

        return {
            "success": True,
            "message": "Resume uploaded",
            "file_path": str(file_path)
        }
    except Exception as e:
        logger.error(f"Error uploading resume: {e}")
        return {"success": False, "error": str(e)}


@router.get("/bot-interactions")
async def get_bot_interactions():
    """Get list of bot interactions"""
    try:
        interactions = await db.get_bot_interactions(limit=100)
        return {
            "success": True,
            "interactions": interactions
        }
    except Exception as e:
        logger.error(f"Error getting bot interactions: {e}")
        return {"success": False, "error": str(e)}


@router.get("/bot-interactions/stats")
async def get_bot_interaction_stats():
    """Get bot interaction statistics"""
    try:
        all_interactions = await db.get_bot_interactions(limit=1000)

        stats = {
            "total": len(all_interactions),
            "success": len([i for i in all_interactions if i["status"] == "success"]),
            "failed": len([i for i in all_interactions if i["status"] == "failed"]),
            "in_progress": len([i for i in all_interactions if i["status"] == "in_progress"]),
            "timeout": len([i for i in all_interactions if i["status"] == "timeout"]),
        }

        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"Error getting bot stats: {e}")
        return {"success": False, "error": str(e)}
