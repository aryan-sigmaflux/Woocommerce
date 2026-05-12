"""
Coupon state handler — prompt, validate, and apply discount.
Shows available coupons fetched from WooCommerce.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client
from clients import woocommerce as woo_client
from engines.coupon import validate_coupon, calculate_discount, CouponValidationError
from config import settings

logger = logging.getLogger(__name__)


async def _fetch_available_coupons() -> list[dict]:
    """Fetch active, non-expired coupons from WooCommerce."""
    try:
        coupons = await woo_client.get_all_active_coupons(per_page=20)
        now = datetime.now(timezone.utc)

        valid_coupons = []
        for c in coupons:
            # Skip expired coupons
            date_expires = c.get("date_expires")
            if date_expires:
                try:
                    expires_dt = datetime.fromisoformat(date_expires.replace("Z", "+00:00"))
                    if expires_dt < now:
                        continue
                except (ValueError, TypeError):
                    pass

            # Skip fully used coupons
            usage_limit = c.get("usage_limit")
            usage_count = c.get("usage_count", 0)
            if usage_limit and usage_count >= usage_limit:
                continue

            valid_coupons.append(c)

        return valid_coupons
    except Exception as e:
        logger.warning(f"[COUPON] Failed to fetch coupons from WooCommerce: {e}")
        return []


def _format_coupon_display(coupon: dict) -> str:
    """Format a single coupon for display."""
    code = coupon.get("code", "").upper()
    discount_type = coupon.get("discount_type", "")
    amount = coupon.get("amount", "0")
    min_amount = coupon.get("minimum_amount", "")

    if discount_type == "percent":
        desc = f"{amount}% off"
    elif discount_type == "fixed_cart":
        desc = f"₹{float(amount):,.0f} off on cart"
    elif discount_type == "fixed_product":
        desc = f"₹{float(amount):,.0f} off per item"
    else:
        desc = f"₹{float(amount):,.0f} discount"

    if min_amount and float(min_amount) > 0:
        desc += f" (min ₹{float(min_amount):,.0f})"

    return f"  🏷️ *{code}* — {desc}"


async def enter_coupon_prompt(db: AsyncSession, session: CheckoutSession):
    """Transition to COUPON_PROMPT — show available coupons and ask if user wants to apply one."""
    await session_manager.update_session_state(db, session, "COUPON_PROMPT")

    # Fetch available coupons
    coupons = await _fetch_available_coupons()

    if coupons:
        coupon_lines = [_format_coupon_display(c) for c in coupons]
        coupon_list = "\n".join(coupon_lines)

        body = (
            f"🏷️ *Available Coupons:*\n\n"
            f"{coupon_list}\n\n"
            f"Want to apply a coupon code?"
        )
    else:
        body = "🏷️ Do you have a discount or coupon code?"

    await wa_client.send_buttons(
        to=session.phone,
        body=body,
        buttons=[
            {"id": "coupon_yes", "title": "Yes, apply a code"},
            {"id": "coupon_skip", "title": "Skip"},
        ],
    )


async def handle_coupon_prompt(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """Handle the coupon prompt response."""
    phone = session.phone

    if button_id == "coupon_skip" or (message_text and message_text.strip().lower() in ["skip", "no"]):
        # Skip coupon → go to order summary
        from bot.states.summary import enter_order_summary
        await enter_order_summary(db, session)
        return

    if button_id == "coupon_yes" or (message_text and message_text.strip().lower() in ["yes", "code"]):
        await session_manager.update_session_state(db, session, "COLLECTING_COUPON")
        await wa_client.send_text(phone, "Enter your coupon code:")
        return

    # Unexpected
    count = await session_manager.increment_unexpected_input(db, session)
    if count >= 5:
        await wa_client.send_text(phone, '🔄 Type "RESTART" to start over.')
    else:
        await wa_client.send_buttons(
            to=phone,
            body="Please choose an option:",
            buttons=[
                {"id": "coupon_yes", "title": "Yes, apply a code"},
                {"id": "coupon_skip", "title": "Skip"},
            ],
        )


async def handle_collecting_coupon(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """
    COLLECTING_COUPON — validate the entered coupon code against WooCommerce.
    Max 3 attempts, then auto-skip.
    """
    phone = session.phone
    
    # If the user clicked a button from an older state instead of typing a code,
    # it's an unexpected input. Let them know we expect text.
    if button_id:
        await wa_client.send_text(phone, "Please type your coupon code below, or type 'skip' to proceed without one.")
        return

    code = message_text.strip().upper()

    if not code:
        await wa_client.send_text(phone, "Please enter a coupon code, or type 'skip' to skip.")
        return

    if code.lower() == "skip":
        from bot.states.summary import enter_order_summary
        await enter_order_summary(db, session)
        return

    # Increment attempt counter
    attempts = await session_manager.increment_coupon_attempts(db, session)

    # Filter out temp address entries from cart for coupon validation
    cart_items = [item for item in (session.cart or []) if not any(k.startswith("_") for k in item)]
    subtotal = Decimal(str(session.subtotal))

    try:
        coupon = await validate_coupon(code, cart_items, subtotal, phone)
    except CouponValidationError as e:
        if attempts >= settings.MAX_COUPON_ATTEMPTS:
            await wa_client.send_text(
                phone,
                f"❌ {str(e)}\n\nMaximum attempts reached. Continuing without coupon."
            )
            from bot.states.summary import enter_order_summary
            await enter_order_summary(db, session)
            return

        remaining = settings.MAX_COUPON_ATTEMPTS - attempts
        await wa_client.send_text(
            phone,
            f"❌ {str(e)}\n\nTry another code ({remaining} attempt{'s' if remaining != 1 else ''} left), or type 'skip'."
        )
        return

    # Valid coupon — calculate discount
    discount_amount, discount_type = calculate_discount(coupon, cart_items, subtotal)

    # Format discount for display
    coupon_amount = coupon.get("amount", "0")
    if discount_type == "percentage":
        discount_display = f"{code} - {coupon_amount}%"
    else:
        discount_display = code

    await session_manager.update_session_state(
        db, session, "COUPON_PROMPT",  # Temporary — will be overwritten by enter_order_summary
        coupon_code=code,
        discount_amount=discount_amount,
        discount_type=discount_type,
    )

    await wa_client.send_text(
        phone,
        f"✅ Coupon *{code}* applied! Discount: ₹{discount_amount:,.2f}"
    )

    # Advance to order summary
    from bot.states.summary import enter_order_summary
    await enter_order_summary(db, session)
