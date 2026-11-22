"""Access-control tests for the deduplication endpoints.

The job endpoints and /stats act on every user's messages (and the embeddings
job spends OpenAI credit), so only superusers may call them. The message
listing endpoints are open to any authenticated user, scoped to their own
channels. Redis/arq is mocked: no jobs are actually queued.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

DEDUP_URL = f"{settings.API_V1_STR}/deduplication"

# (method, path, JSON body) for the superuser-only endpoints
SUPERUSER_ENDPOINTS = [
    ("POST", "/embeddings/generate", {"batch_size": 10}),
    ("POST", "/jobs", {"similarity_threshold": 0.9}),
    ("GET", "/stats", None),
]


@pytest.fixture
def mock_create_pool() -> Generator[AsyncMock, None, None]:
    redis = MagicMock()
    redis.enqueue_job = AsyncMock(return_value=MagicMock(job_id="job-123"))
    redis.close = AsyncMock()
    with patch(
        "app.api.routes.deduplication.create_pool",
        AsyncMock(return_value=redis),
    ) as create_pool:
        yield create_pool


@pytest.mark.parametrize(("method", "path", "body"), SUPERUSER_ENDPOINTS)
def test_superuser_endpoints_require_authentication(
    client: TestClient,
    mock_create_pool: AsyncMock,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    r = client.request(method, f"{DEDUP_URL}{path}", json=body)
    assert r.status_code == 401
    mock_create_pool.assert_not_awaited()


@pytest.mark.parametrize(("method", "path", "body"), SUPERUSER_ENDPOINTS)
def test_superuser_endpoints_reject_normal_user(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    mock_create_pool: AsyncMock,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    r = client.request(
        method, f"{DEDUP_URL}{path}", json=body, headers=normal_user_token_headers
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "The user doesn't have enough privileges"
    mock_create_pool.assert_not_awaited()


def test_generate_embeddings_as_superuser(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    mock_create_pool: AsyncMock,
) -> None:
    with patch.object(settings, "OPENAI_API_KEY", "sk-test"):
        r = client.post(
            f"{DEDUP_URL}/embeddings/generate",
            json={"batch_size": 10},
            headers=superuser_token_headers,
        )
    assert r.status_code == 200
    assert r.json()["job_id"] == "job-123"
    assert r.json()["status"] == "queued"
    redis = mock_create_pool.return_value
    redis.enqueue_job.assert_awaited_once_with("generate_embeddings", batch_size=10)


def test_create_deduplication_job_as_superuser(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    mock_create_pool: AsyncMock,
) -> None:
    r = client.post(
        f"{DEDUP_URL}/jobs",
        json={"similarity_threshold": 0.9},
        headers=superuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["job_id"] == "job-123"
    redis = mock_create_pool.return_value
    redis.enqueue_job.assert_awaited_once_with(
        "deduplicate_messages", similarity_threshold=0.9, channel_ids=None
    )


def test_stats_as_superuser(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{DEDUP_URL}/stats", headers=superuser_token_headers)
    assert r.status_code == 200
    data = r.json()
    for key in (
        "total_messages",
        "messages_with_embeddings",
        "dedupe_groups",
        "deduplicated_messages",
        "deduplication_ratio",
    ):
        assert key in data


def test_list_messages_open_to_normal_user(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{DEDUP_URL}/messages", headers=normal_user_token_headers)
    assert r.status_code == 200
    assert "data" in r.json()


def test_list_messages_requires_authentication(client: TestClient) -> None:
    r = client.get(f"{DEDUP_URL}/messages")
    assert r.status_code == 401
