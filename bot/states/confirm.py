"""
Confirm state handler — final confirmation, order creation, and session archival.
"""

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client
from clients import woocommerce as woo_client
from templates.message_templates import send_order_confirmed

logger = logging.getLogger(__name__)


async def enter_order_confirm(db: AsyncSession, session: CheckoutSession):
    """Send the confirmation prompt with full summary."""
    phone = session.phone

    from bot.states.summary import build_full_summary_with_address
    summary = await build_full_summary_with_address(db, session)

    payment_label = "Cash on Delivery" if session.payment_method == "cod" else "Razorpay (Online)"
    summary += f"\n\n💰 Payment: {payment_label}"

    await wa_client.send_text(phone, summary)

    await session_manager.update_session_state(db, session, "ORDER_CONFIRM_PROMPT")
    await wa_client.send_buttons(
        to=phone,
        body="Ready to place your order?",
        buttons=[
            {"id": "order_confirm", "title": "✅ Confirm Order"},
            {"id": "order_edit_addr", "title": "✏️ Edit Address"},
            {"id": "order_cancel", "title": "❌ Cancel"},
        ],
    )


async def handle_order_confirm_prompt(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """ORDER_CONFIRM_PROMPT — confirm, edit address, or cancel."""
    phone = session.phone

    if button_id == "order_confirm":
        await session_manager.update_session_state(db, session, "ORDER_PLACING")
        await wa_client.send_text(phone, "⏳ Placing your order...")
        await create_confirmed_order(db, session)
        return

    if button_id == "order_edit_addr":
        # Go back to address check, preserving cart + coupon + payment
        from bot.states.address import enter_address_check
        await enter_address_check(db, session)
        return

    if button_id == "order_cancel":
        # Cancel pending Woo order
        if session.woo_pending_order_id:
            try:
                await woo_client.update_order(
                    session.woo_pending_order_id,
                    {"status": "cancelled"}
                )
            except Exception as e:
                logger.error(f"[CONFIRM] Failed to cancel Woo order: {e}")

        await session_manager.cancel_session(db, session)
        await wa_client.send_text(
            phone,
            "❌ Order cancelled. Browse our catalog again anytime! 🛍️"
        )
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
                {"id": "order_confirm", "title": "✅ Confirm Order"},
                {"id": "order_edit_addr", "title": "✏️ Edit Address"},
                {"id": "order_cancel", "title": "❌ Cancel"},
            ],
        )


