"""Message retrieval API endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, col, func, select

from app.api.deps import CurrentUser, get_db
from app.models import (
    Channel,
    TelegramMessage,
    TelegramMessagePublic,
    TelegramMessagesPublic,
)

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/", response_model=TelegramMessagesPublic)
def list_messages(
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
    channel_id: uuid.UUID | None = Query(None, description="Filter by channel ID"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    since: datetime | None = Query(None, description="Get messages after this timestamp"),
    until: datetime | None = Query(None, description="Get messages before this timestamp"),
    search: str | None = Query(None, min_length=2, description="Search in message text"),
) -> TelegramMessagesPublic:
    """List messages with optional filtering and pagination.

    Messages are returned in reverse chronological order (newest first).
    """
    # Get user's channel IDs for authorization
    user_channels = session.exec(
        select(Channel.id).where(Channel.user_id == current_user.id)
    ).all()

    if not user_channels:
        return TelegramMessagesPublic(data=[], count=0)

    # Base query - only messages from user's channels
    base_query = select(TelegramMessage).where(
        col(TelegramMessage.channel_id).in_(user_channels)
    )
    count_query = (
        select(func.count())
        .select_from(TelegramMessage)
        .where(col(TelegramMessage.channel_id).in_(user_channels))
    )

    # Apply channel filter
    if channel_id:
        if channel_id not in user_channels:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Channel not found",
            )
        base_query = base_query.where(TelegramMessage.channel_id == channel_id)
        count_query = count_query.where(TelegramMessage.channel_id == channel_id)

    # Apply time filters
    if since:
        base_query = base_query.where(TelegramMessage.timestamp >= since)
        count_query = count_query.where(TelegramMessage.timestamp >= since)
    if until:
        base_query = base_query.where(TelegramMessage.timestamp <= until)
        count_query = count_query.where(TelegramMessage.timestamp <= until)

    # Apply search filter
    if search:
        search_pattern = f"%{search}%"
        base_query = base_query.where(
            col(TelegramMessage.text).ilike(search_pattern)
        )
        count_query = count_query.where(
            col(TelegramMessage.text).ilike(search_pattern)
        )

    # Get total count
    count = session.exec(count_query).one()

    # Get messages with pagination, ordered by timestamp desc
    statement = (
        base_query
        .order_by(col(TelegramMessage.timestamp).desc())
        .offset(skip)
        .limit(limit)
    )
    messages = session.exec(statement).all()

    return TelegramMessagesPublic(data=messages, count=count)


@router.get("/{message_id}", response_model=TelegramMessagePublic)
def get_message(
    message_id: uuid.UUID,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> TelegramMessage:
    """Get a specific message by ID."""
    # Get user's channel IDs for authorization
    user_channels = session.exec(
        select(Channel.id).where(Channel.user_id == current_user.id)
    ).all()

    message = session.exec(
        select(TelegramMessage).where(
            TelegramMessage.id == message_id,
            col(TelegramMessage.channel_id).in_(user_channels),
        )
    ).first()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    return message


@router.get("/channel/{channel_id}", response_model=TelegramMessagesPublic)
def list_channel_messages(
    channel_id: uuid.UUID,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    since: datetime | None = Query(None, description="Get messages after this timestamp"),
) -> TelegramMessagesPublic:
    """List messages for a specific channel.

    Convenience endpoint for getting messages from a single channel.
    """
    # Verify channel ownership
    channel = session.exec(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.user_id == current_user.id,
        )
    ).first()

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    # Build query
    base_query = select(TelegramMessage).where(
        TelegramMessage.channel_id == channel_id
    )
    count_query = (
        select(func.count())
        .select_from(TelegramMessage)
        .where(TelegramMessage.channel_id == channel_id)
    )

    # Apply time filter
    if since:
        base_query = base_query.where(TelegramMessage.timestamp >= since)
        count_query = count_query.where(TelegramMessage.timestamp >= since)

    # Get total count
    count = session.exec(count_query).one()

    # Get messages with pagination
    statement = (
        base_query
        .order_by(col(TelegramMessage.timestamp).desc())
        .offset(skip)
        .limit(limit)
    )
    messages = session.exec(statement).all()

    return TelegramMessagesPublic(data=messages, count=count)


@router.get("/stats/summary")
def get_messages_summary(
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> dict:
    """Get summary statistics for user's messages."""
    # Get user's channel IDs
    user_channels = session.exec(
        select(Channel.id).where(Channel.user_id == current_user.id)
    ).all()

    if not user_channels:
        return {
            "total_messages": 0,
            "channels_count": 0,
            "oldest_message": None,
            "newest_message": None,
        }

    # Total message count
    total_count = session.exec(
        select(func.count())
        .select_from(TelegramMessage)
        .where(col(TelegramMessage.channel_id).in_(user_channels))
    ).one()

    # Oldest and newest message timestamps
    oldest = session.exec(
        select(func.min(TelegramMessage.timestamp))
        .where(col(TelegramMessage.channel_id).in_(user_channels))
    ).one()

    newest = session.exec(
        select(func.max(TelegramMessage.timestamp))
        .where(col(TelegramMessage.channel_id).in_(user_channels))
    ).one()

    # Messages per channel
    channel_stats = session.exec(
        select(
            TelegramMessage.channel_id,
            func.count().label("count"),
        )
        .where(col(TelegramMessage.channel_id).in_(user_channels))
        .group_by(TelegramMessage.channel_id)
    ).all()

    return {
        "total_messages": total_count,
        "channels_count": len(user_channels),
        "oldest_message": oldest.isoformat() if oldest else None,
        "newest_message": newest.isoformat() if newest else None,
        "messages_per_channel": {
            str(channel_id): count for channel_id, count in channel_stats
        },
    }
