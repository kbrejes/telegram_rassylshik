"""Seed channels management routes"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import sys
sys.path.append(str(__file__).rsplit("/", 3)[0])

from db.database import get_db
from db.models import SeedChannel

router = APIRouter(prefix="/api/seeds", tags=["seeds"])


class SeedChannelCreate(BaseModel):
    """Create a seed channel"""
    username: str
    title: Optional[str] = None
    category: Optional[str] = None


class SeedChannelBulkCreate(BaseModel):
    """Bulk create seed channels"""
    usernames: List[str]
    category: Optional[str] = None


class SeedChannelUpdate(BaseModel):
    """Update seed channel"""
    title: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("")
async def list_seeds(
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    """List all seed channels"""
    query = db.query(SeedChannel)

    if category:
        query = query.filter(SeedChannel.category == category)
    if is_active is not None:
        query = query.filter(SeedChannel.is_active == is_active)

    seeds = query.order_by(SeedChannel.category, SeedChannel.username).all()

    # Group by category
    by_category = {}
    for seed in seeds:
        cat = seed.category or "uncategorized"
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(seed.to_dict())

    return {
        "total": len(seeds),
        "by_category": by_category,
        "seeds": [s.to_dict() for s in seeds]
    }


@router.post("")
async def create_seed(seed: SeedChannelCreate, db: Session = Depends(get_db)):
    """Add a new seed channel"""
    # Normalize username
    username = seed.username.lower().lstrip("@")

    existing = db.query(SeedChannel).filter(SeedChannel.username == username).first()
    if existing:
        raise HTTPException(400, f"Seed @{username} already exists")

    new_seed = SeedChannel(
        username=username,
        title=seed.title,
        category=seed.category
    )
    db.add(new_seed)
    db.commit()

    return {"success": True, "seed": new_seed.to_dict()}


@router.post("/bulk")
async def create_seeds_bulk(data: SeedChannelBulkCreate, db: Session = Depends(get_db)):
    """Add multiple seed channels at once"""
    created = []
    skipped = []

    for username in data.usernames:
        username = username.lower().lstrip("@").strip()
        if not username:
            continue

        existing = db.query(SeedChannel).filter(SeedChannel.username == username).first()
        if existing:
            skipped.append(username)
            continue

        new_seed = SeedChannel(
            username=username,
            category=data.category
        )
        db.add(new_seed)
        created.append(username)

    db.commit()

    return {
        "success": True,
        "created": len(created),
        "skipped": len(skipped),
        "created_usernames": created,
        "skipped_usernames": skipped
    }


@router.get("/categories")
async def list_categories(db: Session = Depends(get_db)):
    """List all seed categories"""
    from sqlalchemy import func

    results = db.query(
        SeedChannel.category,
        func.count(SeedChannel.id).label("count")
    ).group_by(SeedChannel.category).all()

    return {
        "categories": [
            {"name": r[0] or "uncategorized", "count": r[1]}
            for r in results
        ]
    }


@router.get("/{seed_id}")
async def get_seed(seed_id: int, db: Session = Depends(get_db)):
    """Get a single seed channel"""
    seed = db.query(SeedChannel).filter(SeedChannel.id == seed_id).first()
    if not seed:
        raise HTTPException(404, "Seed channel not found")
    return seed.to_dict()


@router.patch("/{seed_id}")
async def update_seed(
    seed_id: int,
    update: SeedChannelUpdate,
    db: Session = Depends(get_db)
):
    """Update a seed channel"""
    seed = db.query(SeedChannel).filter(SeedChannel.id == seed_id).first()
    if not seed:
        raise HTTPException(404, "Seed channel not found")

    if update.title is not None:
        seed.title = update.title
    if update.category is not None:
        seed.category = update.category
    if update.is_active is not None:
        seed.is_active = update.is_active

    db.commit()
    return seed.to_dict()


@router.delete("/{seed_id}")
async def delete_seed(seed_id: int, db: Session = Depends(get_db)):
    """Delete a seed channel"""
    seed = db.query(SeedChannel).filter(SeedChannel.id == seed_id).first()
    if not seed:
        raise HTTPException(404, "Seed channel not found")

    username = seed.username
    db.delete(seed)
    db.commit()

    return {"success": True, "message": f"Seed @{username} deleted"}