async def create_confirmed_order(db: AsyncSession, session: CheckoutSession):
    """
    Finalize the order: update the existing pending WooCommerce order to 'processing'
    with full address, payment, and shipping info. Falls back to creating a new order
    if no pending order exists.
    """
    phone = session.phone

    # Get address
    address = None
    if session.selected_address_id:
        address = await session_manager.get_address_by_id(db, session.selected_address_id)

    if not address:
        await wa_client.send_text(phone, "⚠️ No delivery address found. Please try again.")
        from bot.states.address import enter_address_check
        await enter_address_check(db, session)
        return

    # Extract email if we saved it from the Flow
    email = ""
    for item in (session.cart or []):
        if isinstance(item, dict) and item.get("_meta"):
            email = item.get("email", "")
            break
            
    # Filter out temp/meta dicts from items
    cart_items = [item for item in (session.cart or []) if not any(k.startswith("_") for k in item)]

    # Split full_name into first/last
    name_parts = (address.full_name or "").split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    is_paid = session.payment_method == "razorpay" and session.razorpay_payment_id
    payment_title = "Cash on Delivery" if session.payment_method == "cod" else "Razorpay"

    address_data = {
        "first_name": first_name,
        "last_name": last_name,
        "address_1": address.line1,
        "address_2": address.line2 or "",
        "city": address.city,
        "state": address.state,
        "postcode": address.pincode,
        "country": "IN",
        "phone": f"+{phone}" if not phone.startswith("+") else phone,
    }
    
    billing_data = dict(address_data)
    if email:
        billing_data["email"] = email

    final_line_items = []
    for item in cart_items:
        p_id = item.get("woo_product_id")
        if isinstance(p_id, str):
            # If it's a SKU string, resolve it to an ID
            logger.info(f"[CONFIRM] Resolving SKU {p_id} to ID...")
            sku_data = await woo_client.get_product_by_sku(p_id)
            if sku_data:
                p_id = sku_data["id"]
        
        final_line_items.append({
            "product_id": p_id,
            "quantity": item.get("qty", 1),
        })

    order_payload = {
        "status": "processing",
        "payment_method": session.payment_method or "cod",
        "payment_method_title": payment_title,
        "set_paid": bool(is_paid),
        "billing": billing_data,
        "shipping": address_data,
        "line_items": final_line_items,
        "shipping_lines": [
            {
                "method_id": "whatsapp_flat",
                "method_title": "WhatsApp Delivery",
                "total": str(float(session.shipping_cost or 0)),
            }
        ],
        "meta_data": [
            {"key": "_whatsapp_order", "value": "true"},
            {"key": "_whatsapp_session_id", "value": str(session.id)},
            {"key": "_wa_tracking_sent", "value": "false"},
            {"key": "_whatsapp_checkout_status", "value": "completed"},
        ],
    }

    # Add coupon if applied
    if session.coupon_code:
        order_payload["coupon_lines"] = [{"code": session.coupon_code}]

    # Add Razorpay transaction ID if paid online
    if session.razorpay_payment_id:
        order_payload["transaction_id"] = session.razorpay_payment_id

    try:
        confirmed_order_id = None

        if session.woo_pending_order_id:
            # Update the existing pending order to "processing"
            logger.info(f"[CONFIRM] Updating pending order #{session.woo_pending_order_id} to processing")
            woo_order = await woo_client.update_order(session.woo_pending_order_id, order_payload)
            confirmed_order_id = woo_order.get("id")
            logger.info(f"[CONFIRM] WooCommerce order #{confirmed_order_id} updated to processing for {phone}")
        else:
            # No pending order exists — create a new one directly as processing
            logger.info(f"[CONFIRM] No pending order found, creating new order for {phone}")
            woo_order = await woo_client.create_order(order_payload)
            confirmed_order_id = woo_order.get("id")
            logger.info(f"[CONFIRM] WooCommerce order #{confirmed_order_id} created for {phone}")

        # Complete the session
        await session_manager.complete_session(db, session, confirmed_order_id)

        # Send confirmation message
        await send_order_confirmed(phone, confirmed_order_id)

    except Exception as e:
        logger.error(f"[CONFIRM] Failed to finalize WooCommerce order: {e}")

        # Check if the coupon was rejected (check response body if it's an HTTP error)
        error_msg = str(e).lower()
        import httpx
        if isinstance(e, httpx.HTTPStatusError):
             error_msg += " " + e.response.text.lower()

        if "coupon" in error_msg:
            # Strip coupon and retry
            await session_manager.update_session_state(
                db, session, "ORDER_CONFIRM_PROMPT",
                coupon_code=None,
                discount_amount=Decimal("0"),
                discount_type=None,
            )
            # Recalculate total
            subtotal = Decimal(str(session.subtotal))
            shipping = Decimal(str(session.shipping_cost or 0))
            session.total = subtotal + shipping
            await db.flush()

            await wa_client.send_text(
                phone,
                "⚠️ This coupon is not applicable to your order. We've removed it and recalculated your total."
            )
            await enter_order_confirm(db, session)
            return

        await wa_client.send_text(
            phone,
            "⚠️ We're experiencing a small hiccup placing your order. Please try again in a few minutes."
        )
        await wa_client.send_buttons(
            to=phone,
            body="Would you like to try again?",
            buttons=[
                {"id": "order_confirm", "title": "🔄 Try Again"},
                {"id": "order_cancel", "title": "❌ Cancel"},
            ],
        )
