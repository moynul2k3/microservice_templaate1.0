from datetime import datetime, timezone

from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.auth import UserCreate, UserSignup


async def get_user_by_email(email: str) -> User | None:
    return await User.get_or_none(email=email.lower())


async def create_user(payload: UserCreate | UserSignup) -> User:
    return await User.create(
        email=payload.email.lower(),
        password=get_password_hash(payload.password),
        is_2fa=payload.is_2fa,
    )


async def authenticate(email: str, password: str) -> User | None:
    user = await verify_user_credentials(email, password)
    if not user:
        return None

    await mark_login_success(user)
    return user


async def verify_user_credentials(email: str, password: str) -> User | None:
    user = await get_user_by_email(email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password):
        return None
    return user


async def mark_login_success(user: User) -> None:
    now = datetime.now(timezone.utc)
    user.last_login_at = now
    user.updated_at = now
    await user.save(update_fields=["last_login_at", "updated_at"])
