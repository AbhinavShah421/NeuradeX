"""
Auth API — signup, OTP verification, broker linking, login, session management
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_db
from app.models.user import User
from app.utils.groww_client import get_groww_client, init_groww_client
from app.utils.elk_logger import get_logger
from app.utils.otp_service import (
    clear_signup_state,
    generate_and_send_otp,
    get_pending_signup,
    is_email_verified,
    store_pending_signup,
    verify_otp,
)

logger = get_logger(__name__)
router = APIRouter()

ALGORITHM = "HS256"


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_jwt(user_id: int, email: str, broker: str, groww_token: str) -> tuple[str, str]:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "email": email,
        "broker": broker,
        "groww_token": groww_token,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)
    return token, expires.isoformat()


def _decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session token")


def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.split(" ", 1)[1]
    return _decode_jwt(token)


# ── Password helpers ──────────────────────────────────────────────────────────

def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── Schemas ───────────────────────────────────────────────────────────────────

class SignupSendOtpRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    phone: str
    password: str
    confirm_password: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = v.strip().replace(" ", "").replace("-", "")
        if not digits.lstrip("+").isdigit() or len(digits.lstrip("+")) < 10:
            raise ValueError("Invalid phone number")
        return digits

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v


class SignupVerifyOtpRequest(BaseModel):
    email: EmailStr
    otp: str


class SignupCompleteRequest(BaseModel):
    email: EmailStr
    broker: str = "groww"
    api_key: str
    api_secret: str


class LoginRequest(BaseModel):
    identifier: str  # email or phone
    password: str


# ── Step 1: send OTP ──────────────────────────────────────────────────────────

@router.post("/signup/send-otp")
async def signup_send_otp(req: SignupSendOtpRequest, db: AsyncSession = Depends(get_db)):
    # Check if email already registered
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(400, "An account with this email already exists")

    # Check phone uniqueness
    if req.phone:
        result = await db.execute(select(User).where(User.phone == req.phone))
        if result.scalar_one_or_none():
            raise HTTPException(400, "An account with this phone number already exists")

    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    # Store pending data in Redis
    pending = {
        "first_name": req.first_name,
        "last_name": req.last_name,
        "email": req.email,
        "phone": req.phone,
        "password_hash": _hash_password(req.password),
    }
    await store_pending_signup(req.email, pending)

    # Generate and send OTP
    await generate_and_send_otp(req.email, req.phone, req.first_name)

    return {
        "status": "success",
        "message": f"Verification code sent to {req.email} and WhatsApp",
    }


# ── Step 2: verify OTP ────────────────────────────────────────────────────────

@router.post("/signup/verify-otp")
async def signup_verify_otp(req: SignupVerifyOtpRequest):
    ok = await verify_otp(req.email, req.otp.strip())
    if not ok:
        raise HTTPException(400, "Invalid or expired verification code")
    return {"status": "success", "message": "Email verified successfully"}


# ── Step 3: complete signup + broker linking ──────────────────────────────────

@router.post("/signup/complete")
async def signup_complete(req: SignupCompleteRequest, db: AsyncSession = Depends(get_db)):
    if req.broker != "groww":
        raise HTTPException(400, "Only Groww broker is supported currently")

    if not req.api_key.strip() or not req.api_secret.strip():
        raise HTTPException(400, "API key and secret are required")

    # Check OTP was verified
    if not await is_email_verified(req.email):
        raise HTTPException(400, "Email not verified — please complete OTP verification first")

    # Get pending signup data
    pending = await get_pending_signup(req.email)
    if not pending:
        raise HTTPException(400, "Signup session expired — please start again")

    # Create user in DB
    user = User(
        first_name=pending["first_name"],
        last_name=pending["last_name"],
        email=pending["email"],
        phone=pending.get("phone"),
        password_hash=pending["password_hash"],
        broker=req.broker,
        broker_api_key=req.api_key.strip(),
        broker_api_secret=req.api_secret.strip(),
        is_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Clean up Redis state
    await clear_signup_state(req.email)

    # Issue JWT
    token, expires_at = _create_jwt(user.id, user.email, req.broker, req.api_key.strip())
    logger.info(
        "New user registered",
        extra={"log_type": "auth_event", "event": "signup_complete", "user_id": user.id, "email": user.email},
    )

    return {
        "status": "success",
        "data": {
            "token": token,
            "broker": req.broker,
            "expires_at": expires_at,
            "user_id": user.id,
            "name": f"{user.first_name} {user.last_name}",
            "email": user.email,
        },
    }


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    identifier = req.identifier.strip()

    # Try email first, then phone
    result = await db.execute(select(User).where(User.email == identifier))
    user = result.scalar_one_or_none()

    if not user:
        result = await db.execute(select(User).where(User.phone == identifier))
        user = result.scalar_one_or_none()

    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    if not user.is_verified:
        raise HTTPException(403, "Account not verified — please complete signup")

    groww_token = user.broker_api_key or ""
    token, expires_at = _create_jwt(user.id, user.email, user.broker or "groww", groww_token)
    logger.info(
        "User logged in",
        extra={"log_type": "auth_event", "event": "login_success", "user_id": user.id, "email": user.email},
    )

    return {
        "status": "success",
        "data": {
            "token": token,
            "broker": user.broker,
            "expires_at": expires_at,
            "user_id": user.id,
            "name": f"{user.first_name} {user.last_name}",
            "email": user.email,
        },
    }


# ── Session endpoints ─────────────────────────────────────────────────────────

@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {
        "status": "success",
        "data": {
            "broker": user.get("broker"),
            "email": user.get("email"),
            "authenticated": True,
        },
    }


@router.get("/profile")
async def profile(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user_id = user.get("sub")
    db_user: User | None = None

    if user_id:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        db_user = result.scalar_one_or_none()

    # Build profile from DB row
    if db_user:
        name = f"{db_user.first_name} {db_user.last_name}".strip() or "NeuradeX User"
        email = db_user.email or ""
        initials = "".join(w[0].upper() for w in name.split() if w)[:2] or "NU"

        # Extract account_id from the stored Groww API key JWT (no sig check needed)
        account_id = ""
        try:
            parts = (db_user.broker_api_key or "").split(".")
            if len(parts) == 3:
                padding = (4 - len(parts[1]) % 4) % 4
                payload_bytes = base64.urlsafe_b64decode(parts[1] + "=" * padding)
                sub_raw = json.loads(payload_bytes).get("sub", "{}")
                sub = json.loads(sub_raw) if isinstance(sub_raw, str) else sub_raw
                account_id = sub.get("userAccountId", "")
        except Exception as exc:
            logger.debug("Could not decode broker JWT for account_id: %s", exc)

        short_id = account_id[:8].upper() if account_id else "——"

        return {
            "status": "success",
            "data": {
                "name": name,
                "email": email,
                "initials": initials,
                "account_id": short_id,
                "broker": db_user.broker,
            },
        }

    # Fallback: decode from JWT payload (legacy tokens without DB user)
    groww_token = user.get("groww_token", "")
    account_id = ""
    try:
        parts = groww_token.split(".")
        if len(parts) == 3:
            padding = (4 - len(parts[1]) % 4) % 4
            payload_bytes = base64.urlsafe_b64decode(parts[1] + "=" * padding)
            sub_raw = json.loads(payload_bytes).get("sub", "{}")
            sub = json.loads(sub_raw) if isinstance(sub_raw, str) else sub_raw
            account_id = sub.get("userAccountId", "")
    except Exception as exc:
        logger.debug("Could not decode legacy Groww JWT for account_id: %s", exc)

    name = "Groww User"
    groww = get_groww_client()
    if groww:
        for endpoint in ["/user/profile", "/customer/profile", "/user/account", "/user/details"]:
            try:
                data = await groww._get(endpoint)
                inner = data.get("payload", data)
                fetched = (
                    inner.get("name") or inner.get("fullName") or
                    inner.get("userName") or inner.get("displayName") or
                    (inner.get("firstName", "") + " " + inner.get("lastName", ""))
                ).strip()
                if fetched and fetched != " ":
                    name = fetched
                    break
            except Exception as exc:
                logger.debug("Groww profile %s failed: %s", endpoint, exc)

    short_id = account_id[:8].upper() if account_id else "——"
    initials = "".join(w[0].upper() for w in name.split() if w)[:2] or "GW"

    return {
        "status": "success",
        "data": {
            "name": name,
            "email": user.get("email", ""),
            "initials": initials,
            "account_id": short_id,
            "broker": user.get("broker"),
        },
    }


@router.post("/logout")
async def logout():
    return {"status": "success", "message": "Logged out"}


# ── Groww token management ────────────────────────────────────────────────────

@router.get("/groww/status")
async def groww_status(user: dict = Depends(get_current_user)):
    """Return the current Groww API token status."""
    client = get_groww_client()
    if not client:
        return {
            "status": "success",
            "data": {
                "status": "not_configured",
                "token_expiry": None,
                "time_remaining_seconds": None,
                "failure_count": 0,
                "failure_reason": "Groww API credentials not set",
                "last_attempt": None,
                "has_token": False,
            },
        }
    return {"status": "success", "data": client.get_status()}


@router.post("/groww/refresh")
async def groww_refresh(user: dict = Depends(get_current_user)):
    """Force a Groww token refresh using the currently stored credentials."""
    client = get_groww_client()
    if not client:
        raise HTTPException(400, "Groww API client is not configured")
    result = await client.force_refresh()
    if result["success"]:
        return {"status": "success", "data": result}
    raise HTTPException(502, f"Groww token refresh failed: {result.get('error')}")


class GrowwCredentialsRequest(BaseModel):
    api_key: str
    api_secret: str


@router.put("/groww/credentials")
async def groww_update_credentials(
    req: GrowwCredentialsRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Groww API credentials and immediately attempt a token refresh."""
    if not req.api_key.strip() or not req.api_secret.strip():
        raise HTTPException(400, "api_key and api_secret are required")

    user_id = user.get("sub")
    if user_id:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        db_user = result.scalar_one_or_none()
        if db_user:
            db_user.broker_api_key = req.api_key.strip()
            db_user.broker_api_secret = req.api_secret.strip()
            await db.commit()

    # Re-init or update the singleton client
    client = get_groww_client()
    if client:
        refresh_result = await client.update_credentials(req.api_key.strip(), req.api_secret.strip())
    else:
        new_client = init_groww_client(req.api_key.strip(), req.api_secret.strip())
        refresh_result = await new_client.force_refresh()

    if refresh_result["success"]:
        return {
            "status": "success",
            "message": "Credentials updated and token refreshed",
            "data": refresh_result,
        }
    # Credentials saved to DB even if token refresh fails (TOTP might need approval)
    return {
        "status": "partial",
        "message": "Credentials saved but token refresh failed — Groww TOTP session may need approval",
        "data": refresh_result,
    }
