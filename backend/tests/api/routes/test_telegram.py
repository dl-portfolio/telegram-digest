"""Tests for the Telegram login endpoints.

They drive the server's single Telegram session, so only superusers may call
them. The Telegram service is mocked: nothing here talks to Telegram.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

TELEGRAM_URL = f"{settings.API_V1_STR}/telegram"

# (method, path, JSON body) for every Telegram endpoint
ENDPOINTS = [
    ("GET", "/status", None),
    ("POST", "/start", {"phone": "+447700900123"}),
    ("POST", "/verify", {"code": "12345"}),
    ("POST", "/verify-2fa", {"password": "secret"}),
    ("POST", "/logout", None),
]


@pytest.fixture
def mock_telegram_service() -> Generator[AsyncMock, None, None]:
    with patch("app.api.routes.telegram.telegram_service") as service:
        service.is_authenticated = AsyncMock(return_value=True)
        service.start_auth = AsyncMock(
            return_value={
                "status": "code_sent",
                "phone": "+447700900123",
                "message": "Verification code sent",
            }
        )
        service.verify_code = AsyncMock(
            return_value={"status": "authenticated", "user_id": "42"}
        )
        service.verify_2fa = AsyncMock(
            return_value={"status": "authenticated", "user_id": "42"}
        )
        service.logout = AsyncMock(return_value={"status": "logged_out"})
        yield service


def _assert_service_untouched(service: AsyncMock) -> None:
    for name in (
        "is_authenticated",
        "start_auth",
        "verify_code",
        "verify_2fa",
        "logout",
    ):
        getattr(service, name).assert_not_awaited()


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_telegram_endpoints_require_authentication(
    client: TestClient,
    mock_telegram_service: AsyncMock,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    r = client.request(method, f"{TELEGRAM_URL}{path}", json=body)
    assert r.status_code == 401
    _assert_service_untouched(mock_telegram_service)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_telegram_endpoints_reject_invalid_token(
    client: TestClient,
    mock_telegram_service: AsyncMock,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    r = client.request(
        method,
        f"{TELEGRAM_URL}{path}",
        json=body,
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert r.status_code == 403
    _assert_service_untouched(mock_telegram_service)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_telegram_endpoints_reject_normal_user(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    mock_telegram_service: AsyncMock,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    r = client.request(
        method,
        f"{TELEGRAM_URL}{path}",
        json=body,
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "The user doesn't have enough privileges"
    _assert_service_untouched(mock_telegram_service)


def test_telegram_status_as_superuser(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    mock_telegram_service: AsyncMock,
) -> None:
    r = client.get(f"{TELEGRAM_URL}/status", headers=superuser_token_headers)
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    mock_telegram_service.is_authenticated.assert_awaited_once()


def test_telegram_start_as_superuser(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    mock_telegram_service: AsyncMock,
) -> None:
    r = client.post(
        f"{TELEGRAM_URL}/start",
        json={"phone": "+447700900123"},
        headers=superuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json() == {
        "status": "code_sent",
        "phone": "+447700900123",
        "message": "Verification code sent",
    }
    mock_telegram_service.start_auth.assert_awaited_once_with("+447700900123")


def test_telegram_logout_as_superuser(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    mock_telegram_service: AsyncMock,
) -> None:
    r = client.post(f"{TELEGRAM_URL}/logout", headers=superuser_token_headers)
    assert r.status_code == 200
    assert r.json() == {"status": "logged_out"}
    mock_telegram_service.logout.assert_awaited_once()
