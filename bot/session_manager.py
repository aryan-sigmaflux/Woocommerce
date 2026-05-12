"""
Session Manager — CRUD for checkout_sessions.
One active session per phone at a time.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession, Address, MessageLog
from config import settings

logger = logging.getLogger(__name__)

# Terminal states — sessions in these states are considered closed
TERMINAL_STATES = {"ORDER_PLACED", "SESSION_CANCELLED"}

# Pending states — sessions where checkout was started but not completed
PENDING_STATES = {"WELCOME_SENT", "COLLECTING_PINCODE_CHECK", "ADDRESS_CHECK",
                  "COLLECTING_ADDRESS_NAME",
                  "COLLECTING_ADDRESS_LINE1", "COLLECTING_ADDRESS_LINE2",
                  "COLLECTING_ADDRESS_CITY", "COLLECTING_ADDRESS_STATE",
                  "COLLECTING_ADDRESS_PINCODE", "ADDRESS_CONFIRM",
                  "COUPON_PROMPT", "COLLECTING_COUPON",
                  "ORDER_SUMMARY", "PAYMENT_METHOD_SELECT",
                  "PAYMENT_LINK_SENT", "ORDER_CONFIRM_PROMPT"}

# All valid states
VALID_STATES = {
    "WELCOME_SENT",
    "COLLECTING_PINCODE_CHECK",
    "ADDRESS_CHECK",
    "COLLECTING_ADDRESS_NAME",
    "COLLECTING_ADDRESS_LINE1",
    "COLLECTING_ADDRESS_LINE2",
    "COLLECTING_ADDRESS_CITY",
    "COLLECTING_ADDRESS_STATE",
    "COLLECTING_ADDRESS_PINCODE",
    "ADDRESS_CONFIRM",
    "ADDRESS_CONFIRMED",
    "COUPON_PROMPT",
    "COLLECTING_COUPON",
    "ORDER_SUMMARY",
    "PAYMENT_METHOD_SELECT",
    "PAYMENT_LINK_SENT",
    "ORDER_CONFIRM_PROMPT",
    "ORDER_PLACING",
    "ORDER_PLACED",
    "SESSION_CANCELLED",
}


async def get_active_session(db: AsyncSession, phone: str) -> Optional[CheckoutSession]:
    """Get the currently active (non-terminal, non-expired) session for a phone."""
    result = await db.execute(
        select(CheckoutSession).where(
            and_(
                CheckoutSession.phone == phone,
                CheckoutSession.state.notin_(TERMINAL_STATES),
                CheckoutSession.expires_at > datetime.now(timezone.utc),
            )
        ).order_by(CheckoutSession.created_at.desc())
    )
    return result.scalars().first()


async def get_session_by_id(db: AsyncSession, session_id: UUID) -> Optional[CheckoutSession]:
    """Get a session by its ID."""
    result = await db.execute(
        select(CheckoutSession).where(CheckoutSession.id == session_id)
    )
    return result.scalars().first()


async def create_session(
    db: AsyncSession,
    phone: str,
    cart: list[dict],
    woo_pending_order_id: int,
    subtotal: Decimal,
) -> CheckoutSession:
    """
    Create a new checkout session.
    Sets state to WELCOME_SENT and expires 24h out.
    """
    session = CheckoutSession(
        phone=phone,
        state="WELCOME_SENT",
        cart=cart,
        woo_pending_order_id=woo_pending_order_id,
        subtotal=subtotal,
        total=subtotal,  # Will be recalculated with shipping/discount
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_EXPIRY_HOURS),
    )
    db.add(session)
    await db.flush()
    logger.info(f"[SESSION] Created session {session.id} for {phone} | order #{woo_pending_order_id}")
    return session


async def update_session_state(
    db: AsyncSession,
    session: CheckoutSession,
    new_state: str,
    **kwargs,
) -> CheckoutSession:
    """
    Update session state and any additional fields.
    Also refreshes expires_at and resets unexpected_input_count on state change.
    """
    session.state = new_state
    session.updated_at = datetime.now(timezone.utc)
    session.expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_EXPIRY_HOURS)
    session.unexpected_input_count = 0  # Reset on valid state transition

    for key, value in kwargs.items():
        if hasattr(session, key):
            setattr(session, key, value)

    await db.flush()
    logger.info(f"[SESSION] {session.phone} → {new_state}")
    return session


async def increment_unexpected_input(db: AsyncSession, session: CheckoutSession) -> int:
    """Increment and return the unexpected input count."""
    session.unexpected_input_count = (session.unexpected_input_count or 0) + 1
    session.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return session.unexpected_input_count


async def increment_coupon_attempts(db: AsyncSession, session: CheckoutSession) -> int:
    """Increment and return the coupon attempt count."""
    session.coupon_attempts = (session.coupon_attempts or 0) + 1
    session.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return session.coupon_attempts


async def cancel_session(db: AsyncSession, session: CheckoutSession):
    """Mark session as cancelled."""
    await update_session_state(db, session, "SESSION_CANCELLED")


async def complete_session(
    db: AsyncSession,
    session: CheckoutSession,
    woo_confirmed_order_id: int,
):
    """Mark session as completed with the confirmed order ID."""
    await update_session_state(
        db, session, "ORDER_PLACED",
        woo_confirmed_order_id=woo_confirmed_order_id,
    )


# ── Address Operations ──────────────────────────────────────────────


async def get_addresses_for_phone(db: AsyncSession, phone: str) -> list[Address]:
    """Get all saved addresses for a phone number."""
    result = await db.execute(
        select(Address).where(Address.phone == phone).order_by(Address.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_address_by_id(db: AsyncSession, address_id: UUID) -> Optional[Address]:
    """Get an address by its ID."""
    result = await db.execute(
        select(Address).where(Address.id == address_id)
    )
    return result.scalars().first()


async def save_address(
    db: AsyncSession,
    phone: str,
    full_name: str,
    line1: str,
    city: str,
    state: str,
    pincode: str,
    line2: str = None,
    label: str = None,
) -> Address:
    """Save a new address and set it as last-used."""
    # Flip all existing addresses for this phone to not-last-used
    await db.execute(
        update(Address)
        .where(Address.phone == phone)
        .values(is_last_used=False)
    )

    address = Address(
        phone=phone,
        full_name=full_name,
        line1=line1,
        line2=line2,
        city=city,
        state=state,
        pincode=pincode,
        label=label,
        is_last_used=True,
    )
    db.add(address)
    await db.flush()
    logger.info(f"[SESSION] Saved address {address.id} for {phone}")
    return address


async def set_address_as_last_used(db: AsyncSession, phone: str, address_id: UUID):
    """Mark an address as last-used, flip all others."""
    await db.execute(
        update(Address).where(Address.phone == phone).values(is_last_used=False)
    )
    await db.execute(
        update(Address).where(Address.id == address_id).values(is_last_used=True)
    )
    await db.flush()


# ── Message Log Operations ──────────────────────────────────────────


async def log_message(
    db: AsyncSession,
    phone: str,
    direction: str,
    body: str = None,
    wamid: str = None,
    message_type: str = None,
    session_id: UUID = None,
) -> Optional[MessageLog]:
    """
    Log a message. Returns None if wamid already exists (duplicate).
    """
    # Deduplication check
    if wamid:
        existing = await db.execute(
            select(MessageLog).where(MessageLog.wamid == wamid)
        )
        if existing.scalars().first():
            logger.debug(f"[LOG] Duplicate wamid: {wamid}")
            return None

    log = MessageLog(
        phone=phone,
        direction=direction,
        body=body,
        wamid=wamid,
        message_type=message_type,
        session_id=session_id,
    )
    db.add(log)
    from sqlalchemy.exc import IntegrityError
    try:
        await db.flush()
    except IntegrityError as e:
        # If we got a duplicate wamid during concurrent processing, just ignore it
        if "unique constraint" in str(e).lower():
            logger.debug(f"[SESSION] Ignoring duplicate wamid log record: {wamid}")
            return None
        raise e
    return log


async def is_wamid_seen(db: AsyncSession, wamid: str) -> bool:
    """Check if we've already processed this message ID."""
    result = await db.execute(
        select(MessageLog.id).where(MessageLog.wamid == wamid)
    )
    return result.scalars().first() is not None


