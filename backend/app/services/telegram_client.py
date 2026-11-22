"""Telegram client service for authentication and channel scraping."""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

import structlog
from telethon import TelegramClient
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import (
    Channel as TelegramChannel,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

from app.core.config import settings

if TYPE_CHECKING:
    from telethon.tl.types import User

log = structlog.get_logger()


class TelegramAuthError(Exception):
    """Base exception for Telegram authentication errors."""

    pass


class TelegramCodeExpiredError(TelegramAuthError):
    """Raised when the verification code has expired."""

    pass


class TelegramCodeInvalidError(TelegramAuthError):
    """Raised when the verification code is invalid."""

    pass


class Telegram2FARequiredError(TelegramAuthError):
    """Raised when 2FA password is required."""

    pass


class TelegramChannelError(Exception):
    """Base exception for Telegram channel errors."""

    pass


class TelegramChannelNotFoundError(TelegramChannelError):
    """Raised when a channel cannot be found."""

    pass


class TelegramChannelAccessError(TelegramChannelError):
    """Raised when access to a channel is denied."""

    pass


class TelegramRateLimitError(TelegramChannelError):
    """Raised when rate limited by Telegram."""

    def __init__(self, message: str, wait_seconds: int):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class TelegramService:
    """Service for interacting with Telegram API via Telethon."""

    _instance: "TelegramService | None" = None
    _client: TelegramClient | None = None
    _phone_code_hash: str | None = None
    _pending_phone: str | None = None

    def __new__(cls) -> "TelegramService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def session_path(self) -> Path:
        """Get the path for storing Telegram session files."""
        path = Path(settings.TELEGRAM_SESSION_PATH)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def session_file(self) -> str:
        """Get the session file path (without extension)."""
        return str(self.session_path / "telegram_session")

    async def get_client(self) -> TelegramClient:
        """Get or create the Telegram client."""
        if not settings.telegram_configured:
            raise TelegramAuthError(
                "Telegram API credentials not configured. "
                "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in environment."
            )

        if self._client is None:
            self._client = TelegramClient(
                self.session_file,
                settings.TELEGRAM_API_ID,  # type: ignore
                settings.TELEGRAM_API_HASH,  # type: ignore
            )

        if not self._client.is_connected():
            await self._client.connect()

        return self._client

    async def is_authenticated(self) -> bool:
        """Check if the client is authenticated with Telegram."""
        try:
            client = await self.get_client()
            return await client.is_user_authorized()
        except Exception:
            return False

    async def start_auth(self, phone: str) -> dict[str, str]:
        """Start the authentication flow by sending a code to the phone.

        Args:
            phone: Phone number with country code (e.g., +1234567890)

        Returns:
            dict with status and phone_code_hash
        """
        client = await self.get_client()

        # Send code request
        sent_code = await client.send_code_request(phone)
        self._phone_code_hash = sent_code.phone_code_hash
        self._pending_phone = phone

        return {
            "status": "code_sent",
            "phone": phone,
            "message": "Verification code sent to your Telegram app",
        }

    async def verify_code(self, code: str) -> dict[str, str]:
        """Verify the authentication code.

        Args:
            code: The verification code received on Telegram

        Returns:
            dict with authentication status

        Raises:
            TelegramCodeExpiredError: If the code has expired
            TelegramCodeInvalidError: If the code is invalid
            Telegram2FARequiredError: If 2FA password is required
        """
        if not self._pending_phone or not self._phone_code_hash:
            raise TelegramAuthError("No pending authentication. Call start_auth first.")

        client = await self.get_client()

        try:
            user: User = await client.sign_in(
                phone=self._pending_phone,
                code=code,
                phone_code_hash=self._phone_code_hash,
            )
            self._clear_pending_auth()
            return {
                "status": "authenticated",
                "user_id": str(user.id),
                "username": user.username or "",
                "first_name": user.first_name or "",
            }
        except PhoneCodeExpiredError:
            self._clear_pending_auth()
            raise TelegramCodeExpiredError("Verification code has expired. Request a new one.")
        except PhoneCodeInvalidError:
            raise TelegramCodeInvalidError("Invalid verification code. Please try again.")
        except SessionPasswordNeededError:
            raise Telegram2FARequiredError("Two-factor authentication is enabled. Password required.")

    async def verify_2fa(self, password: str) -> dict[str, str]:
        """Verify 2FA password.

        Args:
            password: The 2FA password

        Returns:
            dict with authentication status
        """
        client = await self.get_client()

        user: User = await client.sign_in(password=password)
        self._clear_pending_auth()

        return {
            "status": "authenticated",
            "user_id": str(user.id),
            "username": user.username or "",
            "first_name": user.first_name or "",
        }

    async def logout(self) -> dict[str, str]:
        """Log out from Telegram and remove session."""
        try:
            if self._client and self._client.is_connected():
                await self._client.log_out()

            # Remove session file
            session_file = Path(f"{self.session_file}.session")
            if session_file.exists():
                session_file.unlink()

            self._client = None
            self._clear_pending_auth()

            return {"status": "logged_out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _clear_pending_auth(self) -> None:
        """Clear pending authentication state."""
        self._phone_code_hash = None
        self._pending_phone = None

    async def disconnect(self) -> None:
        """Disconnect the client."""
        if self._client and self._client.is_connected():
            await self._client.disconnect()

    async def resolve_channel(self, identifier: str) -> tuple[int, str]:
        """Resolve a channel identifier to its ID and title.

        Args:
            identifier: Channel username (with or without @) or invite link

        Returns:
            Tuple of (channel_id, channel_title)

        Raises:
            TelegramChannelNotFoundError: If channel cannot be found
            TelegramChannelAccessError: If access is denied
            TelegramRateLimitError: If rate limited
        """
        client = await self.get_client()

        # Clean up identifier
        identifier = identifier.strip()
        if identifier.startswith("@"):
            identifier = identifier[1:]

        try:
            entity = await client.get_entity(identifier)

            if isinstance(entity, TelegramChannel):
                return entity.id, entity.title
            else:
                raise TelegramChannelNotFoundError(
                    f"'{identifier}' is not a channel or group"
                )

        except (UsernameNotOccupiedError, UsernameInvalidError):
            raise TelegramChannelNotFoundError(
                f"Channel '{identifier}' not found"
            )
        except ChannelPrivateError:
            raise TelegramChannelAccessError(
                f"Cannot access private channel '{identifier}'"
            )
        except ChannelInvalidError:
            raise TelegramChannelNotFoundError(
                f"Channel '{identifier}' is invalid"
            )
        except FloodWaitError as e:
            raise TelegramRateLimitError(
                f"Rate limited. Please wait {e.seconds} seconds.",
                wait_seconds=e.seconds,
            )

    async def fetch_messages(
        self,
        channel_identifier: str,
        min_id: int | None = None,
        limit: int = 100,
    ) -> AsyncIterator[dict]:
        """Fetch messages from a Telegram channel.

        Args:
            channel_identifier: Channel username or ID
            min_id: Only fetch messages with ID greater than this (for incremental)
            limit: Maximum number of messages to fetch

        Yields:
            Message data dictionaries

        Raises:
            TelegramChannelNotFoundError: If channel cannot be found
            TelegramChannelAccessError: If access is denied
            TelegramRateLimitError: If rate limited
        """
        client = await self.get_client()

        try:
            entity = await client.get_entity(channel_identifier)
        except (UsernameNotOccupiedError, UsernameInvalidError):
            raise TelegramChannelNotFoundError(
                f"Channel '{channel_identifier}' not found"
            )
        except ChannelPrivateError:
            raise TelegramChannelAccessError(
                f"Cannot access private channel '{channel_identifier}'"
            )
        except FloodWaitError as e:
            raise TelegramRateLimitError(
                f"Rate limited. Please wait {e.seconds} seconds.",
                wait_seconds=e.seconds,
            )

        try:
            async for message in client.iter_messages(
                entity,
                limit=limit,
                min_id=min_id or 0,
                reverse=True,  # Oldest first when using min_id
            ):
                if not isinstance(message, Message):
                    continue

                # Skip empty messages (service messages without text)
                if not message.text and not message.media:
                    continue

                yield self._parse_message(message)

        except FloodWaitError as e:
            await log.awarning(
                "Rate limited during message fetch",
                wait_seconds=e.seconds,
                channel=channel_identifier,
            )
            raise TelegramRateLimitError(
                f"Rate limited. Please wait {e.seconds} seconds.",
                wait_seconds=e.seconds,
            )

    def _parse_message(self, message: Message) -> dict:
        """Parse a Telegram message into a dictionary.

        Args:
            message: Telethon Message object

        Returns:
            Dictionary with message data
        """
        # Extract media metadata
        media_metadata = None
        if message.media:
            media_metadata = self._extract_media_metadata(message.media)

        # Extract reactions if available
        reactions = None
        if message.reactions:
            reactions = self._extract_reactions(message.reactions)

        # Get sender info
        author_id = None
        author_username = None
        if message.sender:
            author_id = message.sender_id
            if hasattr(message.sender, "username"):
                author_username = message.sender.username

        return {
            "telegram_message_id": message.id,
            "text": message.text or "",
            "timestamp": message.date,
            "edited_at": message.edit_date,
            "author_id": author_id,
            "author_username": author_username,
            "view_count": message.views,
            "forward_count": message.forwards,
            "reactions_json": reactions,
            "media_metadata_json": media_metadata,
        }

    def _extract_media_metadata(self, media) -> dict | None:
        """Extract metadata from message media."""
        if isinstance(media, MessageMediaPhoto):
            return {
                "type": "photo",
                "has_spoiler": getattr(media, "spoiler", False),
            }
        elif isinstance(media, MessageMediaDocument):
            doc = media.document
            if doc:
                return {
                    "type": "document",
                    "mime_type": doc.mime_type,
                    "size": doc.size,
                }
        elif isinstance(media, MessageMediaWebPage):
            if media.webpage and hasattr(media.webpage, "url"):
                return {
                    "type": "webpage",
                    "url": media.webpage.url,
                    "title": getattr(media.webpage, "title", None),
                    "description": getattr(media.webpage, "description", None),
                }
        return None

    def _extract_reactions(self, reactions) -> dict | None:
        """Extract reaction data from message reactions."""
        if not reactions or not reactions.results:
            return None

        reaction_data = []
        for result in reactions.results:
            reaction = result.reaction
            # Handle different reaction types
            if hasattr(reaction, "emoticon"):
                reaction_data.append({
                    "emoticon": reaction.emoticon,
                    "count": result.count,
                })
            elif hasattr(reaction, "document_id"):
                reaction_data.append({
                    "custom_emoji_id": reaction.document_id,
                    "count": result.count,
                })

        return {"reactions": reaction_data, "total": sum(r["count"] for r in reaction_data)}


# Singleton instance
telegram_service = TelegramService()
