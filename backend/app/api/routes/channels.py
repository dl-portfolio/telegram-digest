"""Channel management API endpoints."""

import uuid

from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.api.deps import CurrentUser, get_db
from app.core.config import settings
from app.models import (
    Channel,
    ChannelCreate,
    ChannelPublic,
    ChannelsPublic,
    ChannelUpdate,
)

router = APIRouter(prefix="/channels", tags=["channels"])

# Maximum channels per user (MVP constraint)
MAX_CHANNELS_PER_USER = 3


@router.get("/", response_model=ChannelsPublic)
def list_channels(
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
    skip: int = 0,
    limit: int = 100,
) -> ChannelsPublic:
    """List all channels for the current user."""
    count_statement = (
        select(func.count())
        .select_from(Channel)
        .where(Channel.user_id == current_user.id)
    )
    count = session.exec(count_statement).one()

    statement = (
        select(Channel)
        .where(Channel.user_id == current_user.id)
        .offset(skip)
        .limit(limit)
    )
    channels = session.exec(statement).all()

    return ChannelsPublic(data=channels, count=count)


@router.post("/", response_model=ChannelPublic, status_code=status.HTTP_201_CREATED)
def create_channel(
    *,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
    channel_in: ChannelCreate,
) -> Channel:
    """Create a new channel.

    MVP constraint: Maximum 3 channels per user.
    """
    # Check channel limit
    count_statement = (
        select(func.count())
        .select_from(Channel)
        .where(Channel.user_id == current_user.id)
    )
    current_count = session.exec(count_statement).one()

    if current_count >= MAX_CHANNELS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum of {MAX_CHANNELS_PER_USER} channels allowed per user",
        )

    # Check for duplicate telegram_identifier for this user
    existing = session.exec(
        select(Channel).where(
            Channel.user_id == current_user.id,
            Channel.telegram_identifier == channel_in.telegram_identifier,
        )
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Channel with this identifier already exists",
        )

    channel = Channel.model_validate(channel_in, update={"user_id": current_user.id})
    session.add(channel)
    session.commit()
    session.refresh(channel)

    return channel


@router.get("/{channel_id}", response_model=ChannelPublic)
def get_channel(
    channel_id: uuid.UUID,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> Channel:
    """Get a specific channel by ID."""
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

    return channel


@router.patch("/{channel_id}", response_model=ChannelPublic)
def update_channel(
    channel_id: uuid.UUID,
    channel_in: ChannelUpdate,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> Channel:
    """Update a channel."""
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

    update_data = channel_in.model_dump(exclude_unset=True)
    channel.sqlmodel_update(update_data)
    session.add(channel)
    session.commit()
    session.refresh(channel)

    return channel


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(
    channel_id: uuid.UUID,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> None:
    """Delete a channel and all its messages."""
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

    session.delete(channel)
    session.commit()


class ScrapeResponse(BaseModel):
    """Response for scrape trigger endpoint."""

    status: str
    job_id: str | None = None
    message: str


async def get_redis_pool() -> ArqRedis:
    """Get ARQ Redis connection pool."""
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
    )
    return await create_pool(redis_settings)


@router.post("/{channel_id}/scrape", response_model=ScrapeResponse)
async def trigger_scrape(
    channel_id: uuid.UUID,
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> ScrapeResponse:
    """Trigger a scrape job for a specific channel.

    This queues a background job to fetch new messages from the channel.
    """
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

    if not channel.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Channel is not active",
        )

    try:
        redis = await get_redis_pool()
        job = await redis.enqueue_job("scrape_channel", str(channel_id))
        await redis.close()

        if job:
            return ScrapeResponse(
                status="queued",
                job_id=job.job_id,
                message=f"Scrape job queued for channel {channel.telegram_identifier}",
            )
        else:
            return ScrapeResponse(
                status="already_queued",
                message="A scrape job for this channel is already pending",
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue scrape job: {str(e)}",
        )


@router.post("/scrape-all", response_model=ScrapeResponse)
async def trigger_scrape_all(
    session: Session = Depends(get_db),
    current_user: CurrentUser = None,
) -> ScrapeResponse:
    """Trigger scrape jobs for all active channels.

    This queues background jobs for all active channels owned by the user.
    """
    # Get user's active channels
    channels = session.exec(
        select(Channel).where(
            Channel.user_id == current_user.id,
            Channel.is_active == True,
        )
    ).all()

    if not channels:
        return ScrapeResponse(
            status="no_channels",
            message="No active channels to scrape",
        )

    try:
        redis = await get_redis_pool()
        queued_count = 0

        for channel in channels:
            job = await redis.enqueue_job("scrape_channel", str(channel.id))
            if job:
                queued_count += 1

        await redis.close()

        return ScrapeResponse(
            status="queued",
            message=f"Queued scrape jobs for {queued_count} channels",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue scrape jobs: {str(e)}",
        )
