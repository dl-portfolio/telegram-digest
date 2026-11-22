"""Tests for ARQ worker tasks with mocked Telegram service."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq import Retry
from sqlmodel import Session, delete

from app import crud
from app.core.config import settings
from app.models import Channel, TelegramMessage, User
from app.worker.tasks import (
    health_check,
    scrape_all_channels,
    scrape_channel,
)


@pytest.fixture
def mock_ctx():
    """Create a mock ARQ context."""
    return {}


@pytest.fixture
def test_user(db: Session) -> User:
    """Get or create the test superuser."""
    user = crud.get_user_by_email(session=db, email=settings.FIRST_SUPERUSER)
    if not user:
        raise ValueError("Test superuser not found - ensure init_db was run")
    return user


@pytest.fixture(autouse=True)
def cleanup_worker_test_data(db: Session):
    """Clean up worker test data before and after each test."""
    # Clean before
    db.execute(delete(TelegramMessage))
    db.execute(delete(Channel))
    db.commit()
    yield
    # Clean after
    db.rollback()  # Ensure clean state
    try:
        db.execute(delete(TelegramMessage))
        db.execute(delete(Channel))
        db.commit()
    except Exception:
        db.rollback()


@pytest.fixture
def test_channel(db: Session, test_user: User) -> Channel:
    """Create a test channel in the database."""
    channel = Channel(
        telegram_identifier="test_osint_channel",
        name="Test OSINT Channel",
        user_id=test_user.id,
        is_active=True,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@pytest.fixture
def inactive_channel(db: Session, test_user: User) -> Channel:
    """Create an inactive test channel."""
    channel = Channel(
        telegram_identifier="inactive_channel",
        name="Inactive Channel",
        user_id=test_user.id,
        is_active=False,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


class TestHealthCheck:
    """Test health check task."""

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy(self, mock_ctx):
        """Test health check returns healthy status."""
        result = await health_check(mock_ctx)

        assert result["status"] == "healthy"
        assert "message" in result


class TestScrapeChannel:
    """Test channel scraping task."""

    @pytest.mark.asyncio
    async def test_scrape_channel_not_found(self, mock_ctx, db: Session):
        """Test scraping a non-existent channel."""
        fake_id = str(uuid.uuid4())

        result = await scrape_channel(mock_ctx, fake_id)

        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_scrape_inactive_channel(
        self, mock_ctx, db: Session, inactive_channel: Channel
    ):
        """Test that inactive channels are skipped."""
        result = await scrape_channel(mock_ctx, str(inactive_channel.id))

        assert result["status"] == "skipped"
        assert "inactive" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_scrape_channel_success(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test successful channel scraping."""
        with patch("app.worker.tasks.telegram_service") as mock_service:
            # Mock channel resolution
            mock_service.resolve_channel = AsyncMock(
                return_value=(123456789, "Test OSINT Channel")
            )

            # Mock message fetching
            test_messages = [
                {
                    "telegram_message_id": 1001,
                    "text": "Test message 1 about cybersecurity",
                    "timestamp": datetime.utcnow() - timedelta(hours=1),
                    "edited_at": None,
                    "author_id": 12345,
                    "author_username": "test_author",
                    "view_count": 100,
                    "forward_count": 5,
                    "reactions_json": {"reactions": [{"emoticon": "👍", "count": 10}]},
                    "media_metadata_json": None,
                },
                {
                    "telegram_message_id": 1002,
                    "text": "Test message 2 about OSINT techniques",
                    "timestamp": datetime.utcnow(),
                    "edited_at": None,
                    "author_id": 12345,
                    "author_username": "test_author",
                    "view_count": 150,
                    "forward_count": 10,
                    "reactions_json": None,
                    "media_metadata_json": {"type": "photo"},
                },
            ]

            async def mock_fetch(*_args, **_kwargs):
                for msg in test_messages:
                    yield msg

            mock_service.fetch_messages = mock_fetch

            result = await scrape_channel(mock_ctx, str(test_channel.id))

            assert result["status"] == "success"
            assert result["messages_fetched"] == 2
            assert result["last_message_id"] == 1002

            # Verify channel was updated
            db.refresh(test_channel)
            assert test_channel.telegram_id == 123456789
            assert test_channel.last_scraped_at is not None
            assert test_channel.last_scraped_message_id == 1002

            # Verify messages were stored
            messages = db.query(TelegramMessage).filter(
                TelegramMessage.channel_id == test_channel.id
            ).all()
            assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_scrape_channel_incremental(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test incremental scraping with min_id."""
        # Set up channel with existing last_scraped_message_id
        test_channel.last_scraped_message_id = 1000
        test_channel.telegram_id = 123456789
        db.add(test_channel)
        db.commit()

        with patch("app.worker.tasks.telegram_service") as mock_service:
            captured_min_id = None

            async def mock_fetch(_identifier, min_id=None, **_kwargs):
                nonlocal captured_min_id
                captured_min_id = min_id
                yield {
                    "telegram_message_id": 1001,
                    "text": "New message",
                    "timestamp": datetime.utcnow(),
                    "edited_at": None,
                    "author_id": None,
                    "author_username": None,
                    "view_count": None,
                    "forward_count": None,
                    "reactions_json": None,
                    "media_metadata_json": None,
                }

            mock_service.fetch_messages = mock_fetch

            await scrape_channel(mock_ctx, str(test_channel.id))

            # Verify min_id was passed correctly
            assert captured_min_id == 1000

    @pytest.mark.asyncio
    async def test_scrape_channel_updates_existing(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test that existing messages are updated, not duplicated."""
        test_channel.telegram_id = 123456789
        db.add(test_channel)

        # Create existing message
        existing_msg = TelegramMessage(
            channel_id=test_channel.id,
            telegram_message_id=1001,
            text="Original text",
            timestamp=datetime.utcnow() - timedelta(hours=2),
            view_count=50,
        )
        db.add(existing_msg)
        db.commit()

        with patch("app.worker.tasks.telegram_service") as mock_service:
            async def mock_fetch(*_args, **_kwargs):
                yield {
                    "telegram_message_id": 1001,
                    "text": "Updated text",
                    "timestamp": datetime.utcnow() - timedelta(hours=2),
                    "edited_at": datetime.utcnow(),
                    "author_id": None,
                    "author_username": None,
                    "view_count": 100,  # Updated
                    "forward_count": 5,
                    "reactions_json": {"reactions": [{"emoticon": "❤️", "count": 3}]},
                    "media_metadata_json": None,
                }

            mock_service.fetch_messages = mock_fetch

            result = await scrape_channel(mock_ctx, str(test_channel.id))

            assert result["status"] == "success"
            assert result["messages_fetched"] == 1

            # Verify only one message exists (updated, not duplicated)
            messages = db.query(TelegramMessage).filter(
                TelegramMessage.channel_id == test_channel.id
            ).all()
            assert len(messages) == 1
            assert messages[0].text == "Updated text"
            assert messages[0].view_count == 100

    @pytest.mark.asyncio
    async def test_scrape_channel_not_found_on_telegram(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test handling when channel doesn't exist on Telegram."""
        from app.services.telegram_client import TelegramChannelNotFoundError

        with patch("app.worker.tasks.telegram_service") as mock_service:
            mock_service.resolve_channel = AsyncMock(
                side_effect=TelegramChannelNotFoundError("Channel not found")
            )

            result = await scrape_channel(mock_ctx, str(test_channel.id))

            assert result["status"] == "error"
            assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_scrape_channel_access_denied(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test handling when channel access is denied."""
        from app.services.telegram_client import TelegramChannelAccessError

        with patch("app.worker.tasks.telegram_service") as mock_service:
            mock_service.resolve_channel = AsyncMock(
                side_effect=TelegramChannelAccessError("Cannot access channel")
            )

            result = await scrape_channel(mock_ctx, str(test_channel.id))

            assert result["status"] == "error"
            assert "access" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_scrape_channel_rate_limited(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test that rate limiting triggers retry."""
        from app.services.telegram_client import TelegramRateLimitError

        with patch("app.worker.tasks.telegram_service") as mock_service:
            mock_service.resolve_channel = AsyncMock(
                side_effect=TelegramRateLimitError("Rate limited", wait_seconds=60)
            )

            # Should raise Retry exception for ARQ to handle
            with pytest.raises(Retry):
                await scrape_channel(mock_ctx, str(test_channel.id))


class TestScrapeAllChannels:
    """Test scraping all channels task."""

    @pytest.mark.asyncio
    async def test_scrape_all_no_channels(self, db: Session):
        """Test scrape all with no active channels."""
        mock_ctx = {}

        result = await scrape_all_channels(mock_ctx)

        assert result["status"] == "success"
        assert result["channels_queued"] == 0

    @pytest.mark.asyncio
    async def test_scrape_all_queues_active_channels(
        self, db: Session, test_channel: Channel, inactive_channel: Channel
    ):
        """Test that only active channels are queued."""
        mock_redis = AsyncMock()
        mock_redis.enqueue_job = AsyncMock(return_value=MagicMock())
        mock_ctx = {"redis": mock_redis}

        result = await scrape_all_channels(mock_ctx)

        assert result["status"] == "success"
        assert result["channels_queued"] == 1

        # Verify only active channel was queued
        mock_redis.enqueue_job.assert_called_once_with(
            "scrape_channel",
            str(test_channel.id),
        )

    @pytest.mark.asyncio
    async def test_scrape_all_no_redis(self, db: Session, test_channel: Channel):
        """Test error handling when Redis not available."""
        mock_ctx = {}  # No redis in context

        result = await scrape_all_channels(mock_ctx)

        assert result["status"] == "error"
        assert "redis" in result["error"].lower()


class TestIntegrationFlow:
    """Test the full scraping integration flow."""

    @pytest.mark.asyncio
    async def test_full_scrape_flow(
        self, mock_ctx, db: Session, test_channel: Channel
    ):
        """Test complete flow from channel to stored messages."""
        with patch("app.worker.tasks.telegram_service") as mock_service:
            # Mock successful channel resolution
            mock_service.resolve_channel = AsyncMock(
                return_value=(987654321, "OSINT Intelligence Channel")
            )

            # Create realistic message data
            now = datetime.utcnow()
            test_messages = [
                {
                    "telegram_message_id": 5001,
                    "text": "🔒 Security Alert: New vulnerability discovered in popular framework",
                    "timestamp": now - timedelta(hours=5),
                    "edited_at": None,
                    "author_id": 1234567890,
                    "author_username": "security_researcher",
                    "view_count": 1500,
                    "forward_count": 50,
                    "reactions_json": {
                        "reactions": [
                            {"emoticon": "🔥", "count": 25},
                            {"emoticon": "👀", "count": 15},
                        ],
                        "total": 40,
                    },
                    "media_metadata_json": {
                        "type": "webpage",
                        "url": "https://example.com/vuln",
                    },
                },
                {
                    "telegram_message_id": 5002,
                    "text": "New OSINT tool released for social media analysis",
                    "timestamp": now - timedelta(hours=2),
                    "edited_at": now - timedelta(hours=1),
                    "author_id": 1234567890,
                    "author_username": "osint_tools",
                    "view_count": 2000,
                    "forward_count": 100,
                    "reactions_json": None,
                    "media_metadata_json": {"type": "document", "mime_type": "application/pdf"},
                },
                {
                    "telegram_message_id": 5003,
                    "text": "Weekly threat intelligence roundup",
                    "timestamp": now - timedelta(minutes=30),
                    "edited_at": None,
                    "author_id": None,
                    "author_username": None,
                    "view_count": 500,
                    "forward_count": 20,
                    "reactions_json": {"reactions": [{"emoticon": "👍", "count": 10}], "total": 10},
                    "media_metadata_json": None,
                },
            ]

            async def mock_fetch(*_args, **_kwargs):
                for msg in test_messages:
                    yield msg

            mock_service.fetch_messages = mock_fetch

            # Run scrape
            result = await scrape_channel(mock_ctx, str(test_channel.id))

            # Verify result
            assert result["status"] == "success"
            assert result["messages_fetched"] == 3
            assert result["last_message_id"] == 5003

            # Verify channel was updated
            db.refresh(test_channel)
            assert test_channel.telegram_id == 987654321
            # Name only updates if it was None - our channel already had a name
            assert test_channel.name == "Test OSINT Channel"
            assert test_channel.last_scraped_at is not None
            assert test_channel.last_scraped_message_id == 5003

            # Verify all messages stored correctly
            stored_messages = (
                db.query(TelegramMessage)
                .filter(TelegramMessage.channel_id == test_channel.id)
                .order_by(TelegramMessage.telegram_message_id)
                .all()
            )
            assert len(stored_messages) == 3

            # Verify first message
            msg1 = stored_messages[0]
            assert msg1.telegram_message_id == 5001
            assert "Security Alert" in msg1.text
            assert msg1.view_count == 1500
            assert msg1.reactions_json["total"] == 40

            # Verify second message (with edits)
            msg2 = stored_messages[1]
            assert msg2.telegram_message_id == 5002
            assert msg2.edited_at is not None
            assert msg2.media_metadata_json["type"] == "document"

            # Verify third message (no author)
            msg3 = stored_messages[2]
            assert msg3.telegram_message_id == 5003
            assert msg3.author_id is None
