"""Tests for channel management API endpoints."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.models import Channel


@pytest.fixture(autouse=True)
def cleanup_channels(db: Session):
    """Clean up channels before and after each test."""
    # Clean before test
    statement = delete(Channel)
    db.execute(statement)
    db.commit()
    yield
    # Clean after test
    statement = delete(Channel)
    db.execute(statement)
    db.commit()


class TestChannelCRUD:
    """Test channel CRUD operations."""

    def test_create_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str], db: Session
    ) -> None:
        """Test creating a new channel."""
        data = {
            "telegram_identifier": "test_channel",
            "name": "Test Channel",
            "category": "security",
        }
        r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["telegram_identifier"] == "test_channel"
        assert result["name"] == "Test Channel"
        assert result["category"] == "security"
        assert result["is_active"] is True
        assert "id" in result
        assert "user_id" in result

    def test_create_channel_default_category(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test creating a channel with default category."""
        data = {"telegram_identifier": "another_channel"}
        r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["category"] == "other"

    def test_create_duplicate_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test creating a duplicate channel returns error."""
        data = {"telegram_identifier": "duplicate_channel"}
        # Create first
        r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        assert r.status_code == 201

        # Try to create duplicate
        r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"]

    def test_max_channels_limit(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test that user cannot create more than 3 channels (MVP limit)."""
        # Create 3 channels
        for i in range(3):
            data = {"telegram_identifier": f"channel_{i}"}
            r = client.post(
                f"{settings.API_V1_STR}/channels/",
                headers=superuser_token_headers,
                json=data,
            )
            assert r.status_code == 201

        # Try to create 4th channel
        data = {"telegram_identifier": "channel_4"}
        r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        assert r.status_code == 400
        assert "Maximum of 3 channels" in r.json()["detail"]

    def test_list_channels(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test listing channels."""
        # Create channels
        for i in range(2):
            data = {"telegram_identifier": f"list_channel_{i}"}
            client.post(
                f"{settings.API_V1_STR}/channels/",
                headers=superuser_token_headers,
                json=data,
            )

        r = client.get(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["count"] == 2
        assert len(result["data"]) == 2

    def test_get_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test getting a specific channel."""
        # Create channel
        data = {"telegram_identifier": "get_channel"}
        create_r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        channel_id = create_r.json()["id"]

        # Get channel
        r = client.get(
            f"{settings.API_V1_STR}/channels/{channel_id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["telegram_identifier"] == "get_channel"

    def test_get_nonexistent_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test getting a channel that doesn't exist returns 404."""
        fake_id = str(uuid.uuid4())
        r = client.get(
            f"{settings.API_V1_STR}/channels/{fake_id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 404

    def test_update_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Test updating a channel."""
        # Create channel
        data = {"telegram_identifier": "update_channel", "category": "tech"}
        create_r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        channel_id = create_r.json()["id"]

        # Update channel
        update_data = {"category": "security", "is_active": False}
        r = client.patch(
            f"{settings.API_V1_STR}/channels/{channel_id}",
            headers=superuser_token_headers,
            json=update_data,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["category"] == "security"
        assert result["is_active"] is False

    def test_delete_channel(
        self, client: TestClient, superuser_token_headers: dict[str, str], db: Session
    ) -> None:
        """Test deleting a channel."""
        # Create channel
        data = {"telegram_identifier": "delete_channel"}
        create_r = client.post(
            f"{settings.API_V1_STR}/channels/",
            headers=superuser_token_headers,
            json=data,
        )
        channel_id = create_r.json()["id"]

        # Delete channel
        r = client.delete(
            f"{settings.API_V1_STR}/channels/{channel_id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 204

        # Verify deleted
        r = client.get(
            f"{settings.API_V1_STR}/channels/{channel_id}",
            headers=superuser_token_headers,
        )
        assert r.status_code == 404

    def test_create_channel_unauthenticated(self, client: TestClient) -> None:
        """Test that unauthenticated requests are rejected."""
        data = {"telegram_identifier": "unauth_channel"}
        r = client.post(f"{settings.API_V1_STR}/channels/", json=data)
        assert r.status_code == 401
