import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt

from app.core.config import get_settings


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    *,
    session_id: str | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    return jwt.encode(
        {
            "sub": subject,
            "sid": session_id or str(uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": expires_at,
            "jti": str(uuid4()),
            "type": "access",
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> str | None:
    payload = decode_access_token_claims(token)
    return payload.get("sub") if payload is not None else None


def decode_access_token_claims(token: str) -> dict[str, str] | None:
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require_sub": True,
                "require_iat": True,
                "require_nbf": True,
                "require_exp": True,
            },
        )
    except JWTError:
        return None
    required = ("sub", "sid", "iss", "aud", "jti", "type")
    if payload.get("type") != "access" or not all(
        isinstance(payload.get(key), str) for key in required
    ):
        return None
    return {key: str(payload[key]) for key in required}


def generate_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def pkce_s256_challenge(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def sign_csrf_token(session_id: str, nonce: str | None = None) -> str:
    import hmac

    settings = get_settings()
    value = f"{session_id}.{nonce or secrets.token_urlsafe(24)}"
    signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{value}.{signature}"


def verify_csrf_token(token: str, session_id: str) -> bool:
    import hmac

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != session_id:
        return False
    unsigned = ".".join(parts[:2])
    expected = hmac.new(
        get_settings().jwt_secret_key.encode("utf-8"),
        unsigned.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(parts[2], expected)
