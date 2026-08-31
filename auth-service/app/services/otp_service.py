import asyncio
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.auth import OtpPurpose
from app.utils.redis import get_redis


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _otp_redis_key(email: str, purpose: OtpPurpose) -> str:
    return f"otp:{purpose}:{email}"


def _generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def _purpose_copy(purpose: OtpPurpose) -> str:
    return "login" if purpose == "login" else "signup"


def _build_email_bodies(purpose: OtpPurpose, otp: str) -> tuple[str, str, str]:
    expires_in = settings.SIGNUP_OTP_EXPIRE_MINUTES
    action = _purpose_copy(purpose)
    subject = f"Your {action} OTP"
    text_body = (
        f"Use this OTP to complete your {action}: {otp}\n\n"
        f"It will expire in {expires_in} minutes."
    )
    html_body = (
        "<html><body style=\"font-family:Arial,sans-serif;line-height:1.5;\">"
        f"<h2>Complete your {action}</h2>"
        f"<p>Use this OTP to complete your {action}:</p><p style=\"font-size:28px;"
        "font-weight:700;letter-spacing:4px;\">"
        f"{otp}</p><p>This OTP will expire in {expires_in} minutes.</p>"
        "</body></html>"
    )
    return subject, text_body, html_body


def _send_email_sync(to_email: str, subject: str, text_body: str, html_body: str) -> None:
    from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USER or "no-reply@example.com"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = (
        f"{settings.SMTP_FROM_NAME} <{from_email}>"
        if settings.SMTP_FROM_NAME
        else from_email
    )
    message["To"] = to_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def _send_otp_email(to_email: str, purpose: OtpPurpose, otp: str) -> None:
    subject, text_body, html_body = _build_email_bodies(purpose, otp)

    if not settings.SMTP_HOST:
        print(f"[auth-service] {_purpose_copy(purpose).title()} OTP for {to_email}: {otp}", flush=True)
        return

    try:
        await asyncio.to_thread(_send_email_sync, to_email, subject, text_body, html_body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send OTP email",
        ) from exc


async def send_otp(email: str, purpose: OtpPurpose) -> None:
    normalized_email = _normalize_email(email)

    if purpose == "signup":
        existing_user = await User.get_or_none(email=normalized_email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already in use",
            )
    elif purpose == "login":
        existing_user = await User.get_or_none(email=normalized_email)
        if not existing_user or not existing_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        if not existing_user.is_2fa:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Two-factor authentication is not enabled for this account",
            )

    otp = _generate_otp()
    otp_hash = get_password_hash(otp)

    redis = get_redis()
    redis_key = _otp_redis_key(normalized_email, purpose)
    ttl_seconds = settings.SIGNUP_OTP_EXPIRE_MINUTES * 60
    await redis.set(redis_key, otp_hash, ex=ttl_seconds)

    await _send_otp_email(normalized_email, purpose, otp)


async def verify_otp(email: str, purpose: OtpPurpose, otp: str) -> None:
    normalized_email = _normalize_email(email)
    redis = get_redis()
    redis_key = _otp_redis_key(normalized_email, purpose)

    otp_hash = await redis.get(redis_key)

    if not otp_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP expired or not found for this email and purpose",
        )

    if not verify_password(otp, otp_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )

    await redis.delete(redis_key)

