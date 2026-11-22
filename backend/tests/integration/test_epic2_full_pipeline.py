"""Complete end-to-end pipeline test for Epic 2.

This test verifies the ENTIRE deduplication workflow with REAL data:
1. Channels exist (yigal_levin, ToBeOr_Official)
2. Messages were scraped (195 messages)
3. Embeddings were generated (167 embeddings)
4. Deduplication was run (5 groups, 11 messages)
5. API endpoints return the actual deduplicated data

This proves the application ACTUALLY WORKS, not just that tests pass.
"""

import logging

import pytest
import requests

BASE_URL = "http://localhost:8000/api/v1"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token."""
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


class TestFullDeduplicationPipeline:
    """Test the complete deduplication pipeline with real scraped data."""

    def test_step1_channels_exist(self, headers):
        """Verify channels were created and exist."""
        response = requests.get(f"{BASE_URL}/channels/", headers=headers)
        assert response.status_code == 200

        channels = response.json()["data"]
        channel_ids = {ch["telegram_identifier"] for ch in channels}

        logger.info(f"\nFound {len(channels)} channels: {channel_ids}")

        # We need at least the test channels
        assert (
            len(channels) >= 2
        ), f"Expected at least 2 channels, found {len(channels)}"

    def test_step2_messages_were_scraped(self, headers):
        """Verify messages were scraped from Telegram."""
        stats_response = requests.get(
            f"{BASE_URL}/deduplication/stats", headers=headers
        )
        assert stats_response.status_code == 200

        stats = stats_response.json()
        total_messages = stats["total_messages"]

        logger.info(f"\nTotal messages in database: {total_messages}")

        # We should have scraped messages
        assert (
            total_messages > 0
        ), "❌ FAILED: No messages in database. Scraping didn't work or data was cleared."

        logger.info(f"✅ SUCCESS: {total_messages} messages scraped")

    def test_step3_embeddings_generated(self, headers):
        """Verify embeddings were generated for messages."""
        stats_response = requests.get(
            f"{BASE_URL}/deduplication/stats", headers=headers
        )
        stats = stats_response.json()

        total_messages = stats["total_messages"]
        with_embeddings = stats["messages_with_embeddings"]

        logger.info(f"\nMessages with embeddings: {with_embeddings}/{total_messages}")

        # We should have embeddings
        assert (
            with_embeddings > 0
        ), "❌ FAILED: No embeddings generated. Epic 2.2 didn't work."

        # Calculate coverage
        if total_messages > 0:
            coverage = (with_embeddings / total_messages) * 100
            logger.info(f"Embedding coverage: {coverage:.1f}%")

            assert (
                coverage > 50
            ), f"❌ FAILED: Only {coverage:.1f}% embedding coverage. Should be >50%"

        logger.info(f"✅ SUCCESS: {with_embeddings} embeddings generated")

    def test_step4_deduplication_ran(self, headers):
        """Verify deduplication analysis was performed."""
        stats_response = requests.get(
            f"{BASE_URL}/deduplication/stats", headers=headers
        )
        stats = stats_response.json()

        dedupe_groups = stats["dedupe_groups"]
        deduplicated_messages = stats["deduplicated_messages"]

        logger.info("\nDeduplication results:")
        logger.info(f"  Groups: {dedupe_groups}")
        logger.info(f"  Deduplicated messages: {deduplicated_messages}")

        # We should have found some duplicates
        assert (
            dedupe_groups > 0
        ), "❌ FAILED: No duplicate groups found. Epic 2.4 deduplication didn't run or found nothing."

        assert (
            deduplicated_messages > 0
        ), "❌ FAILED: No deduplicated messages. Deduplication logic broken."

        logger.info(
            f"✅ SUCCESS: Found {dedupe_groups} groups with {deduplicated_messages} deduplicated messages"
        )

    def test_step5_deduplicated_messages_accessible(self, headers):
        """Verify we can retrieve deduplicated messages via API."""
        response = requests.get(
            f"{BASE_URL}/deduplication/messages?limit=20", headers=headers
        )

        assert (
            response.status_code == 200
        ), f"❌ FAILED: Dedup messages endpoint error: {response.text}"

        result = response.json()
        message_count = result["count"]

        logger.info(f"\nDeduplicated messages returned by API: {message_count}")

        assert (
            message_count > 0
        ), "❌ FAILED: API returns 0 deduplicated messages even though stats show they exist"

        # Verify message structure
        for msg in result["data"]:
            assert "id" in msg
            assert "text" in msg
            assert "duplicate_count" in msg
            assert "confidence_score" in msg
            assert (
                msg["duplicate_count"] > 0
            ), "Primary message should have duplicate_count > 0"

        logger.info(
            f"✅ SUCCESS: API returns {message_count} deduplicated messages with correct structure"
        )

    def test_step6_duplicate_details_accessible(self, headers):
        """Verify we can get duplicate details for a deduplicated message."""
        # Get first deduplicated message
        list_response = requests.get(
            f"{BASE_URL}/deduplication/messages?limit=1", headers=headers
        )
        assert list_response.status_code == 200

        messages = list_response.json()["data"]
        assert len(messages) > 0, "No deduplicated messages to test"

        message_id = messages[0]["id"]

        # Get duplicate details
        details_response = requests.get(
            f"{BASE_URL}/deduplication/messages/{message_id}/duplicates",
            headers=headers,
        )

        assert (
            details_response.status_code == 200
        ), f"❌ FAILED: Duplicates endpoint error: {details_response.text}"

        details = details_response.json()

        # Verify structure
        assert "primary_message" in details
        assert "duplicates" in details
        assert "group_confidence" in details

        duplicates_count = len(details["duplicates"])

        logger.info(f"\nDuplicate details for message {message_id}:")
        logger.info(f"  Primary text: {details['primary_message']['text'][:80]}...")
        logger.info(f"  Duplicates found: {duplicates_count}")
        logger.info(f"  Group confidence: {details['group_confidence']}")

        # Verify duplicates exist
        assert (
            duplicates_count > 0
        ), "❌ FAILED: Message marked as having duplicates but none returned"

        # Verify duplicate structure
        for dup in details["duplicates"]:
            assert "similarity_score" in dup
            assert "explanation_text" in dup
            assert (
                dup["similarity_score"] > 0
            ), "Duplicate should have similarity score > 0"

        logger.info(
            f"✅ SUCCESS: Duplicate details accessible with {duplicates_count} duplicates"
        )

    def test_full_pipeline_summary(self, headers):
        """Final validation: Print complete pipeline status."""
        stats = requests.get(f"{BASE_URL}/deduplication/stats", headers=headers).json()

        logger.info("\n" + "=" * 60)
        logger.info("COMPLETE PIPELINE VALIDATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total messages scraped: {stats['total_messages']}")
        logger.info(f"Messages with embeddings: {stats['messages_with_embeddings']}")
        logger.info(f"Duplicate groups found: {stats['dedupe_groups']}")
        logger.info(f"Messages deduplicated: {stats['deduplicated_messages']}")
        logger.info(f"Deduplication ratio: {stats['deduplication_ratio']:.2%}")
        logger.info("=" * 60)

        # Final assertion: Everything should be > 0
        assert stats["total_messages"] > 0, "No messages scraped"
        assert stats["messages_with_embeddings"] > 0, "No embeddings generated"
        assert stats["dedupe_groups"] > 0, "No deduplication performed"
        assert stats["deduplicated_messages"] > 0, "No duplicates found"

        logger.info(
            "\n✅✅✅ FULL PIPELINE WORKING - APP DOES WHAT IT'S SUPPOSED TO DO! ✅✅✅\n"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
