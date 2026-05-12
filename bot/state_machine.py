"""
State Machine — routes incoming messages to the correct state handler.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from bot.states.welcome import handle_welcome
from bot.states.pincode import handle_pincode_check
from bot.states.address import (
    handle_address_check,
    handle_collecting_name,
    handle_collecting_line1,
    handle_collecting_line2,
    handle_collecting_city,
    handle_collecting_state,
    handle_collecting_pincode,
    handle_address_confirm,
)
from bot.states.coupon import handle_coupon_prompt, handle_collecting_coupon
from bot.states.payment import handle_payment_method_select, handle_payment_link_sent
from bot.states.confirm import handle_order_confirm_prompt
from clients import whatsapp as wa_client

logger = logging.getLogger(__name__)


async def route_message(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str = "",
    button_id: str = None,
    list_id: str = None,
):
    """
    Main entry point: route an incoming message/button/list selection
    to the correct state handler based on the session's current state.
    """
    state = session.state
    phone = session.phone

    # Handle RESTART command from any state
    if message_text and message_text.strip().upper() == "RESTART":
        await _restart_session(db, session)
        return

    logger.info(f"[STATE] {phone} | state={state} | text={message_text[:50] if message_text else ''} | btn={button_id} | list={list_id}")

    # ── State Routing ───────────────────────────────────────────────

    if state == "WELCOME_SENT":
        await handle_welcome(db, session, message_text, button_id)

    elif state == "COLLECTING_PINCODE_CHECK":
        await handle_pincode_check(db, session, message_text)

    elif state == "ADDRESS_CHECK":
        await handle_address_check(db, session, message_text, button_id, list_id)

    elif state == "COLLECTING_ADDRESS_NAME":
        await handle_collecting_name(db, session, message_text)

    elif state == "COLLECTING_ADDRESS_LINE1":
        await handle_collecting_line1(db, session, message_text)

    elif state == "COLLECTING_ADDRESS_LINE2":
        await handle_collecting_line2(db, session, message_text)

    elif state == "COLLECTING_ADDRESS_CITY":
        await handle_collecting_city(db, session, message_text)

    elif state == "COLLECTING_ADDRESS_STATE":
        await handle_collecting_state(db, session, message_text)

    elif state == "COLLECTING_ADDRESS_PINCODE":
        await handle_collecting_pincode(db, session, message_text)

    elif state == "ADDRESS_CONFIRM":
        await handle_address_confirm(db, session, message_text, button_id)

    elif state == "COUPON_PROMPT":
        await handle_coupon_prompt(db, session, message_text, button_id)

    elif state == "COLLECTING_COUPON":
        await handle_collecting_coupon(db, session, message_text, button_id)

    elif state == "ORDER_SUMMARY":
        # Summary is auto-advancing, shouldn't receive input here normally
        # Treat as unexpected, re-enter payment selection
        from bot.states.summary import enter_payment_method_select
        await enter_payment_method_select(db, session)

    elif state == "PAYMENT_METHOD_SELECT":
        await handle_payment_method_select(db, session, message_text, button_id)

    elif state == "PAYMENT_LINK_SENT":
        await handle_payment_link_sent(db, session, message_text, button_id)

    elif state == "ORDER_CONFIRM_PROMPT":
        await handle_order_confirm_prompt(db, session, message_text, button_id)

    elif state == "ORDER_PLACING":
        # Order is being placed, don't interrupt
        await wa_client.send_text(phone, "⏳ Your order is being placed. Please wait a moment...")

    elif state in session_manager.TERMINAL_STATES:
        # Session is closed
        await wa_client.send_text(
            phone,
            "You don't have an active checkout session. "
            "Browse our catalog and place a new order! 🛍️"
        )

    else:
        logger.warning(f"[STATE] Unknown state: {state} for {phone}")
        await wa_client.send_text(
            phone,
            "Something went wrong. Please browse our catalog and start a new order. 🛍️"
        )


async def _restart_session(db: AsyncSession, session: CheckoutSession):
    """Cancel current session and prompt user to start fresh."""
    phone = session.phone
    await session_manager.cancel_session(db, session)
    await wa_client.send_text(
        phone,
        "🔄 Session restarted.\n\n"
        "Browse our catalog and place a new order to begin checkout. 🛍️"
    )