# ── Expired Session Cleanup ─────────────────────────────────────────


async def get_expired_sessions(db: AsyncSession) -> list[CheckoutSession]:
    """Get all non-terminal sessions past their expiry."""
    result = await db.execute(
        select(CheckoutSession).where(
            and_(
                CheckoutSession.state.notin_(TERMINAL_STATES),
                CheckoutSession.expires_at <= datetime.now(timezone.utc),
            )
        )
    )
    return list(result.scalars().all())


async def get_all_active_sessions(db: AsyncSession) -> list[CheckoutSession]:
    """Get all active sessions (admin use)."""
    result = await db.execute(
        select(CheckoutSession).where(
            CheckoutSession.state.notin_(TERMINAL_STATES)
        ).order_by(CheckoutSession.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_session_by_razorpay_link(db: AsyncSession, link_id: str) -> Optional[CheckoutSession]:
    """Find a session by its Razorpay link ID."""
    result = await db.execute(
        select(CheckoutSession).where(CheckoutSession.razorpay_link_id == link_id)
    )
    return result.scalars().first()


async def get_pending_sessions(db: AsyncSession) -> list[CheckoutSession]:
    """
    Get all sessions where checkout was initiated (cart sent to user)
    but the order has NOT been placed yet.
    These represent "pending" orders — customers who started but didn't finish.
    """
    result = await db.execute(
        select(CheckoutSession).where(
            and_(
                CheckoutSession.state.in_(PENDING_STATES),
                CheckoutSession.expires_at > datetime.now(timezone.utc),
            )
        ).order_by(CheckoutSession.updated_at.desc())
    )
    return list(result.scalars().all())
