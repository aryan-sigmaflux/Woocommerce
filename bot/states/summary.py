"""
Order summary state handler — builds and sends the summary message.
"""

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client
from engines.shipping import calculate_shipping

logger = logging.getLogger(__name__)


async def enter_order_summary(db: AsyncSession, session: CheckoutSession):
    """Build and send the order summary, then advance to payment method selection."""
    phone = session.phone

    # Calculate totals
    cart_items = [item for item in (session.cart or []) if not any(k.startswith("_") for k in item)]
    subtotal = Decimal(str(session.subtotal))
    discount = Decimal(str(session.discount_amount or 0))
    shipping = Decimal(str(calculate_shipping(cart_items, subtotal=subtotal)))

    total = subtotal - discount + shipping

    # Update session with calculated values
    await session_manager.update_session_state(
        db, session, "ORDER_SUMMARY",
        shipping_cost=shipping,
        total=total,
    )

    # Build summary message with address
    summary = await build_full_summary_with_address(db, session)

    await wa_client.send_text(phone, summary)

    # Advance to payment method selection
    await enter_payment_method_select(db, session)


async def enter_payment_method_select(db: AsyncSession, session: CheckoutSession):
    """Send payment method selection buttons."""
    await session_manager.update_session_state(db, session, "PAYMENT_METHOD_SELECT")
    await wa_client.send_buttons(
        to=session.phone,
        body="💰 How would you like to pay?",
        buttons=[
            {"id": "pay_cod", "title": "🤝 Cash on Delivery"},
            {"id": "pay_online", "title": "💳 Pay Online"},
        ],
    )


def _build_summary_message(
    session: CheckoutSession,
    cart_items: list[dict],
    subtotal: Decimal,
    discount: Decimal,
    shipping: Decimal,
    total: Decimal,
) -> str:
    """Build a formatted order summary text message."""
    lines = ["🛍️ *Order Summary*\n", "📦 Items:"]

    for item in cart_items:
        name = item.get("name", "Item")
        qty = item.get("qty", 1)
        unit_price = float(item.get("unit_price", 0))
        line_total = unit_price * qty
        lines.append(f"  • {name} × {qty} — ₹{line_total:,.0f}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Subtotal:           ₹{subtotal:,.2f}")

    if discount > 0:
        coupon_code = session.coupon_code or ""
        discount_type = session.discount_type or ""
        if discount_type == "percentage":
            lines.append(f"Discount ({coupon_code}):  -₹{discount:,.2f}")
        else:
            lines.append(f"Discount ({coupon_code}):  -₹{discount:,.2f}")

    if shipping > 0:
        lines.append(f"Delivery:           ₹{shipping:,.2f}")
    else:
        lines.append(f"Delivery:           FREE ✨")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"*Total:             ₹{total:,.2f}*")

    return "\n".join(lines)


async def build_full_summary_with_address(
    db: AsyncSession,
    session: CheckoutSession,
) -> str:
    """Build the full summary including the delivery address."""
    cart_items = [item for item in (session.cart or []) if not any(k.startswith("_") for k in item)]
    subtotal = Decimal(str(session.subtotal))
    discount = Decimal(str(session.discount_amount or 0))
    shipping = Decimal(str(session.shipping_cost or 0))
    total = Decimal(str(session.total or 0))

    summary = _build_summary_message(session, cart_items, subtotal, discount, shipping, total)

    # Append address
    if session.selected_address_id:
        address = await session_manager.get_address_by_id(db, session.selected_address_id)
        if address:
            addr_parts = [address.full_name, address.line1]
            if address.line2:
                addr_parts[1] += f", {address.line2}"
            addr_parts.append(f"{address.city}, {address.state} - {address.pincode}")
            summary += "\n" + "\n".join(addr_parts)

    return summary
