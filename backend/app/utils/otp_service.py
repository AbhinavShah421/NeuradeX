"""
OTP generation, Redis storage, and delivery via email + WhatsApp
"""

import json
import logging
import random
import string

from app.config import settings
from app.utils.redis_client import get_redis

logger = logging.getLogger(__name__)

OTP_TTL = 600  # 10 minutes


DEV_OTP = "888444"  # hardcoded until SMTP/Twilio creds are configured

def _generate_otp(length: int = 6) -> str:
    return DEV_OTP


# ── Redis helpers ─────────────────────────────────────────────────────────────

async def store_otp(email: str, otp: str) -> None:
    """Store OTP in Redis with TTL."""
    redis = get_redis()
    await redis.setex(f"otp:{email}", OTP_TTL, otp)


async def verify_otp(email: str, otp: str) -> bool:
    """Return True and delete the OTP key if the code matches."""
    redis = get_redis()
    stored = await redis.get(f"otp:{email}")
    if stored and stored == otp:
        await redis.delete(f"otp:{email}")
        await redis.setex(f"otp_verified:{email}", OTP_TTL, "1")
        return True
    return False


async def is_email_verified(email: str) -> bool:
    redis = get_redis()
    return bool(await redis.get(f"otp_verified:{email}"))


async def store_pending_signup(email: str, data: dict) -> None:
    """Temporarily store signup data in Redis until OTP is verified."""
    redis = get_redis()
    await redis.setex(f"signup_pending:{email}", OTP_TTL, json.dumps(data))


async def get_pending_signup(email: str) -> dict | None:
    redis = get_redis()
    raw = await redis.get(f"signup_pending:{email}")
    return json.loads(raw) if raw else None


async def clear_signup_state(email: str) -> None:
    redis = get_redis()
    await redis.delete(f"otp:{email}", f"otp_verified:{email}", f"signup_pending:{email}")


# ── Delivery ──────────────────────────────────────────────────────────────────

async def send_otp_email(email: str, otp: str, name: str) -> bool:
    """Send OTP via email using aiosmtplib. Returns True on success."""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured — OTP for %s: %s", email, otp)
        return False

    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your NeuradeX verification code: {otp}"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = email

        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;background:#f9fafb;border-radius:12px">
          <h2 style="color:#00b386;margin-bottom:8px">NeuradeX</h2>
          <p style="color:#374151">Hi {name}, your verification code is:</p>
          <div style="font-size:36px;font-weight:700;letter-spacing:8px;color:#111827;margin:24px 0">{otp}</div>
          <p style="color:#6b7280;font-size:13px">This code expires in 10 minutes. Do not share it with anyone.</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )
        logger.info("OTP email sent to %s", email)
        return True
    except Exception as exc:
        logger.error("Failed to send OTP email to %s: %s", email, exc)
        return False


async def send_otp_whatsapp(phone: str, otp: str, name: str) -> bool:
    """Send OTP via Twilio WhatsApp. Returns True on success."""
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio not configured — WhatsApp OTP for %s: %s", phone, otp)
        return False

    # Normalize phone to E.164 with India prefix if needed
    normalized = phone.strip()
    if not normalized.startswith("+"):
        normalized = "+91" + normalized.lstrip("0")

    try:
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{normalized}",
            body=f"Hi {name}, your NeuradeX verification code is: *{otp}*\nThis code expires in 10 minutes.",
        )
        logger.info("OTP WhatsApp sent to %s", normalized)
        return True
    except Exception as exc:
        logger.error("Failed to send WhatsApp OTP to %s: %s", normalized, exc)
        return False


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_and_send_otp(email: str, phone: str, name: str) -> str:
    """Generate OTP, store it, send via all channels. Returns the OTP (for debug logging)."""
    otp = _generate_otp()
    await store_otp(email, otp)

    email_ok = await send_otp_email(email, otp, name)
    wa_ok = await send_otp_whatsapp(phone, otp, name)

    if not email_ok and not wa_ok:
        # Dev fallback — log so the developer can complete the flow
        logger.warning("DEV — OTP for %s is: %s", email, otp)

    return otp
