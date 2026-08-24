import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, ForeignKey, Float, Index, Integer, String, Text, UniqueConstraint, text
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
    __table_args__ = (
        UniqueConstraint("account_id", "external_id"),
        # Partial indexes = the enrich claim queries. No queue table: "needs work"
        # is a visible fact about the row (comments/visual_description IS NULL),
        # and these indexes make that predicate cheap at bulk volume.
        Index("ix_posts_comment_attempts_pending", "comment_attempts", postgresql_where=text("comments IS NULL")),
        Index(
            "ix_posts_visual_attempts_pending",
            "visual_attempts",
            postgresql_where=text("visual_description IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(nullable=False)
    caption: Mapped[str | None] = mapped_column(nullable=True)
    media_urls: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    posted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    likes: Mapped[int | None] = mapped_column(nullable=True)
    # none_as_null=True: without it, SQLAlchemy encodes a Python None bind value as
    # the JSON null *literal*, not SQL NULL — invisible via the ORM (which decodes
    # JSON null back to Python None on read) but breaks any raw `IS NULL` query,
    # including the enrich claim query this column exists for.
    comments: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    shares: Mapped[int | None] = mapped_column(nullable=True)
    views: Mapped[int | None] = mapped_column(nullable=True)
    comment_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Visual modality: thumbnail_url is the raw scraped URL (goes stale in hours);
    # cover_key points at the durably-stored bytes (see cover_storage.py) that
    # enrichment actually describes. visual_generated_at means "when it succeeded" —
    # it is NOT stamped on failure, so a failed attempt can be retried up to
    # visual_attempts < 3 rather than being silently permanent.
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_status: Mapped[str | None] = mapped_column(String, nullable=True)  # pending | stored | missing
    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_model: Mapped[str | None] = mapped_column(String, nullable=True)
    visual_generated_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    visual_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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
    """One row per routed post — a post's embedding plus which world it was assigned to.

    Unique on post_id + upsert on write: routing is idempotent and re-runnable —
    a re-route (e.g. after a blob-assembly change) updates the existing row rather
    than duplicating it.
    """

    __tablename__ = "world_posts"
    __table_args__ = (UniqueConstraint("post_id"), {"schema": "topology"})

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    world_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("topology.worlds.id"))
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    posted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)

    # blob_text is the exact string that was embedded — can't be rebuilt later since
    # comments keep accruing after the scrape. cosine/margin: all 8 cosines are already
    # computed for the argmax, margin is what the (future) abstain threshold keys off.
    blob_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cosine: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin: Mapped[float | None] = mapped_column(Float, nullable=True)
