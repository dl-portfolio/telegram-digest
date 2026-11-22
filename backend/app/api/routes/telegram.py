"""Telegram authentication API endpoints."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.services.telegram_client import (
    Telegram2FARequiredError,
    TelegramAuthError,
    TelegramCodeExpiredError,
    TelegramCodeInvalidError,
    telegram_service,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])


class PhoneRequest(BaseModel):
    """Request to start Telegram authentication."""

    phone: str


class CodeVerifyRequest(BaseModel):
    """Request to verify Telegram authentication code."""

    code: str


class PasswordVerifyRequest(BaseModel):
    """Request to verify 2FA password."""

    password: str


class AuthStatusResponse(BaseModel):
    """Response for authentication status."""

    authenticated: bool
    user_id: str | None = None
    username: str | None = None
    first_name: str | None = None


class AuthStartResponse(BaseModel):
    """Response for authentication start."""

    status: str
    phone: str
    message: str


class AuthVerifyResponse(BaseModel):
    """Response for authentication verification."""

    status: str
    user_id: str | None = None
    username: str | None = None
    first_name: str | None = None
    requires_2fa: bool = False


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status() -> AuthStatusResponse:
    """Check if Telegram is authenticated."""
    try:
        authenticated = await telegram_service.is_authenticated()
        return AuthStatusResponse(authenticated=authenticated)
    except TelegramAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


@router.post("/start", response_model=AuthStartResponse)
async def start_auth(request: PhoneRequest) -> AuthStartResponse:
    """Start Telegram authentication flow.

    Sends a verification code to the provided phone number.
    """
    try:
        result = await telegram_service.start_auth(request.phone)
        return AuthStartResponse(**result)
    except TelegramAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start authentication: {e}",
        )


@router.post("/verify", response_model=AuthVerifyResponse)
async def verify_code(request: CodeVerifyRequest) -> AuthVerifyResponse:
    """Verify the Telegram authentication code.

    If 2FA is enabled, returns requires_2fa=True and you must call /verify-2fa.
    """
    try:
        result = await telegram_service.verify_code(request.code)
        return AuthVerifyResponse(**result)
    except TelegramCodeExpiredError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except TelegramCodeInvalidError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Telegram2FARequiredError:
        return AuthVerifyResponse(
            status="2fa_required",
            requires_2fa=True,
        )
    except TelegramAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/verify-2fa", response_model=AuthVerifyResponse)
async def verify_2fa(request: PasswordVerifyRequest) -> AuthVerifyResponse:
    """Verify 2FA password for Telegram authentication."""
    try:
        result = await telegram_service.verify_2fa(request.password)
        return AuthVerifyResponse(**result)
    except TelegramAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to verify 2FA: {e}",
        )


@router.post("/logout")
async def logout() -> dict[str, str]:
    """Log out from Telegram."""
    return await telegram_service.logout()
