"""Deduplication API endpoints."""

from typing import Annotated
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.api.deps import CurrentUser, get_current_user, get_db
from app.core.config import settings
from app.models import (
    Channel,
    DedupeGroup,
    DedupeGroupMember,
    DedupeGroupMemberPublic,
    DeduplicatedMessagePublic,
    TelegramMessage,
)

router = APIRouter(prefix="/deduplication", tags=["deduplication"])


class EmbeddingsJobRequest(BaseModel):
    """Request to generate embeddings."""

    batch_size: int = 50


class EmbeddingsJobResponse(BaseModel):
    """Response from embeddings job."""

    job_id: str
    status: str
    message: str


class DeduplicationJobRequest(BaseModel):
    """Request to run deduplication."""

    similarity_threshold: float = 0.85
    channel_ids: list[str] | None = None


class DeduplicationJobResponse(BaseModel):
    """Response from deduplication job."""

    job_id: str
    status: str
    message: str


class DeduplicationStatsResponse(BaseModel):
    """Deduplication statistics."""

    total_messages: int
    messages_with_embeddings: int
    dedupe_groups: int
    deduplicated_messages: int
    deduplication_ratio: float


class DeduplicatedMessagesResponse(BaseModel):
    """Response with deduplicated messages."""

    data: list[DeduplicatedMessagePublic]
    count: int
    total_unique_messages: int
    total_raw_messages: int
    deduplication_ratio: float


class DuplicatesResponse(BaseModel):
    """Response with duplicate messages for a primary message."""

    primary_message: DeduplicatedMessagePublic
    duplicates: list[DedupeGroupMemberPublic]
    group_confidence: float


@router.post(
    "/embeddings/generate",
    response_model=EmbeddingsJobResponse,
    dependencies=[Depends(get_current_user)],
)
async def generate_embeddings_job(
    request: EmbeddingsJobRequest,
) -> EmbeddingsJobResponse:
    """
    Queue job to generate embeddings for messages without them.

    Requires OpenAI API key to be configured.
    """
    if not settings.openai_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key not configured",
        )

    # Queue ARQ job
    redis = await create_pool(
        RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    )
    job = await redis.enqueue_job(
        "generate_embeddings",
        batch_size=request.batch_size,
    )
    await redis.close()

    return EmbeddingsJobResponse(
        job_id=job.job_id,
        status="queued",
        message=f"Embedding generation queued for batch size {request.batch_size}",
    )


@router.post(
    "/jobs",
    response_model=DeduplicationJobResponse,
    dependencies=[Depends(get_current_user)],
)
async def create_deduplication_job(
    request: DeduplicationJobRequest,
) -> DeduplicationJobResponse:
    """
    Queue job to deduplicate messages using semantic similarity.

    Requires embeddings to be generated first.
    """
    # Queue ARQ job
    redis = await create_pool(
        RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    )
    job = await redis.enqueue_job(
        "deduplicate_messages",
        similarity_threshold=request.similarity_threshold,
        channel_ids=request.channel_ids,
    )
    await redis.close()

    return DeduplicationJobResponse(
        job_id=job.job_id,
        status="queued",
        message=f"Deduplication job queued with threshold {request.similarity_threshold}",
    )


@router.get(
    "/stats",
    response_model=DeduplicationStatsResponse,
    dependencies=[Depends(get_current_user)],
)
def get_deduplication_stats(
    db: Annotated[Session, Depends(get_db)],
) -> DeduplicationStatsResponse:
    """Get deduplication statistics."""
    # Total messages
    total_messages = db.exec(select(func.count(TelegramMessage.id))).one()

    # Messages with embeddings
    messages_with_embeddings = db.exec(
        select(func.count(TelegramMessage.id)).where(
            TelegramMessage.embedding.is_not(None)
        )
    ).one()

    # Dedupe groups
    dedupe_groups = db.exec(select(func.count(DedupeGroup.id))).one()

    # Deduplicated messages
    deduplicated_messages = db.exec(
        select(func.count(TelegramMessage.id)).where(
            TelegramMessage.dedupe_group_id.is_not(None)
        )
    ).one()

    # Calculate deduplication ratio
    dedup_ratio = (
        (deduplicated_messages - dedupe_groups) / deduplicated_messages
        if deduplicated_messages > 0
        else 0.0
    )

    return DeduplicationStatsResponse(
        total_messages=total_messages,
        messages_with_embeddings=messages_with_embeddings,
        dedupe_groups=dedupe_groups,
        deduplicated_messages=deduplicated_messages,
        deduplication_ratio=dedup_ratio,
    )


