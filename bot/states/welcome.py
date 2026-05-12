"""
Welcome state handler — entry point after order webhook.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client
from clients import woocommerce as woo_client

logger = logging.getLogger(__name__)


async def handle_welcome(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """
    State: WELCOME_SENT
    User received the welcome message with [Continue Checkout] [Cancel] buttons.
    """
    phone = session.phone

    if button_id == "btn_cancel":
        # Cancel the pending WooCommerce order
        if session.woo_pending_order_id:
            try:
                await woo_client.update_order(
                    session.woo_pending_order_id,
                    {"status": "cancelled"}
                )
            except Exception as e:
                logger.error(f"[WELCOME] Failed to cancel Woo order #{session.woo_pending_order_id}: {e}")

        await session_manager.cancel_session(db, session)
        await wa_client.send_text(
            phone,
            "❌ Order cancelled. Browse our catalog again anytime you'd like! 🛍️"
        )
        return

    if button_id == "btn_continue_checkout" or (message_text and message_text.strip().lower() in ["continue", "yes", "checkout"]):
        # Move to pincode check first
        await _enter_pincode_check(db, session)
        return

    # Unexpected input
    count = await session_manager.increment_unexpected_input(db, session)
    if count >= 5:
        await wa_client.send_text(phone, '🔄 Type "RESTART" to start over.')
    else:
        await wa_client.send_buttons(
            to=phone,
            body="Please tap one of the buttons below to continue 👇",
            buttons=[
                {"id": "btn_continue_checkout", "title": "Continue Checkout"},
                {"id": "btn_cancel", "title": "Cancel Order"},
            ],
        )


async def send_welcome_message(
    db: AsyncSession,
    session: CheckoutSession,
):
    """Send the initial welcome message when a new session is created."""
    phone = session.phone

    # Build cart summary
    cart_lines = []
    for item in session.cart:
        name = item.get("name", "Item")
        qty = item.get("qty", 1)
        price = item.get("unit_price", 0)
        cart_lines.append(f"  • {name} × {qty} — ₹{float(price) * qty:,.0f}")

    cart_text = "\n".join(cart_lines) if cart_lines else "  Your selected items"

    body = (
        f"🛍️ *Your order is being prepared!*\n\n"
        f"📦 Items:\n{cart_text}\n\n"
        f"Let's complete your checkout — just a few quick steps."
    )

    await wa_client.send_text(to=phone, body=body)

    # Immediately advance to pincode check (which then advances to address)
    await _enter_pincode_check(db, session)


async def _enter_pincode_check(db: AsyncSession, session: CheckoutSession):
    """Transition to PINCODE_CHECK — validate delivery area first."""
    from bot.states.pincode import enter_pincode_check
    await enter_pincode_check(db, session)


async def _enter_address_check(db: AsyncSession, session: CheckoutSession):
    """Transition to ADDRESS_CHECK — check for saved addresses."""
    from bot.states.address import enter_address_check
    await enter_address_check(db, session)
