"""
User ORM model
"""

from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from app.database.postgres import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    phone = Column(String(20), unique=True, index=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    broker = Column(String(50), default="groww")
    broker_api_key = Column(Text, nullable=True)
    # Holds the approval secret when broker_key_type == "approval", or the
    # base32 TOTP seed when it is "totp".
    broker_api_secret = Column(Text, nullable=True)
    # "approval" needs a manual daily session approval in the broker's app;
    # "totp" derives a 6-digit code locally and refreshes unattended.
    broker_key_type = Column(String(20), default="approval")
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
