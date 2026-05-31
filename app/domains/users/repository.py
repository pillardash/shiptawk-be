from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.users.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    statement = select(User).where(
        User.email == normalize_email(email),
        User.deleted_at.is_(None),
    )
    return db.scalars(statement).first()


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    statement = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    return db.scalars(statement).first()


def create_user(db: Session, *, email: str, password_hash: str) -> User:
    user = User(email=normalize_email(email), password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
