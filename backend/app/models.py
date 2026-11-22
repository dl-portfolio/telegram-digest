import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from pydantic import EmailStr
from sqlalchemy import JSON, BigInteger
from sqlmodel import Column, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models import DedupeGroup


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# ============================================================================
# Channel Models (Story 1.3)
# ============================================================================


class ChannelCategory(str, Enum):
    """Categories for Telegram channels."""

    SECURITY = "security"
    TECH = "tech"
    GEOPOLITICS = "geopolitics"
    CRYPTO = "crypto"
    AI_ML = "ai_ml"
    OTHER = "other"


class ChannelBase(SQLModel):
    """Base properties for a Telegram channel."""

    telegram_identifier: str = Field(
        max_length=255,
        description="Telegram channel username or ID",
    )
    name: str | None = Field(
        default=None,
        max_length=255,
        description="Display name for the channel",
    )
    category: ChannelCategory = Field(
        default=ChannelCategory.OTHER,
        description="Category of the channel",
    )
    is_active: bool = Field(
        default=True,
        description="Whether to include this channel in scraping",
    )


class ChannelCreate(ChannelBase):
    """Properties to receive on channel creation."""

    pass


class ChannelUpdate(SQLModel):
    """Properties to receive on channel update."""

    name: str | None = Field(default=None, max_length=255)
    category: ChannelCategory | None = None
    is_active: bool | None = None


