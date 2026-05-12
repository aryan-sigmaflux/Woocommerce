"""
Payment state handler — COD and Razorpay payment link flows.
"""

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client
from clients import razorpay as rzp_client
from config import settings

logger = logging.getLogger(__name__)


async def handle_payment_method_select(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """PAYMENT_METHOD_SELECT — user chooses COD or online."""
    phone = session.phone

    if button_id == "pay_cod":
        await session_manager.update_session_state(
            db, session, "ORDER_CONFIRM_PROMPT",
            payment_method="cod",
        )
        from bot.states.confirm import enter_order_confirm
        await enter_order_confirm(db, session)
        return

    if button_id == "pay_online":
        await _create_and_send_payment_link(db, session)
        return

    # Unexpected
    count = await session_manager.increment_unexpected_input(db, session)
    if count >= 5:
        await wa_client.send_text(phone, '🔄 Type "RESTART" to start over.')
    else:
        await wa_client.send_buttons(
            to=phone,
            body="Please select a payment method:",
            buttons=[
                {"id": "pay_cod", "title": "🤝 Cash on Delivery"},
                {"id": "pay_online", "title": "💳 Pay Online"},
            ],
        )


async def _create_and_send_payment_link(db: AsyncSession, session: CheckoutSession):
    """Generate Razorpay payment link and send to user."""
    phone = session.phone
    total = Decimal(str(session.total or 0))
    amount_paise = int(total * 100)

    # Get customer name from address
    name = "Customer"
    if session.selected_address_id:
        address = await session_manager.get_address_by_id(db, session.selected_address_id)
        if address:
            name = address.full_name

    try:
        if settings.DUMMY_RAZORPAY:
            link_id = f"dummy_{session.id}"
            short_url = "https://rzp.io/l/dummy_payment"
            logger.info(f"[PAYMENT] Using dummy Razorpay link for session {session.id}")
            
            buttons = [
                {"id": "pay_simulate_dev", "title": "⚡ Simulate Payment"},
                {"id": "pay_switch_cod", "title": "🤝 Switch to COD"},
            ]
        else:
            link_data = await rzp_client.create_payment_link(
                amount_paise=amount_paise,
                customer_name=name,
                customer_phone=phone,
                description=f"WhatsApp Order",
            )
            link_id = link_data.get("id", "")
            short_url = link_data.get("short_url", "")
            buttons = [
                {"id": "pay_switch_cod", "title": "🤝 Switch to COD"},
            ]

        await session_manager.update_session_state(
            db, session, "PAYMENT_LINK_SENT",
            payment_method="razorpay",
            razorpay_link_id=link_id,
        )

        await wa_client.send_buttons(
            to=phone,
            body=(
                f"💳 Complete your payment:\n"
                f"{short_url}\n\n"
                f"Link expires in {settings.PAYMENT_LINK_EXPIRY_MINUTES} minutes. "
                f"Order confirmed automatically on payment."
            ),
            buttons=buttons,
        )

    except Exception as e:
        logger.error(f"[PAYMENT] Failed to create Razorpay link: {e}")
        await wa_client.send_buttons(
            to=phone,
            body=(
                "⚠️ Something went wrong creating your payment link.\n"
                "Would you like to try again or switch to Cash on Delivery?"
            ),
            buttons=[
                {"id": "pay_online", "title": "🔄 Try Again"},
                {"id": "pay_cod", "title": "🤝 Cash on Delivery"},
            ],
        )


async def handle_payment_link_sent(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """PAYMENT_LINK_SENT — waiting for payment or user action."""
    phone = session.phone

    if button_id == "pay_switch_cod" or (message_text and message_text.strip().lower() == "cod"):
        # Cancel the Razorpay link
        if session.razorpay_link_id:
            try:
                await rzp_client.cancel_payment_link(session.razorpay_link_id)
            except Exception as e:
                logger.warning(f"[PAYMENT] Failed to cancel Razorpay link: {e}")

        await session_manager.update_session_state(
            db, session, "ORDER_CONFIRM_PROMPT",
            payment_method="cod",
            razorpay_link_id=None,
        )
        from bot.states.confirm import enter_order_confirm
        await enter_order_confirm(db, session)
        return

    if button_id == "pay_new_link":
        await _create_and_send_payment_link(db, session)
        return

    if button_id == "pay_simulate_dev":
        logger.info(f"[PAYMENT] Simulating payment for session {session.id}")
        await handle_payment_captured(db, session, f"pay_dummy_{session.id}")
        return

    # Default: remind about the payment link
    await wa_client.send_text(
        phone,
        "⏳ We're waiting for your payment. Complete the payment using the link above, "
        "or tap below to switch to Cash on Delivery."
    )


async def handle_payment_timeout(db: AsyncSession, session: CheckoutSession):
    """Called when the payment link expires (30 min)."""
    phone = session.phone
    await wa_client.send_buttons(
        to=phone,
        body="⏰ Payment link expired.",
        buttons=[
            {"id": "pay_new_link", "title": "🔄 New Payment Link"},
            {"id": "pay_switch_cod", "title": "🤝 Switch to COD"},
        ],
    )


async def handle_payment_captured(
    db: AsyncSession,
    session: CheckoutSession,
    razorpay_payment_id: str,
):
    """Called when Razorpay webhook confirms payment captured."""
    phone = session.phone

    await session_manager.update_session_state(
        db, session, "ORDER_PLACING",
        razorpay_payment_id=razorpay_payment_id,
    )

    await wa_client.send_text(phone, "✅ Payment received! Confirming your order...")

    # Auto-advance to order creation
    from bot.states.confirm import create_confirmed_order
    await create_confirmed_order(db, session)
