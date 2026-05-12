"""
Pincode check state handler — asks the user for their delivery pincode
BEFORE starting address collection. Validates serviceability and persists
the pincode so returning users aren't asked again.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client
from utils.pincode_service import is_pincode_serviceable

logger = logging.getLogger(__name__)


async def enter_pincode_check(db: AsyncSession, session: CheckoutSession):
    """
    Entry point: check if we already have a validated pincode for this phone.
    If yes, skip straight to address. If no, ask for it.
    """
    phone = session.phone

    # Check 1: Does this session already have a validated pincode in cart meta?
    saved_pincode = _get_saved_pincode(session.cart)
    if saved_pincode:
        logger.info(f"[PINCODE] {phone} has pincode {saved_pincode} in session, skipping check")
        await _advance_to_address(db, session)
        return

    # Check 2: Was pincode already validated during the greeting flow? (in-memory cache)
    from utils.pincode_service import get_validated_pincode
    cached_pincode = get_validated_pincode(phone)
    if cached_pincode and is_pincode_serviceable(cached_pincode):
        logger.info(f"[PINCODE] {phone} has greeting-validated pincode {cached_pincode}, skipping check")
        session.cart = _set_saved_pincode(session.cart, cached_pincode)
        await _advance_to_address(db, session)
        return

    # Check 3: Does this user have any saved addresses with a pincode we can reuse?
    addresses = await session_manager.get_addresses_for_phone(db, phone)
    if addresses:
        # Use the most recent (last-used or first) address's pincode
        last_used = next((a for a in addresses if a.is_last_used), addresses[0])
        pincode = last_used.pincode

        if is_pincode_serviceable(pincode):
            logger.info(f"[PINCODE] {phone} has saved address with serviceable pincode {pincode}")
            session.cart = _set_saved_pincode(session.cart, pincode)
            await _advance_to_address(db, session)
            return
        else:
            # Their saved pincode is no longer serviceable
            logger.info(f"[PINCODE] {phone} saved pincode {pincode} is NOT serviceable")

    # No saved pincode — ask the user
    await session_manager.update_session_state(db, session, "COLLECTING_PINCODE_CHECK")
    await wa_client.send_text(
        phone,
        "📍 *Before we proceed, please enter your delivery pincode*\n\n"
        "We'll check if delivery is available in your area."
    )


async def handle_pincode_check(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """
    State: COLLECTING_PINCODE_CHECK
    User sends their pincode. We validate format + serviceability.
    """
    phone = session.phone
    pincode = message_text.strip()

    # Validate format (Indian pincode: 6 digits)
    if not pincode or not pincode.isdigit() or len(pincode) != 6:
        await wa_client.send_text(
            phone,
            "⚠️ Please enter a valid 6-digit pincode.\n\n"
            "Example: *600001*"
        )
        return

    # Check serviceability
    if not is_pincode_serviceable(pincode):
        logger.info(f"[PINCODE] {phone} pincode {pincode} is NOT serviceable — cancelling session")

        # Cancel the WooCommerce order if we have one
        if session.woo_pending_order_id:
            try:
                from clients import woocommerce as woo_client
                await woo_client.update_order(
                    session.woo_pending_order_id,
                    {"status": "cancelled"}
                )
            except Exception as e:
                logger.error(f"[PINCODE] Failed to cancel Woo order #{session.woo_pending_order_id}: {e}")

        await session_manager.cancel_session(db, session)
        await wa_client.send_text(
            phone,
            "😔 *Sorry, delivery is not available at pincode {pincode}.*\n\n"
            "We currently deliver only to select areas. "
            "We're expanding soon — stay tuned! 🚀\n\n"
            "Browse our catalog anytime to check back later. 🛍️".replace("{pincode}", pincode)
        )
        return

    # Pincode is serviceable! Save it and advance.
    logger.info(f"[PINCODE] {phone} pincode {pincode} is serviceable ✅")
    session.cart = _set_saved_pincode(session.cart, pincode)

    # Also save to in-memory cache for greeting flow
    from utils.pincode_service import set_validated_pincode
    set_validated_pincode(phone, pincode)

    await wa_client.send_text(phone, f"✅ Great! We deliver to *{pincode}*. Let's continue!")
    await _advance_to_address(db, session)


async def _advance_to_address(db: AsyncSession, session: CheckoutSession):
    """Move to the address check state."""
    from bot.states.address import enter_address_check
    await enter_address_check(db, session)


# ── Helpers ─────────────────────────────────────────────────────────


def _get_saved_pincode(cart: list) -> str | None:
    """Extract the saved/validated pincode from cart metadata."""
    for item in (cart or []):
        if isinstance(item, dict) and item.get("_pincode_check"):
            return item.get("pincode")
    return None


def _set_saved_pincode(cart: list, pincode: str) -> list:
    """Store the validated pincode in cart metadata."""
    import copy
    cart_copy = copy.deepcopy(cart) if cart else []

    for item in cart_copy:
        if isinstance(item, dict) and item.get("_pincode_check"):
            item["pincode"] = pincode
            return cart_copy

    cart_copy.append({"_pincode_check": True, "pincode": pincode})
    return cart_copy
