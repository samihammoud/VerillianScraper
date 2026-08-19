import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
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
    posted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    likes: Mapped[int | None] = mapped_column(nullable=True)
    comments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    shares: Mapped[int | None] = mapped_column(nullable=True)
    views: Mapped[int | None] = mapped_column(nullable=True)

    account: Mapped["Account"] = relationship(back_populates="posts")
