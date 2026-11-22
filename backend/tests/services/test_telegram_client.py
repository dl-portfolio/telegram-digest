"""Tests for Telegram client service with mocked Telegram API."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.telegram_client import (
    Telegram2FARequiredError,
    TelegramChannelAccessError,
    TelegramChannelNotFoundError,
    TelegramCodeExpiredError,
    TelegramCodeInvalidError,
    TelegramRateLimitError,
    TelegramService,
)


@pytest.fixture
def telegram_service():
    """Create a fresh TelegramService instance for each test."""
    # Reset singleton for testing
    TelegramService._instance = None
    TelegramService._client = None
    TelegramService._phone_code_hash = None
    TelegramService._pending_phone = None
    return TelegramService()


class TestTelegramAuthentication:
    """Test Telegram authentication flow."""

    @pytest.mark.asyncio
    async def test_is_authenticated_false_when_not_connected(
        self, telegram_service: TelegramService
    ):
        """Test is_authenticated returns False when not connected."""
        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=False)
            mock_get_client.return_value = mock_client

            result = await telegram_service.is_authenticated()
            assert result is False

    @pytest.mark.asyncio
    async def test_is_authenticated_true_when_authorized(
        self, telegram_service: TelegramService
    ):
        """Test is_authenticated returns True when authorized."""
        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_get_client.return_value = mock_client

            result = await telegram_service.is_authenticated()
            assert result is True

    @pytest.mark.asyncio
    async def test_start_auth_sends_code(self, telegram_service: TelegramService):
        """Test start_auth sends verification code."""
        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_sent_code = MagicMock()
            mock_sent_code.phone_code_hash = "test_hash_123"
            mock_client.send_code_request = AsyncMock(return_value=mock_sent_code)
            mock_get_client.return_value = mock_client

            result = await telegram_service.start_auth("+1234567890")

            assert result["status"] == "code_sent"
            assert result["phone"] == "+1234567890"
            assert telegram_service._phone_code_hash == "test_hash_123"
            assert telegram_service._pending_phone == "+1234567890"

    @pytest.mark.asyncio
    async def test_verify_code_success(self, telegram_service: TelegramService):
        """Test successful code verification."""
        telegram_service._pending_phone = "+1234567890"
        telegram_service._phone_code_hash = "test_hash"

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_user = MagicMock()
            mock_user.id = 12345
            mock_user.username = "testuser"
            mock_user.first_name = "Test"
            mock_client.sign_in = AsyncMock(return_value=mock_user)
            mock_get_client.return_value = mock_client

            result = await telegram_service.verify_code("12345")

            assert result["status"] == "authenticated"
            assert result["user_id"] == "12345"
            assert result["username"] == "testuser"
            # Should clear pending auth
            assert telegram_service._pending_phone is None
            assert telegram_service._phone_code_hash is None

    @pytest.mark.asyncio
    async def test_verify_code_expired(self, telegram_service: TelegramService):
        """Test expired verification code."""
        from telethon.errors import PhoneCodeExpiredError

        telegram_service._pending_phone = "+1234567890"
        telegram_service._phone_code_hash = "test_hash"

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.sign_in = AsyncMock(side_effect=PhoneCodeExpiredError(None))
            mock_get_client.return_value = mock_client

            with pytest.raises(TelegramCodeExpiredError):
                await telegram_service.verify_code("12345")

    @pytest.mark.asyncio
    async def test_verify_code_invalid(self, telegram_service: TelegramService):
        """Test invalid verification code."""
        from telethon.errors import PhoneCodeInvalidError

        telegram_service._pending_phone = "+1234567890"
        telegram_service._phone_code_hash = "test_hash"

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.sign_in = AsyncMock(side_effect=PhoneCodeInvalidError(None))
            mock_get_client.return_value = mock_client

            with pytest.raises(TelegramCodeInvalidError):
                await telegram_service.verify_code("12345")

    @pytest.mark.asyncio
    async def test_verify_code_2fa_required(self, telegram_service: TelegramService):
        """Test 2FA required during verification."""
        from telethon.errors import SessionPasswordNeededError

        telegram_service._pending_phone = "+1234567890"
        telegram_service._phone_code_hash = "test_hash"

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.sign_in = AsyncMock(side_effect=SessionPasswordNeededError(None))
            mock_get_client.return_value = mock_client

            with pytest.raises(Telegram2FARequiredError):
                await telegram_service.verify_code("12345")


class TestChannelResolution:
    """Test channel resolution functionality."""

    @pytest.mark.asyncio
    async def test_resolve_channel_success(self, telegram_service: TelegramService):
        """Test successful channel resolution."""
        from telethon.tl.types import Channel as TelegramChannel

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_channel = MagicMock(spec=TelegramChannel)
            mock_channel.id = 123456789
            mock_channel.title = "Test Channel"
            mock_client.get_entity = AsyncMock(return_value=mock_channel)
            mock_get_client.return_value = mock_client

            channel_id, title = await telegram_service.resolve_channel("test_channel")

            assert channel_id == 123456789
            assert title == "Test Channel"

    @pytest.mark.asyncio
    async def test_resolve_channel_strips_at_sign(self, telegram_service: TelegramService):
        """Test that @ prefix is stripped from channel identifier."""
        from telethon.tl.types import Channel as TelegramChannel

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_channel = MagicMock(spec=TelegramChannel)
            mock_channel.id = 123456789
            mock_channel.title = "Test Channel"
            mock_client.get_entity = AsyncMock(return_value=mock_channel)
            mock_get_client.return_value = mock_client

            await telegram_service.resolve_channel("@test_channel")

            # Should call with stripped identifier
            mock_client.get_entity.assert_called_once_with("test_channel")

    @pytest.mark.asyncio
    async def test_resolve_channel_not_found(self, telegram_service: TelegramService):
        """Test channel not found error."""
        from telethon.errors import UsernameNotOccupiedError

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get_entity = AsyncMock(
                side_effect=UsernameNotOccupiedError(None)
            )
            mock_get_client.return_value = mock_client

            with pytest.raises(TelegramChannelNotFoundError):
                await telegram_service.resolve_channel("nonexistent_channel")

    @pytest.mark.asyncio
    async def test_resolve_channel_private(self, telegram_service: TelegramService):
        """Test private channel access error."""
        from telethon.errors import ChannelPrivateError

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get_entity = AsyncMock(side_effect=ChannelPrivateError(None))
            mock_get_client.return_value = mock_client

            with pytest.raises(TelegramChannelAccessError):
                await telegram_service.resolve_channel("private_channel")

    @pytest.mark.asyncio
    async def test_resolve_channel_rate_limited(self, telegram_service: TelegramService):
        """Test rate limit error during channel resolution."""
        from telethon.errors import FloodWaitError

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_error = FloodWaitError(None, 60)
            mock_client.get_entity = AsyncMock(side_effect=mock_error)
            mock_get_client.return_value = mock_client

            with pytest.raises(TelegramRateLimitError) as exc_info:
                await telegram_service.resolve_channel("some_channel")

            assert exc_info.value.wait_seconds == 60


class TestMessageFetching:
    """Test message fetching functionality."""

    @pytest.mark.asyncio
    async def test_fetch_messages_success(self, telegram_service: TelegramService):
        """Test successful message fetching."""
        from telethon.tl.types import Message

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()

            # Mock entity resolution
            mock_entity = MagicMock()
            mock_client.get_entity = AsyncMock(return_value=mock_entity)

            # Create mock messages that pass isinstance check
            mock_messages = []
            for i in range(3):
                msg = MagicMock(spec=Message)
                msg.id = 1000 + i
                msg.text = f"Message {i}"
                msg.date = datetime.utcnow()
                msg.edit_date = None
                msg.sender = MagicMock()
                msg.sender_id = 12345
                msg.sender.username = "test_sender"
                msg.views = 100
                msg.forwards = 10
                msg.reactions = None
                msg.media = None
                mock_messages.append(msg)

            # Mock iter_messages as async generator
            async def mock_iter_messages(*args, **kwargs):
                for msg in mock_messages:
                    yield msg

            mock_client.iter_messages = mock_iter_messages
            mock_get_client.return_value = mock_client

            # Patch isinstance to accept our mocks
            with patch("app.services.telegram_client.Message", Message):
                # Collect messages
                messages = []
                async for msg in telegram_service.fetch_messages("test_channel", limit=10):
                    messages.append(msg)

            assert len(messages) == 3
            assert messages[0]["telegram_message_id"] == 1000
            assert messages[0]["text"] == "Message 0"
            assert messages[0]["author_id"] == 12345

    @pytest.mark.asyncio
    async def test_fetch_messages_with_min_id(self, telegram_service: TelegramService):
        """Test incremental message fetching with min_id."""
        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_entity = MagicMock()
            mock_client.get_entity = AsyncMock(return_value=mock_entity)

            captured_kwargs = {}

            async def mock_iter_messages(*args, **kwargs):
                captured_kwargs.update(kwargs)
                return
                yield  # Make it an async generator

            mock_client.iter_messages = mock_iter_messages
            mock_get_client.return_value = mock_client

            # Consume the generator
            async for _ in telegram_service.fetch_messages(
                "test_channel", min_id=500, limit=10
            ):
                pass

            assert captured_kwargs.get("min_id") == 500
            assert captured_kwargs.get("limit") == 10

    @pytest.mark.asyncio
    async def test_fetch_messages_skips_empty(self, telegram_service: TelegramService):
        """Test that empty messages are skipped."""
        from telethon.tl.types import Message

        with patch.object(telegram_service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_entity = MagicMock()
            mock_client.get_entity = AsyncMock(return_value=mock_entity)

            # Create one normal message and one empty (both must pass isinstance)
            normal_msg = MagicMock(spec=Message)
            normal_msg.id = 1000
            normal_msg.text = "Has content"
            normal_msg.date = datetime.utcnow()
            normal_msg.edit_date = None
            normal_msg.sender = None
            normal_msg.views = None
            normal_msg.forwards = None
            normal_msg.reactions = None
            normal_msg.media = None

            empty_msg = MagicMock(spec=Message)
            empty_msg.id = 1001
            empty_msg.text = None  # No text
            empty_msg.media = None  # No media

            async def mock_iter_messages(*args, **kwargs):
                yield normal_msg
                yield empty_msg

            mock_client.iter_messages = mock_iter_messages
            mock_get_client.return_value = mock_client

            with patch("app.services.telegram_client.Message", Message):
                messages = []
                async for msg in telegram_service.fetch_messages("test_channel", limit=10):
                    messages.append(msg)

            # Should only get the message with content
            assert len(messages) == 1
            assert messages[0]["text"] == "Has content"


class TestMessageParsing:
    """Test message parsing helpers."""

    def test_parse_message_basic(self, telegram_service: TelegramService):
        """Test basic message parsing."""
        msg = MagicMock()
        msg.id = 123
        msg.text = "Test message"
        msg.date = datetime(2026, 1, 1, 12, 0, 0)
        msg.edit_date = None
        msg.sender = MagicMock()
        msg.sender_id = 456
        msg.sender.username = "test_user"
        msg.views = 100
        msg.forwards = 5
        msg.reactions = None
        msg.media = None

        result = telegram_service._parse_message(msg)

        assert result["telegram_message_id"] == 123
        assert result["text"] == "Test message"
        assert result["author_id"] == 456
        assert result["author_username"] == "test_user"
        assert result["view_count"] == 100
        assert result["forward_count"] == 5

    def test_parse_message_with_reactions(self, telegram_service: TelegramService):
        """Test message parsing with reactions."""
        msg = MagicMock()
        msg.id = 123
        msg.text = "Test"
        msg.date = datetime.utcnow()
        msg.edit_date = None
        msg.sender = None
        msg.views = None
        msg.forwards = None
        msg.media = None

        # Mock reactions
        reaction1 = MagicMock()
        reaction1.emoticon = "👍"
        result1 = MagicMock()
        result1.reaction = reaction1
        result1.count = 5

        reaction2 = MagicMock()
        reaction2.emoticon = "❤️"
        result2 = MagicMock()
        result2.reaction = reaction2
        result2.count = 3

        msg.reactions = MagicMock()
        msg.reactions.results = [result1, result2]

        result = telegram_service._parse_message(msg)

        assert result["reactions_json"] is not None
        assert result["reactions_json"]["total"] == 8
        assert len(result["reactions_json"]["reactions"]) == 2

    def test_extract_media_metadata_photo(self, telegram_service: TelegramService):
        """Test photo media metadata extraction."""
        from telethon.tl.types import MessageMediaPhoto

        media = MagicMock(spec=MessageMediaPhoto)
        media.spoiler = False

        result = telegram_service._extract_media_metadata(media)

        assert result["type"] == "photo"
        assert result["has_spoiler"] is False

    def test_extract_media_metadata_document(self, telegram_service: TelegramService):
        """Test document media metadata extraction."""
        from telethon.tl.types import MessageMediaDocument

        media = MagicMock(spec=MessageMediaDocument)
        media.document = MagicMock()
        media.document.mime_type = "application/pdf"
        media.document.size = 1024

        result = telegram_service._extract_media_metadata(media)

        assert result["type"] == "document"
        assert result["mime_type"] == "application/pdf"
        assert result["size"] == 1024

    def test_extract_media_metadata_webpage(self, telegram_service: TelegramService):
        """Test webpage media metadata extraction."""
        from telethon.tl.types import MessageMediaWebPage

        media = MagicMock(spec=MessageMediaWebPage)
        media.webpage = MagicMock()
        media.webpage.url = "https://example.com"
        media.webpage.title = "Example"
        media.webpage.description = "A test page"

        result = telegram_service._extract_media_metadata(media)

        assert result["type"] == "webpage"
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
