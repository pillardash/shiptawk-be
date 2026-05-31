from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.domains.users.models import User
from app.domains.users.repository import create_user, get_user_by_email, get_user_by_id
from app.shared.exceptions import ConflictError, ForbiddenError, UnauthorizedError


def register_user(db: Session, *, email: str, password: str) -> User:
    if get_user_by_email(db, email) is not None:
        raise ConflictError("A user with this email already exists.", code="user_exists")
    try:
        return create_user(db, email=email, password_hash=hash_password(password))
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A user with this email already exists.", code="user_exists") from exc


def authenticate_user(db: Session, *, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.", code="inactive_user")
    return user


def get_active_user_by_id(db: Session, user_id: UUID) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Invalid authentication credentials.", code="invalid_token")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.", code="inactive_user")
    return user
