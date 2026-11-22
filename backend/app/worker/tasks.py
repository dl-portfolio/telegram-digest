"""ARQ task definitions for background processing."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from arq import Retry
from sqlmodel import Session, col, select

from app.core.db import engine
from app.models import Channel, TelegramMessage
from app.services.telegram_client import (
    TelegramChannelAccessError,
    TelegramChannelNotFoundError,
    TelegramRateLimitError,
    telegram_service,
)

log = structlog.get_logger()

# Default number of messages to fetch per scrape
DEFAULT_MESSAGE_LIMIT = 100

# Max wait time for rate limits (in seconds)
MAX_RATE_LIMIT_WAIT = 900  # 15 minutes


# arq's cron() is typed against the WorkerCoroutine protocol, whose first
# parameter is named `ctx`, so the name has to stay even though it is unused.
async def health_check(ctx: dict[str, Any]) -> dict[str, str]:  # noqa: ARG001
    """
    Health check task to verify worker is running.

    This task is scheduled to run periodically to confirm
    the worker and Redis connection are healthy.
    """
    await log.ainfo("Health check task executed")
    return {"status": "healthy", "message": "Worker is running"}


async def scrape_channel(
    _ctx: dict[str, Any],
    channel_id: str,
    limit: int = DEFAULT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    """
    Scrape messages from a Telegram channel.

    Args:
        _ctx: ARQ context (unused)
        channel_id: UUID of the channel to scrape (as string)
        limit: Maximum number of messages to fetch

    Returns:
        Dictionary with scrape results
    """
    channel_uuid = UUID(channel_id)

    await log.ainfo("Starting channel scrape", channel_id=channel_id, limit=limit)

    with Session(engine) as session:
        # Get channel from database
        channel = session.exec(
            select(Channel).where(Channel.id == channel_uuid)
        ).first()

        if not channel:
            await log.aerror("Channel not found in database", channel_id=channel_id)
            return {"status": "error", "error": "Channel not found"}

        if not channel.is_active:
            await log.ainfo("Channel is inactive, skipping", channel_id=channel_id)
            return {"status": "skipped", "reason": "Channel is inactive"}

        try:
            # Resolve channel if we don't have telegram_id yet
            if not channel.telegram_id:
                telegram_id, channel_title = await telegram_service.resolve_channel(
                    channel.telegram_identifier
                )
                channel.telegram_id = telegram_id
                if not channel.name:
                    channel.name = channel_title
                session.add(channel)
                session.commit()
                session.refresh(channel)

                await log.ainfo(
                    "Resolved channel",
                    channel_id=channel_id,
                    telegram_id=telegram_id,
                    title=channel_title,
                )

            # Fetch messages
            messages_fetched = 0
            last_message_id = channel.last_scraped_message_id

            async for msg_data in telegram_service.fetch_messages(
                channel.telegram_identifier,
                min_id=channel.last_scraped_message_id,
                limit=limit,
            ):
                # Check for duplicate message
                existing = session.exec(
                    select(TelegramMessage).where(
                        TelegramMessage.channel_id == channel.id,
                        TelegramMessage.telegram_message_id == msg_data["telegram_message_id"],
                    )
                ).first()

                if existing:
                    # Update existing message with new data
                    existing.text = msg_data["text"]
                    existing.edited_at = msg_data["edited_at"]
                    existing.view_count = msg_data["view_count"]
                    existing.forward_count = msg_data["forward_count"]
                    existing.reactions_json = msg_data["reactions_json"]
                    existing.scraped_at = datetime.utcnow()
                    session.add(existing)
                else:
                    # Create new message
                    message = TelegramMessage(
                        channel_id=channel.id,
                        telegram_message_id=msg_data["telegram_message_id"],
                        text=msg_data["text"],
                        timestamp=msg_data["timestamp"],
                        edited_at=msg_data["edited_at"],
                        author_id=msg_data["author_id"],
                        author_username=msg_data["author_username"],
                        view_count=msg_data["view_count"],
                        forward_count=msg_data["forward_count"],
                        reactions_json=msg_data["reactions_json"],
                        media_metadata_json=msg_data["media_metadata_json"],
                    )
                    session.add(message)

                messages_fetched += 1

                # Track highest message ID
                if last_message_id is None or msg_data["telegram_message_id"] > last_message_id:
                    last_message_id = msg_data["telegram_message_id"]

            # Update channel scrape metadata
            channel.last_scraped_at = datetime.utcnow()
            if last_message_id:
                channel.last_scraped_message_id = last_message_id
            session.add(channel)
            session.commit()

            await log.ainfo(
                "Channel scrape completed",
                channel_id=channel_id,
                messages_fetched=messages_fetched,
                last_message_id=last_message_id,
            )

            return {
                "status": "success",
                "channel_id": channel_id,
                "messages_fetched": messages_fetched,
                "last_message_id": last_message_id,
            }

        except TelegramChannelNotFoundError as e:
            await log.aerror(
                "Channel not found on Telegram",
                channel_id=channel_id,
                error=str(e),
            )
            return {"status": "error", "error": str(e)}

        except TelegramChannelAccessError as e:
            await log.aerror(
                "Cannot access channel",
                channel_id=channel_id,
                error=str(e),
            )
            return {"status": "error", "error": str(e)}

        except TelegramRateLimitError as e:
            wait_seconds = min(e.wait_seconds, MAX_RATE_LIMIT_WAIT)
            await log.awarning(
                "Rate limited, scheduling retry",
                channel_id=channel_id,
                wait_seconds=wait_seconds,
                original_wait=e.wait_seconds,
            )
            # Use ARQ's Retry mechanism with the specified delay
            raise Retry(defer=timedelta(seconds=wait_seconds + 5))

        except Exception as e:
            await log.aerror(
                "Unexpected error during scrape",
                channel_id=channel_id,
                error=str(e),
                exc_info=True,
            )
            return {"status": "error", "error": str(e)}


async def scrape_all_channels(ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Scrape all active channels for a user.

    This is a scheduled task that runs periodically.
    """
    await log.ainfo("Starting scheduled scrape of all channels")

    with Session(engine) as session:
        # Get all active channels
        channels = session.exec(
            select(Channel).where(col(Channel.is_active).is_(True))
        ).all()

        if not channels:
            await log.ainfo("No active channels to scrape")
            return {"status": "success", "channels_queued": 0}

        # Queue scrape jobs for each channel
        from arq import ArqRedis


        redis: ArqRedis = ctx.get("redis")
        if not redis:
            await log.aerror("Redis connection not available in context")
            return {"status": "error", "error": "Redis not available"}

        queued = 0
        for channel in channels:
            await redis.enqueue_job(
                "scrape_channel",
                str(channel.id),
            )
            queued += 1

        await log.ainfo(
            "Queued channel scrape jobs",
            channels_queued=queued,
        )

        return {"status": "success", "channels_queued": queued}