@router.get("/messages", response_model=DeduplicatedMessagesResponse)
def list_deduplicated_messages(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    channel_id: UUID | None = None,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
) -> DeduplicatedMessagesResponse:
    """
    List deduplicated messages (primary messages from each group).

    Returns one message per dedupe group, ordered by timestamp.
    """
    # Build query for primary messages (those that are dedupe group primaries)
    # Filter by user's channels via Channel table
    query = (
        select(TelegramMessage, DedupeGroup)
        .join(DedupeGroup, TelegramMessage.id == DedupeGroup.primary_message_id)
        .join(Channel, TelegramMessage.channel_id == Channel.id)
        .where(Channel.user_id == current_user.id)
    )

    if channel_id:
        query = query.where(TelegramMessage.channel_id == channel_id)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_count = db.exec(count_query).one()

    # Get paginated results
    results = db.exec(
        query.order_by(TelegramMessage.timestamp.desc()).offset(offset).limit(limit)
    ).all()

    # Build response objects
    deduplicated_messages = []
    for msg, group in results:
        deduplicated_messages.append(
            DeduplicatedMessagePublic(
                id=msg.id,
                channel_id=msg.channel_id,
                telegram_message_id=msg.telegram_message_id,
                text=msg.text,
                timestamp=msg.timestamp,
                edited_at=msg.edited_at,
                author_id=msg.author_id,
                author_username=msg.author_username,
                view_count=msg.view_count,
                forward_count=msg.forward_count,
                reactions_json=msg.reactions_json,
                media_metadata_json=msg.media_metadata_json,
                scraped_at=msg.scraped_at,
                created_at=msg.created_at,
                dedupe_group_id=group.id,
                duplicate_count=group.group_size - 1,  # Exclude primary
                confidence_score=group.confidence_score,
            )
        )

    # Calculate stats - join through Channel to filter by user
    total_messages = db.exec(
        select(func.count(TelegramMessage.id))
        .join(Channel, TelegramMessage.channel_id == Channel.id)
        .where(Channel.user_id == current_user.id)
    ).one()

    total_deduplicated = db.exec(
        select(func.count(TelegramMessage.id))
        .join(Channel, TelegramMessage.channel_id == Channel.id)
        .where(
            Channel.user_id == current_user.id,
            TelegramMessage.dedupe_group_id.is_not(None),
        )
    ).one()

    dedup_ratio = (
        (total_deduplicated - total_count) / total_deduplicated
        if total_deduplicated > 0
        else 0.0
    )

    return DeduplicatedMessagesResponse(
        data=deduplicated_messages,
        count=len(deduplicated_messages),
        total_unique_messages=total_count,
        total_raw_messages=total_messages,
        deduplication_ratio=dedup_ratio,
    )


@router.get("/messages/{message_id}/duplicates", response_model=DuplicatesResponse)
def get_message_duplicates(
    message_id: UUID,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> DuplicatesResponse:
    """
    Get all duplicate messages for a given primary message.

    Returns the primary message and all similar messages with similarity scores.
    """
    # Get the message (filter by user's channels)
    message = db.exec(
        select(TelegramMessage)
        .join(Channel, TelegramMessage.channel_id == Channel.id)
        .where(
            TelegramMessage.id == message_id,
            Channel.user_id == current_user.id,
        )
    ).first()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Get dedupe group
    if not message.dedupe_group_id:
        raise HTTPException(
            status_code=400, detail="Message is not part of a dedupe group"
        )

    dedupe_group = db.exec(
        select(DedupeGroup).where(DedupeGroup.id == message.dedupe_group_id)
    ).first()

    if not dedupe_group:
        raise HTTPException(status_code=404, detail="Dedupe group not found")

    # Get all group members (excluding primary)
    members = db.exec(
        select(DedupeGroupMember)
        .where(
            DedupeGroupMember.group_id == dedupe_group.id,
            DedupeGroupMember.message_id != dedupe_group.primary_message_id,
        )
        .order_by(DedupeGroupMember.similarity_score.desc())
    ).all()

    # Build response
    primary_msg_data = DeduplicatedMessagePublic(
        id=message.id,
        channel_id=message.channel_id,
        telegram_message_id=message.telegram_message_id,
        text=message.text,
        timestamp=message.timestamp,
        edited_at=message.edited_at,
        author_id=message.author_id,
        author_username=message.author_username,
        view_count=message.view_count,
        forward_count=message.forward_count,
        reactions_json=message.reactions_json,
        media_metadata_json=message.media_metadata_json,
        scraped_at=message.scraped_at,
        created_at=message.created_at,
        dedupe_group_id=dedupe_group.id,
        duplicate_count=dedupe_group.group_size - 1,
        confidence_score=dedupe_group.confidence_score,
    )

    duplicates = [
        DedupeGroupMemberPublic(
            id=member.id,
            message_id=member.message_id,
            similarity_score=member.similarity_score,
            explanation_text=member.explanation_text,
        )
        for member in members
    ]

    return DuplicatesResponse(
        primary_message=primary_msg_data,
        duplicates=duplicates,
        group_confidence=dedupe_group.confidence_score,
    )
