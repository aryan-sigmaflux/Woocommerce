"""
Database models — only 3 tables. Everything else lives in WooCommerce.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Column, String, Text, Boolean, Numeric, Integer, DateTime, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from db.database import Base
from config import settings


def _utcnow():
    return datetime.now(timezone.utc)


def _default_expires():
    return datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_EXPIRY_HOURS)


class CheckoutSession(Base):
    """Bot's working memory for a live checkout conversation."""
    __tablename__ = "checkout_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(String(20), nullable=False, index=True)
    state = Column(String(50), nullable=False, default="WELCOME_SENT")

    # Cart snapshot from Woo order webhook
    cart = Column(JSONB, nullable=False, default=list)

    woo_pending_order_id = Column(Integer, nullable=True)
    selected_address_id = Column(UUID(as_uuid=True), ForeignKey("addresses.id"), nullable=True)
    shipping_cost = Column(Numeric(10, 2), default=0)
    coupon_code = Column(String(100), nullable=True)
    discount_amount = Column(Numeric(10, 2), default=0)
    discount_type = Column(String(30), nullable=True)  # percentage | fixed_cart | fixed_product
    payment_method = Column(String(20), nullable=True)  # cod | razorpay
    razorpay_link_id = Column(String(100), nullable=True)
    razorpay_payment_id = Column(String(100), nullable=True)
    subtotal = Column(Numeric(10, 2), default=0)
    total = Column(Numeric(10, 2), default=0)
    woo_confirmed_order_id = Column(Integer, nullable=True)

    # Session will expire 24h after last update
    expires_at = Column(DateTime(timezone=True), default=_default_expires)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Track unexpected input count for interruption handling
    unexpected_input_count = Column(Integer, default=0)

    # Coupon attempt counter
    coupon_attempts = Column(Integer, default=0)

    # Relationship
    selected_address = relationship("Address", foreign_keys=[selected_address_id])
    message_logs = relationship("MessageLog", back_populates="session")

    __table_args__ = (
        Index("ix_checkout_sessions_phone_state", "phone", "state"),
    )


class Address(Base):
    """Saved delivery addresses keyed by phone number."""
    __tablename__ = "addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(String(20), nullable=False, index=True)
    full_name = Column(String(200), nullable=False)
    line1 = Column(String(500), nullable=False)
    line2 = Column(String(500), nullable=True)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    pincode = Column(String(10), nullable=False)
    label = Column(String(50), nullable=True)  # e.g. "Home", "Office"
    is_last_used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MessageLog(Base):
    """Full log of every inbound and outbound WhatsApp message."""
    __tablename__ = "message_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # inbound | outbound
    wamid = Column(String(200), nullable=True, unique=True)  # For deduplication
    message_type = Column(String(30), nullable=True)  # text | interactive | template | button
    body = Column(Text, nullable=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("checkout_sessions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    session = relationship("CheckoutSession", back_populates="message_logs")

    __table_args__ = (
        Index("ix_message_logs_wamid", "wamid", unique=True),
    )
