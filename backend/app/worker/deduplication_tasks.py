"""ARQ tasks for semantic deduplication pipeline."""

import math
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from arq import Retry
from sqlmodel import Session, select

from app.core.db import engine
from app.models import (
    DedupeGroup,
    DedupeGroupMember,
    TelegramMessage,
)
from app.services.embeddings import EmbeddingsError, embeddings_service

log = structlog.get_logger()

# Similarity threshold for grouping (>85% similarity per Story 2.3)
DEFAULT_SIMILARITY_THRESHOLD = 0.85

# Temporal proximity boost (Story 2.4)
TEMPORAL_PROXIMITY_HOURS = 6
TEMPORAL_BOOST_FACTOR = 0.10


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Args:
        vec1: First embedding vector
        vec2: Second embedding vector

    Returns:
        Cosine similarity score (0-1, where 1 is identical)
    """
    if len(vec1) != len(vec2):
        raise ValueError("Vectors must have same dimensions")

    dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=True))
    magnitude1 = math.sqrt(sum(a * a for a in vec1))
    magnitude2 = math.sqrt(sum(b * b for b in vec2))

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return dot_product / (magnitude1 * magnitude2)


async def generate_embeddings(
    _ctx: dict[str, Any],
    batch_size: int = 50,
) -> dict[str, Any]:
    """
    Generate embeddings for messages without them.

    Args:
        _ctx: ARQ context (unused)
        batch_size: Number of messages to process per batch

    Returns:
        Dictionary with generation results
    """
    await log.ainfo("Starting embedding generation", batch_size=batch_size)

    with Session(engine) as session:
        # Get messages without embeddings that have text content
        messages = session.exec(
            select(TelegramMessage)
            .where(
                TelegramMessage.embedding.is_(None),
                TelegramMessage.text.is_not(None),
                TelegramMessage.text != "",
            )
            .limit(batch_size)
        ).all()

        if not messages:
            await log.ainfo("No messages need embeddings")
            return {"status": "success", "messages_processed": 0}

        await log.ainfo("Generating embeddings", message_count=len(messages))

        # Process in batches
        texts = [msg.text for msg in messages if msg.text]
        try:
            embeddings = await embeddings_service.get_embeddings_batch(texts)

            # Update messages with embeddings
            for msg, embedding in zip(messages, embeddings, strict=True):
                msg.embedding = embedding
                msg.embedding_generated_at = datetime.utcnow()
                msg.embedding_model = "text-embedding-3-small"
                session.add(msg)

            session.commit()

            await log.ainfo(
                "Embeddings generated successfully",
                messages_processed=len(messages),
            )

            return {
                "status": "success",
                "messages_processed": len(messages),
            }

        except EmbeddingsError as e:
            await log.aerror("Embedding generation failed", error=str(e))
            raise Retry(defer=60) from e  # Retry after 60 seconds


async def deduplicate_messages(
    _ctx: dict[str, Any],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    channel_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Detect semantically similar messages and create dedupe groups.

    Args:
        _ctx: ARQ context (unused)
        similarity_threshold: Minimum similarity score to group messages (default 0.85)
        channel_ids: Optional list of channel UUIDs to filter by

    Returns:
        Dictionary with deduplication results
    """
    await log.ainfo(
        "Starting deduplication",
        threshold=similarity_threshold,
        channel_filter=bool(channel_ids),
    )

    with Session(engine) as session:
        # Get messages with embeddings but not yet deduplicated
        query = select(TelegramMessage).where(
            TelegramMessage.embedding.is_not(None),
            TelegramMessage.dedupe_group_id.is_(None),
        )

        if channel_ids:
            channel_uuids = [UUID(cid) for cid in channel_ids]
            query = query.where(TelegramMessage.channel_id.in_(channel_uuids))

        messages = session.exec(query.order_by(TelegramMessage.timestamp.desc())).all()

        if len(messages) < 2:
            await log.ainfo("Not enough messages to deduplicate", count=len(messages))
            return {"status": "success", "groups_created": 0, "messages_processed": 0}

        await log.ainfo("Processing messages for deduplication", count=len(messages))

        groups_created = 0
        messages_processed = 0
        processed_ids = set()

        # Process messages to find similar groups
        for i, msg1 in enumerate(messages):
            if msg1.id in processed_ids:
                continue

            # Find similar messages
            similar_messages = []
            for msg2 in messages[i + 1 :]:
                if msg2.id in processed_ids:
                    continue

                # Calculate similarity
                similarity = cosine_similarity(msg1.embedding, msg2.embedding)

                # Apply temporal proximity boost (Story 2.4)
                time_diff = abs(
                    (msg1.timestamp - msg2.timestamp).total_seconds() / 3600
                )
                if time_diff <= TEMPORAL_PROXIMITY_HOURS:
                    similarity += TEMPORAL_BOOST_FACTOR
                    similarity = min(similarity, 1.0)  # Cap at 1.0

                if similarity >= similarity_threshold:
                    similar_messages.append((msg2, similarity))

            # If we found similar messages, create a group
            if similar_messages:
                # Select primary message (highest view count)
                all_messages = [msg1] + [m for m, _ in similar_messages]
                primary_msg = max(
                    all_messages, key=lambda m: m.view_count or 0, default=msg1
                )

                # Calculate group confidence score (Story 2.4)
                avg_similarity = sum(s for _, s in similar_messages) / len(
                    similar_messages
                )
                group_size = len(all_messages)
                confidence_score = avg_similarity * (
                    1 + math.log(group_size) / 10
                )  # Boost for larger groups
                confidence_score = min(confidence_score, 1.0)  # Cap at 1.0

                # Create dedupe group
                dedupe_group = DedupeGroup(
                    primary_message_id=primary_msg.id,
                    confidence_score=confidence_score,
                    group_size=group_size,
                )
                session.add(dedupe_group)
                session.flush()  # Get group ID

                # Add all messages to group
                for message in all_messages:
                    message.dedupe_group_id = dedupe_group.id
                    processed_ids.add(message.id)
                    session.add(message)

                # Create group member records with explanations (Story 2.4)
                for msg, similarity in [(msg1, 1.0)] + similar_messages:
                    # Generate explanation
                    time_diff_hours = abs(
                        (msg.timestamp - primary_msg.timestamp).total_seconds() / 3600
                    )
                    channel_count = len(
                        {m.channel_id for m in all_messages}
                    )  # Unique channels

                    explanation_parts = [
                        f"Cosine similarity: {similarity:.3f}",
                    ]
                    if time_diff_hours <= TEMPORAL_PROXIMITY_HOURS:
                        explanation_parts.append(
                            f"Posted within {time_diff_hours:.1f} hours"
                        )
                    if channel_count > 1:
                        explanation_parts.append(
                            f"Cross-channel validation ({channel_count} channels)"
                        )

                    member = DedupeGroupMember(
                        group_id=dedupe_group.id,
                        message_id=msg.id,
                        similarity_score=similarity,
                        explanation_text=" • ".join(explanation_parts),
                    )
                    session.add(member)

                groups_created += 1
                messages_processed += len(all_messages)

                await log.ainfo(
                    "Created dedupe group",
                    group_id=str(dedupe_group.id),
                    size=group_size,
                    confidence=f"{confidence_score:.3f}",
                )

        session.commit()

        await log.ainfo(
            "Deduplication complete",
            groups_created=groups_created,
            messages_processed=messages_processed,
        )

        return {
            "status": "success",
            "groups_created": groups_created,
            "messages_processed": messages_processed,
        }
