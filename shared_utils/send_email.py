import asyncio
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pydantic import EmailStr
from typing import Optional, List, Dict, Union
from fastapi import UploadFile

from app.config import settings

# ==================================================
# MAIL CONFIG
# ==================================================
conf = ConnectionConfig(
    MAIL_USERNAME=settings.EMAIL_HOST_USER,
    MAIL_PASSWORD=settings.EMAIL_HOST_PASSWORD,
    MAIL_FROM=settings.EMAIL_HOST_USER,
    MAIL_PORT=settings.EMAIL_PORT,
    MAIL_SERVER=settings.EMAIL_HOST,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
)

# ==================================================
# SEND EMAIL FUNCTION
# ==================================================
async def send_email(
    *,
    subject: str,
    to: Union[EmailStr, List[EmailStr]],
    message: Optional[str] = None,
    html_message: Optional[str] = None,

    # sender
    from_email: Optional[EmailStr] = None,  # The email you want "from"
    from_name: Optional[str] = None,

    # optional recipients
    cc: Optional[List[EmailStr]] = None,
    bcc: Optional[List[EmailStr]] = None,
    reply_to: Optional[List[EmailStr]] = None,

    # extras
    attachments: Optional[List[Union[UploadFile, Dict, str]]] = None,
    headers: Optional[Dict] = None,

    # retry
    retries: int = 2,
    delay: int = 2,
) -> bool:

    if from_email and from_email != settings.EMAIL_HOST_USER:
        sender_email = settings.EMAIL_HOST_USER
        reply_to_list = reply_to or [from_email]
    else:
        sender_email = from_email or settings.EMAIL_HOST_USER
        reply_to_list = reply_to or []

    # Compose body
    body = html_message or message
    if not body:
        raise ValueError("Either message or html_message must be provided")
    subtype = "html" if html_message else "plain"

    # Normalize recipients
    recipients = [to] if isinstance(to, str) else list(to)
    cc_list = cc or []
    bcc_list = bcc or []

    # Create message
    msg = MessageSchema(
        subject=subject,
        recipients=recipients,
        body=body,
        subtype=subtype,
        from_email=sender_email,
        from_name=from_name,
        cc=cc_list,
        bcc=bcc_list,
        reply_to=reply_to_list,
        attachments=attachments or [],
        headers=headers,
    )

    fast_mail = FastMail(conf)

    # Retry mechanism
    for attempt in range(1, retries + 2):
        try:
            await fast_mail.send_message(msg)
            print(f">> Email sent from {sender_email} → {recipients} (reply-to: {reply_to_list})", flush=True)
            return True

        except Exception as e:
            print(f">> Email failed (attempt {attempt}/{retries + 1}) → {e}", flush=True)
            if attempt <= retries:
                await asyncio.sleep(delay)
            else:
                raise Exception(f"Failed to send email after {retries + 1} attempts") from e
