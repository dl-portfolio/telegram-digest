"""Tests for message retrieval API endpoints."""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.models import Channel, TelegramMessage


@pytest.fixture(autouse=True)
def cleanup_data(db: Session):
    """Clean up test data before and after each test."""
    # Clean before test
    db.execute(delete(TelegramMessage))
    db.execute(delete(Channel))
    db.commit()
    yield
    # Clean after test
    db.execute(delete(TelegramMessage))
    db.execute(delete(Channel))
    db.commit()


@pytest.fixture
def test_channel(db: Session, superuser_token_headers: dict[str, str], client: TestClient) -> Channel:
    """Create a test channel for message tests."""
    # Create channel via API to get proper user association
    data = {"telegram_identifier": "test_messages_channel", "name": "Test Messages Channel"}
    r = client.post(
        f"{settings.API_V1_STR}/channels/",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 201
    channel_data = r.json()

    # Fetch the actual channel object from DB
    channel = db.get(Channel, uuid.UUID(channel_data["id"]))
    return channel


@pytest.fixture
def test_messages(db: Session, test_channel: Channel) -> list[TelegramMessage]:
    """Create test messages for the test channel."""
    now = datetime.utcnow()
    messages = []

    for i in range(5):
        msg = TelegramMessage(
            channel_id=test_channel.id,
            telegram_message_id=1000 + i,
            text=f"Test message {i} with content about security and OSINT",
            timestamp=now - timedelta(hours=i),
            author_id=12345678,
            author_username="test_author",
            view_count=100 * (i + 1),
            reactions_json={"reactions": [{"emoticon": "👍", "count": i + 1}]},
        )
        db.add(msg)
        messages.append(msg)

    db.commit()
    for msg in messages:
        db.refresh(msg)

    return messages


class TestMessageListing:
    """Test message listing endpoints."""

    def test_list_messages_empty(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test listing messages when none exist."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 0
        assert result["data"] == []

    def test_list_messages(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test listing all messages."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 5
        assert len(result["data"]) == 5
        # Should be ordered by timestamp desc (newest first)
        assert result["data"][0]["telegram_message_id"] == 1000  # Most recent

    def test_list_messages_pagination(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test message pagination."""
        # Get first page
        r = client.get(
            f"{settings.API_V1_STR}/messages/?limit=2",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 5  # Total count
        assert len(result["data"]) == 2  # Page size

        # Get second page
        r = client.get(
            f"{settings.API_V1_STR}/messages/?skip=2&limit=2",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert len(result["data"]) == 2

    def test_list_messages_filter_by_channel(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_channel: Channel,
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test filtering messages by channel."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/?channel_id={test_channel.id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 5

    def test_list_messages_filter_nonexistent_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test filtering by non-existent channel returns 404."""
        fake_id = str(uuid.uuid4())
        r = client.get(
            f"{settings.API_V1_STR}/messages/?channel_id={fake_id}",
            headers=superuser_token_headers,
        )
        # API returns 404 for channels that don't belong to the user
        # or returns 200 with empty results if using empty user_channels check
        assert r.status_code in [200, 404]
        if r.status_code == 200:
            result = r.json()
            assert result["count"] == 0

    def test_list_messages_time_filter_since(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test filtering messages by since timestamp."""
        # Get messages from last 2.5 hours to account for timing
        since = (datetime.utcnow() - timedelta(hours=2, minutes=30)).isoformat()
        r = client.get(
            f"{settings.API_V1_STR}/messages/?since={since}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        # Should get messages at 0, 1, 2 hours ago = 3 messages
        assert result["count"] == 3

    def test_list_messages_time_filter_until(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test filtering messages by until timestamp."""
        until = (datetime.utcnow() - timedelta(hours=3)).isoformat()
        r = client.get(
            f"{settings.API_V1_STR}/messages/?until={until}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        # Should get messages at 3, 4 hours ago = 2 messages
        assert result["count"] == 2

    def test_list_messages_search(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test searching message text."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/?search=security",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 5  # All messages contain "security"

    def test_list_messages_unauthenticated(self, client: TestClient) -> None:
        """Test that unauthenticated requests are rejected."""
        r = client.get(f"{settings.API_V1_STR}/messages/")
        assert r.status_code == 401


class TestMessageRetrieval:
    """Test single message retrieval."""

    def test_get_message(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test getting a specific message."""
        message = test_messages[0]
        r = client.get(
            f"{settings.API_V1_STR}/messages/{message.id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["telegram_message_id"] == message.telegram_message_id
        assert result["text"] == message.text
        assert result["view_count"] == message.view_count
        assert "reactions_json" in result

    def test_get_message_not_found(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test getting a non-existent message."""
        fake_id = str(uuid.uuid4())
        r = client.get(
            f"{settings.API_V1_STR}/messages/{fake_id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 404


class TestChannelMessages:
    """Test channel-specific message endpoints."""

    def test_list_channel_messages(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_channel: Channel,
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test listing messages for a specific channel."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/channel/{test_channel.id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 5

    def test_list_channel_messages_not_found(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test listing messages for non-existent channel."""
        fake_id = str(uuid.uuid4())
        r = client.get(
            f"{settings.API_V1_STR}/messages/channel/{fake_id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 404

    def test_list_channel_messages_with_since(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_channel: Channel,
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test time filtering on channel messages."""
        # Use 1.5 hours to account for timing
        since = (datetime.utcnow() - timedelta(hours=1, minutes=30)).isoformat()
        r = client.get(
            f"{settings.API_V1_STR}/messages/channel/{test_channel.id}?since={since}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        # Should get messages at 0, 1 hours ago = 2 messages
        assert result["count"] == 2


class TestMessageStats:
    """Test message statistics endpoint."""

    def test_get_stats_empty(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test stats with no messages."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/stats/summary",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total_messages"] == 0
        assert result["channels_count"] == 0

    def test_get_stats_with_messages(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        test_channel: Channel,
        test_messages: list[TelegramMessage],
    ) -> None:
        """Test stats with messages."""
        r = client.get(
            f"{settings.API_V1_STR}/messages/stats/summary",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total_messages"] == 5
        assert result["channels_count"] == 1
        assert result["oldest_message"] is not None
        assert result["newest_message"] is not None
        assert str(test_channel.id) in result["messages_per_channel"]
