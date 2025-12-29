"""Discovered channels management routes"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc

import sys
sys.path.append(str(__file__).rsplit("/", 3)[0])

from db.database import get_db
from db.models import DiscoveredChannel

router = APIRouter(prefix="/api/channels", tags=["channels"])


class ChannelUpdate(BaseModel):
    """Update channel fields"""
    added_to_monitoring: Optional[bool] = None
    ignored: Optional[bool] = None
    notes: Optional[str] = None


class BulkAction(BaseModel):
    """Bulk action on multiple channels"""
    channel_ids: List[int]
    action: str  # 'monitor', 'ignore', 'delete'


@router.get("")
async def list_channels(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_score: float = Query(0, ge=0),
    min_subscribers: int = Query(0, ge=0),
    max_subscribers: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    ignored: bool = Query(False),
    added_to_monitoring: Optional[bool] = Query(None),
    sort_by: str = Query("relevance_score", regex="^(relevance_score|subscribers|posts_per_week|first_seen)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    search: Optional[str] = Query(None, description="Search in username/title"),
    db: Session = Depends(get_db)
):
    """
    List discovered channels with filtering and sorting.
    """
    query = db.query(DiscoveredChannel)

    # Filters
    query = query.filter(DiscoveredChannel.relevance_score >= min_score)
    query = query.filter(DiscoveredChannel.subscribers >= min_subscribers)
    query = query.filter(DiscoveredChannel.ignored == ignored)

    if max_subscribers:
        query = query.filter(DiscoveredChannel.subscribers <= max_subscribers)

    if is_active is not None:
        query = query.filter(DiscoveredChannel.is_active == is_active)

    if added_to_monitoring is not None:
        query = query.filter(DiscoveredChannel.added_to_monitoring == added_to_monitoring)

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (DiscoveredChannel.username.ilike(search_pattern)) |
            (DiscoveredChannel.title.ilike(search_pattern))
        )

    # Sorting
    sort_column = getattr(DiscoveredChannel, sort_by)
    if sort_order == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Get total count before pagination
    total = query.count()

    # Pagination
    channels = query.offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "channels": [ch.to_dict() for ch in channels]
    }


@router.get("/stats")
async def get_channels_stats(db: Session = Depends(get_db)):
    """Get aggregate statistics about discovered channels"""
    total = db.query(DiscoveredChannel).count()
    active = db.query(DiscoveredChannel).filter(DiscoveredChannel.is_active == True).count()
    monitored = db.query(DiscoveredChannel).filter(DiscoveredChannel.added_to_monitoring == True).count()
    ignored = db.query(DiscoveredChannel).filter(DiscoveredChannel.ignored == True).count()

    # Score distribution
    excellent = db.query(DiscoveredChannel).filter(DiscoveredChannel.relevance_score >= 15).count()
    good = db.query(DiscoveredChannel).filter(
        DiscoveredChannel.relevance_score >= 10,
        DiscoveredChannel.relevance_score < 15
    ).count()
    moderate = db.query(DiscoveredChannel).filter(
        DiscoveredChannel.relevance_score >= 5,
        DiscoveredChannel.relevance_score < 10
    ).count()

    return {
        "total": total,
        "active": active,
        "monitored": monitored,
        "ignored": ignored,
        "by_score": {
            "excellent": excellent,
            "good": good,
            "moderate": moderate,
            "low": total - excellent - good - moderate
        }
    }


@router.get("/{channel_id}")
async def get_channel(channel_id: int, db: Session = Depends(get_db)):
    """Get a single channel by ID"""
    channel = db.query(DiscoveredChannel).filter(DiscoveredChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")
    return channel.to_dict()


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: int,
    update: ChannelUpdate,
    db: Session = Depends(get_db)
):
    """Update channel fields"""
    channel = db.query(DiscoveredChannel).filter(DiscoveredChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")

    if update.added_to_monitoring is not None:
        channel.added_to_monitoring = update.added_to_monitoring
    if update.ignored is not None:
        channel.ignored = update.ignored
    if update.notes is not None:
        channel.notes = update.notes

    db.commit()
    return channel.to_dict()


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    """Delete a channel from the database"""
    channel = db.query(DiscoveredChannel).filter(DiscoveredChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")

    db.delete(channel)
    db.commit()
    return {"success": True, "message": f"Channel @{channel.username} deleted"}


@router.post("/bulk")
async def bulk_action(action: BulkAction, db: Session = Depends(get_db)):
    """Perform bulk actions on multiple channels"""
    channels = db.query(DiscoveredChannel).filter(
        DiscoveredChannel.id.in_(action.channel_ids)
    ).all()

    if not channels:
        raise HTTPException(404, "No channels found")

    count = len(channels)

    if action.action == "monitor":
        for ch in channels:
            ch.added_to_monitoring = True
            ch.ignored = False
    elif action.action == "ignore":
        for ch in channels:
            ch.ignored = True
            ch.added_to_monitoring = False
    elif action.action == "delete":
        for ch in channels:
            db.delete(ch)
    else:
        raise HTTPException(400, f"Unknown action: {action.action}")

    db.commit()
    return {"success": True, "affected": count, "action": action.action}


@router.post("/{channel_id}/refresh")
async def refresh_channel_stats(channel_id: int, db: Session = Depends(get_db)):
    """Refresh stats for a single channel"""
    from core.telegram_client import get_client
    from datetime import datetime

    channel = db.query(DiscoveredChannel).filter(DiscoveredChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")

    try:
        client = await get_client()
        stats = await client.get_channel_stats(channel.username)

        if stats:
            channel.subscribers = stats.get("subscribers", channel.subscribers)
            channel.posts_per_week = stats.get("posts_per_week", channel.posts_per_week)
            channel.avg_views = stats.get("avg_views", channel.avg_views)
            channel.engagement_rate = stats.get("engagement_rate", channel.engagement_rate)
            channel.last_post_date = stats.get("last_post_date", channel.last_post_date)
            channel.is_active = stats.get("is_active", channel.is_active)
            channel.stats_updated = datetime.utcnow()
            db.commit()

            return {"success": True, "channel": channel.to_dict()}
        else:
            return {"success": False, "message": "Could not fetch stats"}

    except Exception as e:
        raise HTTPException(500, f"Failed to refresh: {str(e)}")
