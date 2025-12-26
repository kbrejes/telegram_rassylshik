"""
Candidate Profile - stores candidate data for job applications
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

PROFILE_FILE = Path(__file__).parent.parent / "configs" / "candidate_profile.json"


@dataclass
class CandidateProfile:
    """Candidate profile data for job applications"""

    # Basic info
    name: str = ""
    phone: str = ""
    email: str = ""

    # Resume
    resume_text: str = ""  # Plain text version for bots
    resume_file_path: str = ""  # Path to PDF file

    # Professional info
    position: str = ""  # Desired position
    experience_years: int = 0
    skills: str = ""  # Comma-separated skills
    portfolio_url: str = ""
    linkedin_url: str = ""
    github_url: str = ""

    # Preferences
    salary_expectation: str = ""  # e.g., "от 150000" or "150000-200000"
    work_format: str = ""  # remote, office, hybrid
    location: str = ""

    # Short bio for introductions
    about: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CandidateProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def get_answer(self, question_type: str) -> Optional[str]:
        """Get answer for common bot questions"""
        mapping = {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "position": self.position,
            "experience": f"{self.experience_years} лет" if self.experience_years else "",
            "salary": self.salary_expectation,
            "location": self.location,
            "portfolio": self.portfolio_url,
            "linkedin": self.linkedin_url,
            "github": self.github_url,
            "about": self.about,
            "skills": self.skills,
        }
        return mapping.get(question_type, "")

    def is_complete(self) -> bool:
        """Check if profile has minimum required data"""
        return bool(self.name and (self.resume_text or self.resume_file_path))


def load_candidate_profile() -> CandidateProfile:
    """Load candidate profile from file"""
    if PROFILE_FILE.exists():
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return CandidateProfile.from_dict(data)
        except Exception as e:
            logger.error(f"Error loading candidate profile: {e}")
    return CandidateProfile()


def save_candidate_profile(profile: CandidateProfile) -> None:
    """Save candidate profile to file"""
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILE_FILE, 'w', encoding='utf-8') as f:
        json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
    logger.info("Candidate profile saved")
