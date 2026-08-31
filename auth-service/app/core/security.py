from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# Prefer a pure-Python default hasher for local stability while still
# accepting existing bcrypt hashes already stored in development databases.
pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def _refresh_secret_key() -> str:
    return settings.JWT_REFRESH_SECRET_KEY or settings.REFRESH_SECRET_KEY or f"{settings.JWT_SECRET_KEY or settings.SECRET_KEY}:refresh"


def _create_token(subject: str, *, expires_delta: timedelta, token_type: str, secret_key: str) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {"sub": subject, "exp": expire, "type": token_type}
    return jwt.encode(payload, secret_key, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str) -> str:
    return _create_token(
        subject,
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        token_type="access",
        secret_key=settings.JWT_SECRET_KEY or settings.SECRET_KEY or "",
    )


def create_refresh_token(subject: str) -> str:
    return _create_token(
        subject,
        expires_delta=timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        token_type="refresh",
        secret_key=_refresh_secret_key(),
    )

def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.JWT_SECRET_KEY or settings.SECRET_KEY or "", algorithms=[settings.JWT_ALGORITHM])
    token_type = payload.get("type")
    if token_type not in (None, "access"):
        raise JWTError("Invalid access token type")
    return payload


def decode_refresh_token(token: str) -> dict:
    payload = jwt.decode(token, _refresh_secret_key(), algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != "refresh":
        raise JWTError("Invalid refresh token type")
    return payload
