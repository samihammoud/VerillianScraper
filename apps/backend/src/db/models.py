import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(nullable=False)
    external_id: Mapped[str] = mapped_column(nullable=False)
    handle: Mapped[str] = mapped_column(nullable=False)
    follower_count: Mapped[int | None] = mapped_column(nullable=True)

    posts: Mapped[list["Post"]] = relationship(back_populates="account")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("account_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(nullable=False)
    caption: Mapped[str | None] = mapped_column(nullable=True)
    media_urls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    posted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    likes: Mapped[int | None] = mapped_column(nullable=True)
    comments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    shares: Mapped[int | None] = mapped_column(nullable=True)
    views: Mapped[int | None] = mapped_column(nullable=True)

    account: Mapped["Account"] = relationship(back_populates="posts")


class World(Base):
    """Manually seeded, static reference set — 8 hand-written world definitions."""

    __tablename__ = "worlds"
    __table_args__ = {"schema": "topology"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    example_snippets: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    reference_embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    embed_model: Mapped[str] = mapped_column(String, default="text-embedding-3-small")
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class WorldPost(Base):
    """Schema created now. Rows only get inserted during routing (later phase) — do not populate yet."""

    __tablename__ = "world_posts"
    __table_args__ = {"schema": "topology"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    world_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("topology.worlds.id"))
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    engagement: Mapped[float | None] = mapped_column(nullable=True)
    posted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