class Channel(ChannelBase, table=True):
    """Database model for Telegram channels."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, ondelete="CASCADE")
    telegram_id: int | None = Field(
        default=None,
        description="Telegram's internal channel ID (resolved from identifier)",
    )
    last_scraped_at: datetime | None = Field(
        default=None,
        description="When this channel was last successfully scraped",
    )
    last_scraped_message_id: int | None = Field(
        default=None,
        description="ID of the last scraped message for incremental scraping",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    user: User | None = Relationship()
    messages: list["TelegramMessage"] = Relationship(
        back_populates="channel", cascade_delete=True
    )


class ChannelPublic(ChannelBase):
    """Properties to return via API."""

    id: uuid.UUID
    user_id: uuid.UUID
    telegram_id: int | None = None
    last_scraped_at: datetime | None = None
    last_scraped_message_id: int | None = None
    created_at: datetime


class ChannelsPublic(SQLModel):
    """List of channels with count."""

    data: list[ChannelPublic]
    count: int


# ============================================================================
# Message Models (Story 1.4)
# ============================================================================


class TelegramMessageBase(SQLModel):
    """Base properties for a Telegram message."""

    telegram_message_id: int = Field(
        description="Telegram's message ID within the channel",
    )
    text: str | None = Field(
        default=None,
        description="Message text content",
    )
    timestamp: datetime = Field(
        description="When the message was posted on Telegram",
    )
    edited_at: datetime | None = Field(
        default=None,
        description="When the message was last edited",
    )
    author_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
        description="Telegram user ID of the message author",
    )
    author_username: str | None = Field(
        default=None,
        max_length=255,
        description="Username of the message author",
    )
    view_count: int | None = Field(
        default=None,
        description="Number of views",
    )
    forward_count: int | None = Field(
        default=None,
        description="Number of forwards",
    )


class TelegramMessage(TelegramMessageBase, table=True):
    """Database model for Telegram messages."""

    __tablename__ = "telegram_message"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    channel_id: uuid.UUID = Field(
        foreign_key="channel.id", nullable=False, ondelete="CASCADE"
    )

    # JSON fields for flexible data
    reactions_json: dict | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="Reactions data as JSON",
    )
    media_metadata_json: dict | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="Media metadata (type, size, dimensions) as JSON",
    )

    # Timestamps
    scraped_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this message was collected",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this record was created in our database",
    )

    # Embeddings (Story 2.2)
    # Using pgvector's Vector type via SQLAlchemy Column
    # Type annotation is Any because Python doesn't have native vector type
    embedding: Any = Field(
        default=None,
        sa_column=Column(Vector(768), nullable=True),
        description="768-dimensional embedding vector for semantic similarity",
    )
    embedding_generated_at: datetime | None = Field(
        default=None,
        description="When the embedding was generated",
    )
    embedding_model: str | None = Field(
        default=None,
        max_length=100,
        description="Model used to generate embedding (e.g., text-embedding-3-small)",
    )

    # Deduplication (Story 2.3)
    dedupe_group_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="dedupe_group.id",
        description="ID of the dedupe group this message belongs to",
    )

    # Relationships
    channel: Channel | None = Relationship(back_populates="messages")
    dedupe_group: "DedupeGroup" = Relationship(
        back_populates="members",
        sa_relationship_kwargs={
            "foreign_keys": "[TelegramMessage.dedupe_group_id]"
        }
    )


class TelegramMessagePublic(TelegramMessageBase):
    """Properties to return via API."""

    id: uuid.UUID
    channel_id: uuid.UUID
    reactions_json: dict | None = None
    media_metadata_json: dict | None = None
    scraped_at: datetime
    created_at: datetime


class TelegramMessagesPublic(SQLModel):
    """List of messages with count and pagination info."""

    data: list[TelegramMessagePublic]
    count: int


# ============================================================================
# Deduplication Models (Story 2.2, 2.3, 2.4)
# ============================================================================


class DedupeGroupBase(SQLModel):
    """Base properties for a deduplication group."""

    confidence_score: float = Field(
        description="Overall confidence score for this group (0-1)",
    )
    group_size: int = Field(
        description="Number of similar messages in this group",
    )


class DedupeGroup(DedupeGroupBase, table=True):
    """Database model for deduplication groups."""

    __tablename__ = "dedupe_group"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    primary_message_id: uuid.UUID = Field(
        foreign_key="telegram_message.id",
        nullable=False,
        ondelete="CASCADE",
        description="ID of the primary (canonical) message in this group",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this group was created",
    )

    # Relationships
    primary_message: TelegramMessage | None = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[DedupeGroup.primary_message_id]",
            "lazy": "joined",
        }
    )
    members: list[TelegramMessage] = Relationship(
        back_populates="dedupe_group",
        sa_relationship_kwargs={
            "foreign_keys": "[TelegramMessage.dedupe_group_id]"
        }
    )
    group_members: list["DedupeGroupMember"] = Relationship(
        back_populates="group", cascade_delete=True
    )


class DedupeGroupMember(SQLModel, table=True):
    """Database model for dedupe group membership with similarity scores."""

    __tablename__ = "dedupe_group_member"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    group_id: uuid.UUID = Field(
        foreign_key="dedupe_group.id",
        nullable=False,
        ondelete="CASCADE",
    )
    message_id: uuid.UUID = Field(
        foreign_key="telegram_message.id",
        nullable=False,
        ondelete="CASCADE",
    )
    similarity_score: float = Field(
        description="Cosine similarity score to primary message (0-1)",
    )
    explanation_text: str | None = Field(
        default=None,
        description="Human-readable explanation of why messages are similar",
    )

    # Relationships
    group: DedupeGroup | None = Relationship(back_populates="group_members")
    message: TelegramMessage | None = Relationship()


class DedupeGroupPublic(DedupeGroupBase):
    """Properties to return via API."""

    id: uuid.UUID
    primary_message_id: uuid.UUID
    created_at: datetime


class DedupeGroupMemberPublic(SQLModel):
    """Properties to return via API for group members."""

    id: uuid.UUID
    message_id: uuid.UUID
    similarity_score: float
    explanation_text: str | None = None


class DeduplicatedMessagePublic(TelegramMessagePublic):
    """Extended message model with deduplication info."""

    dedupe_group_id: uuid.UUID | None = None
    duplicate_count: int = 0  # How many duplicates were found
    confidence_score: float | None = None  # Deduplication confidence
