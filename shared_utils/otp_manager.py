from fastapi import HTTPException
import secrets

from app.config import settings
from app.redis import get_redis
from app.utils.send_email import send_email
import re
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")



# -------------------------
# Detect Input Type (Email / Phone)
# -------------------------
def detect_input_type(value: str) -> str:
    value = value.strip()

    if re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', value):
        return 'email'

    raise HTTPException(status_code=400, detail="Invalid Email.")

# -------------------------
# Constants
# -------------------------
OTP_EXPIRY_SECONDS = 60 * 7
MAX_ATTEMPTS_PER_HOUR = 30


def _otp_key(user_key: str, purpose: str):
    return f"{purpose}:otp:{user_key}"


def _otp_attempts_key(user_key: str, purpose: str):
    return f"{purpose}:otp_attempts:{user_key}"


def _session_key(user_key: str, purpose: str):
    return f"{purpose}:session:{user_key}"


# -------------------------
# Generate OTP
# -------------------------
async def generate_otp(user_key: str, purpose: str):
    redis = get_redis()

    otp_key = _otp_key(user_key, purpose)
    attempts_key = _otp_attempts_key(user_key, purpose)

    key_type = detect_input_type(user_key)

    attempts_raw = await redis.get(attempts_key)
    attempts = int(attempts_raw) if attempts_raw else 0

    print("user_key:", user_key)

    if attempts >= MAX_ATTEMPTS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests. Try again later.",
        )

    otp = str(secrets.randbelow(900000) + 100000)
    await redis.set(otp_key, otp, ex=OTP_EXPIRY_SECONDS)

    PURPOSE_MESSAGES = {
        "admin_login": (
            "Verify Your Email",
            "Welcome back! Please verify your email.",
        ),
        "forgot": (
            "Reset Your Password",
            "You have requested to reset your password.",
        ),
    }



    if purpose not in PURPOSE_MESSAGES:
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")

    title, message = PURPOSE_MESSAGES[purpose]

    html_message = templates.get_template("otp_email.html").render({
        "title": title,
        "name": user_key,
        "otp": otp,
        "expires_in": 5,
        "message": message,
    })

    if key_type == "email":
        try:
            await send_email(
                subject=title,
                to=user_key,
                message=f"Your OTP is: {otp}",
                html_message=html_message,
                from_name="OCReel",
                from_email=settings.DEFAULT_FROM_EMAIL,
            )
        except:
            print("Email not sent.")

    count = await redis.incr(attempts_key)
    if count == 1:
        await redis.expire(attempts_key, 3600)

    return otp


# -------------------------
# Verify OTP
# -------------------------
async def verify_otp(user_key: str, otp_value: str, purpose: str) -> str:
    redis = get_redis()
    otp_key = _otp_key(user_key, purpose)

    stored_otp = await redis.get(otp_key)

    if not stored_otp:
        raise HTTPException(status_code=400, detail="OTP expired or not found.")

    # stored_otp = stored_otp.decode()  # FIX

    if stored_otp != otp_value:
        raise HTTPException(status_code=400, detail="Invalid OTP. Please try again.")

    # OTP is valid -> delete OTP
    await redis.delete(otp_key)

    # Create session key
    session_key = secrets.token_urlsafe(32)
    redis_session_key = _session_key(user_key, purpose)

    await redis.set(redis_session_key, session_key, ex=OTP_EXPIRY_SECONDS)

    return session_key


# -------------------------
# Verify Session Key
# -------------------------
async def verify_session_key(user_key: str, session_key: str, purpose: str) -> bool:
    redis = get_redis()

    stored = await redis.get(_session_key(user_key, purpose))
    if not stored:
        raise HTTPException(status_code=400, detail="Invalid or expired session key.")

    if stored != session_key:
        raise HTTPException(status_code=400, detail="Invalid session key.")

    await redis.delete(_session_key(user_key, purpose))
    return True
