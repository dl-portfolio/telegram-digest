"""Integration tests for Epic 1: Channel Management.

These tests verify the complete channel management flow works in Docker:
1. Create channels via API
2. Verify database state
3. List, update, delete channels
4. Validate all business rules (3 channel limit, duplicates, etc.)
"""

import pytest
import requests

# Docker API base URL
BASE_URL = "http://localhost:8000/api/v1"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token from running Docker environment."""
    response = requests.post(
        f"{BASE_URL}/login/access-token",
        data={"username": "admin@example.com", "password": "changethis"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, f"Auth failed: {response.text}"
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def headers(auth_token):
    """Get authorization headers."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(autouse=True)
def cleanup_channels(headers):
    """Clean up all channels before each test."""
    # Get all channels
    response = requests.get(f"{BASE_URL}/channels/", headers=headers)
    if response.status_code == 200:
        channels = response.json().get("data", [])
        # Delete each channel
        for channel in channels:
            requests.delete(f"{BASE_URL}/channels/{channel['id']}", headers=headers)
    yield
    # Cleanup after test as well
    response = requests.get(f"{BASE_URL}/channels/", headers=headers)
    if response.status_code == 200:
        channels = response.json().get("data", [])
        for channel in channels:
            requests.delete(f"{BASE_URL}/channels/{channel['id']}", headers=headers)


class TestEpic1ChannelManagement:
    """Integration tests for complete channel management workflow."""

    def test_create_channel_full_flow(self, headers):
        """Test creating a channel and verify it exists in database."""
        # Create channel
        channel_data = {
            "telegram_identifier": "test_integration_channel",
            "name": "Integration Test Channel",
            "category": "tech",
        }

        response = requests.post(
            f"{BASE_URL}/channels/", json=channel_data, headers=headers
        )

        assert response.status_code == 201, f"Failed to create channel: {response.text}"
        result = response.json()

        # Verify response structure
        assert result["telegram_identifier"] == "test_integration_channel"
        assert result["name"] == "Integration Test Channel"
        assert result["category"] == "tech"
        assert result["is_active"] is True
        assert "id" in result
        channel_id = result["id"]

        # Verify channel appears in list
        list_response = requests.get(f"{BASE_URL}/channels/", headers=headers)
        assert list_response.status_code == 200
        channels = list_response.json()["data"]
        assert len(channels) == 1
        assert channels[0]["id"] == channel_id

    def test_channel_limit_enforcement(self, headers):
        """Test that 3-channel limit is enforced."""
        # Create 3 channels
        for i in range(3):
            response = requests.post(
                f"{BASE_URL}/channels/",
                json={"telegram_identifier": f"limit_test_{i}"},
                headers=headers,
            )
            assert response.status_code == 201, f"Failed to create channel {i}"

        # Try to create 4th channel
        response = requests.post(
            f"{BASE_URL}/channels/",
            json={"telegram_identifier": "limit_test_4"},
            headers=headers,
        )

        assert response.status_code == 400
        assert "Maximum of 3 channels" in response.json()["detail"]

    def test_duplicate_channel_prevention(self, headers):
        """Test that duplicate channels are prevented."""
        channel_data = {"telegram_identifier": "duplicate_test"}

        # Create first channel
        response = requests.post(
            f"{BASE_URL}/channels/", json=channel_data, headers=headers
        )
        assert response.status_code == 201

        # Try to create duplicate
        response = requests.post(
            f"{BASE_URL}/channels/", json=channel_data, headers=headers
        )

        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_update_channel_flow(self, headers):
        """Test updating channel properties."""
        # Create channel
        response = requests.post(
            f"{BASE_URL}/channels/",
            json={"telegram_identifier": "update_test", "category": "tech"},
            headers=headers,
        )
        channel_id = response.json()["id"]

        # Update channel
        update_response = requests.patch(
            f"{BASE_URL}/channels/{channel_id}",
            json={"category": "security", "is_active": False},
            headers=headers,
        )

        assert update_response.status_code == 200
        result = update_response.json()
        assert result["category"] == "security"
        assert result["is_active"] is False

    def test_delete_channel_flow(self, headers):
        """Test deleting a channel."""
        # Create channel
        response = requests.post(
            f"{BASE_URL}/channels/",
            json={"telegram_identifier": "delete_test"},
            headers=headers,
        )
        channel_id = response.json()["id"]

        # Delete channel
        delete_response = requests.delete(
            f"{BASE_URL}/channels/{channel_id}", headers=headers
        )
        assert delete_response.status_code == 204

        # Verify channel is gone
        get_response = requests.get(
            f"{BASE_URL}/channels/{channel_id}", headers=headers
        )
        assert get_response.status_code == 404

    def test_unauthenticated_access_denied(self):
        """Test that unauthenticated requests are rejected."""
        response = requests.post(
            f"{BASE_URL}/channels/", json={"telegram_identifier": "unauth_test"}
        )
        assert response.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
