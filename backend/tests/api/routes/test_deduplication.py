"""Access-control tests for the deduplication endpoints.

The job endpoints and /stats act on every user's messages (and the embeddings
job spends OpenAI credit), so only superusers may call them. The message
listing endpoints are open to any authenticated user, scoped to their own
channels. Redis/arq is mocked: no jobs are actually queued.
"""

import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, update

from app import crud
from app.core.config import settings
from app.models import (
    Channel,
    DedupeGroup,
    DedupeGroupMember,
    TelegramMessage,
    User,
)

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


# --- Cross-user dedupe groups -------------------------------------------------
#
# Dedupe groups are built across every user's messages, so one group can hold
# messages from several users' channels. Each user must only see (and count)
# the members that live in their own channels.


def _get_user(db: Session, email: str) -> User:
    user = crud.get_user_by_email(session=db, email=email)
    assert user is not None
    return user


@pytest.fixture
def shared_groups(
    db: Session,
    normal_user_token_headers: dict[str, str],  # ensures the normal user exists
) -> Generator[dict[str, Any], None, None]:
    """Two users whose messages share dedupe groups.

    group_a: primary a1 (superuser), members a2 (superuser), b1, b2 (normal)
    group_b: primary b3 (normal), members b4 (normal), a3 (superuser)
    """
    del normal_user_token_headers
    superuser = _get_user(db, settings.FIRST_SUPERUSER)
    normal_user = _get_user(db, settings.EMAIL_TEST_USER)
    suffix = uuid.uuid4().hex[:8]
    chan_a = Channel(
        telegram_identifier=f"dedup_a_{suffix}", name="A", user_id=superuser.id
    )
    chan_b = Channel(
        telegram_identifier=f"dedup_b_{suffix}", name="B", user_id=normal_user.id
    )
    db.add_all([chan_a, chan_b])
    db.commit()

    msgs: dict[str, TelegramMessage] = {}
    for i, (name, channel) in enumerate(
        [
            ("a1", chan_a),
            ("a2", chan_a),
            ("a3", chan_a),
            ("b1", chan_b),
            ("b2", chan_b),
            ("b3", chan_b),
            ("b4", chan_b),
        ]
    ):
        msgs[name] = TelegramMessage(
            channel_id=channel.id,
            telegram_message_id=1000 + i,
            text=f"message {name}",
            timestamp=datetime.utcnow(),
        )
    db.add_all(msgs.values())
    db.commit()

    group_a = DedupeGroup(
        primary_message_id=msgs["a1"].id, confidence_score=0.9, group_size=4
    )
    group_b = DedupeGroup(
        primary_message_id=msgs["b3"].id, confidence_score=0.9, group_size=3
    )
    db.add_all([group_a, group_b])
    db.commit()

    for group, names in (
        (group_a, ["a1", "a2", "b1", "b2"]),
        (group_b, ["b3", "b4", "a3"]),
    ):
        for name in names:
            msg = msgs[name]
            msg.dedupe_group_id = group.id
            db.add(msg)
            db.add(
                DedupeGroupMember(
                    group_id=group.id,
                    message_id=msg.id,
                    similarity_score=0.95,
                    explanation_text=f"explanation for {name}",
                )
            )
    db.commit()

    ids = {name: str(msg.id) for name, msg in msgs.items()}
    yield {"ids": ids, "group_a": str(group_a.id), "group_b": str(group_b.id)}

    msg_ids = [msg.id for msg in msgs.values()]
    group_ids = [group_a.id, group_b.id]
    db.rollback()
    db.execute(
        update(TelegramMessage)
        .where(col(TelegramMessage.id).in_(msg_ids))
        .values(dedupe_group_id=None)
    )
    db.execute(
        delete(DedupeGroupMember).where(col(DedupeGroupMember.group_id).in_(group_ids))
    )
    db.execute(delete(DedupeGroup).where(col(DedupeGroup.id).in_(group_ids)))
    db.execute(delete(TelegramMessage).where(col(TelegramMessage.id).in_(msg_ids)))
    db.execute(delete(Channel).where(col(Channel.id).in_([chan_a.id, chan_b.id])))
    db.commit()


def _listed_counts(client: TestClient, headers: dict[str, str]) -> dict[str, int]:
    r = client.get(f"{DEDUP_URL}/messages", headers=headers)
    assert r.status_code == 200
    return {m["dedupe_group_id"]: m["duplicate_count"] for m in r.json()["data"]}


def _duplicates(
    client: TestClient, headers: dict[str, str], message_id: str
) -> dict[str, Any]:
    r = client.get(f"{DEDUP_URL}/messages/{message_id}/duplicates", headers=headers)
    assert r.status_code == 200
    return r.json()


def test_list_counts_only_own_members(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    shared_groups: dict[str, Any],
) -> None:
    # Superuser: group_a (primary a1) has one other own member, a2; b1/b2 are
    # not counted. group_b's primary is not theirs, so it is not listed.
    su_counts = _listed_counts(client, superuser_token_headers)
    assert su_counts.get(shared_groups["group_a"]) == 1
    assert shared_groups["group_b"] not in su_counts

    # Normal user: group_b (primary b3) has one other own member, b4; a3 is
    # not counted.
    nu_counts = _listed_counts(client, normal_user_token_headers)
    assert nu_counts.get(shared_groups["group_b"]) == 1
    assert shared_groups["group_a"] not in nu_counts


def test_duplicates_only_own_members(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    shared_groups: dict[str, Any],
) -> None:
    ids = shared_groups["ids"]
    superuser_ids = {ids["a1"], ids["a2"], ids["a3"]}
    normal_user_ids = {ids["b1"], ids["b2"], ids["b3"], ids["b4"]}

    cases = [
        (superuser_token_headers, "a1", {ids["a2"]}, normal_user_ids),
        (superuser_token_headers, "a3", set(), normal_user_ids),
        (normal_user_token_headers, "b1", {ids["b2"]}, superuser_ids),
        (normal_user_token_headers, "b3", {ids["b4"]}, superuser_ids),
    ]
    for headers, anchor, expected, foreign in cases:
        data = _duplicates(client, headers, ids[anchor])
        returned = {d["message_id"] for d in data["duplicates"]}
        assert returned == expected, anchor
        assert not returned & foreign, anchor
        assert data["primary_message"]["duplicate_count"] == len(expected), anchor
        for dup in data["duplicates"]:
            assert dup["explanation_text"] in {
                f"explanation for {name}"
                for name, mid in ids.items()
                if mid in expected
            }


def test_duplicates_of_other_users_message_is_404(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    shared_groups: dict[str, Any],
) -> None:
    r = client.get(
        f"{DEDUP_URL}/messages/{shared_groups['ids']['a1']}/duplicates",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404
