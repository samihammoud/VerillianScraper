from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import Account, Post


def get_existing_post_external_ids(db: Session, platform: str, account_external_id: str) -> set[str]:
    """External IDs already stored for this account — used to skip comment fetches for posts we already have."""
    account_id = db.execute(
        select(Account.id).where(Account.platform == platform, Account.external_id == account_external_id)
    ).scalar_one_or_none()

    if account_id is None:
        return set()

    return set(db.execute(select(Post.external_id).where(Post.account_id == account_id)).scalars().all())


def store_account_and_posts(
    db: Session, account_data: dict[str, Any], posts_data: list[dict[str, Any]]
) -> UUID | None:
    """Insert an account and its posts, silently skipping rows that already exist."""
    if not account_data:
        return None

    account_stmt = (
        insert(Account)
        .values(**account_data)
        .on_conflict_do_nothing(index_elements=["platform", "external_id"])
        .returning(Account.id)
    )
    account_id = db.execute(account_stmt).scalar_one_or_none()

    if account_id is None:
        account_id = db.execute(
            select(Account.id).where(
                Account.platform == account_data["platform"],
                Account.external_id == account_data["external_id"],
            )
        ).scalar_one()

    if posts_data:
        rows = [{**post, "account_id": account_id} for post in posts_data]
        post_stmt = (
            insert(Post).values(rows).on_conflict_do_nothing(index_elements=["account_id", "external_id"])
        )
        db.execute(post_stmt)

    db.commit()
    return account_id
