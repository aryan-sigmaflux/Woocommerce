"""
WhatsApp template message builders.
Builds the component payloads for approved Meta templates.
"""

from clients import whatsapp as wa_client


async def send_checkout_welcome(to: str, order_id: int):
    """
    Send the checkout_welcome template (first contact — must be approved).
    Fallback to interactive buttons if within 24h window.
    """
    # Try template first (for first-contact outside 24h window)
    try:
        return await wa_client.send_template(
            to=to,
            template_name="checkout_welcome",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(order_id)},
                    ],
                }
            ],
        )
    except Exception:
        # Fallback: send as interactive button (works within 24h window)
        return await wa_client.send_buttons(
            to=to,
            body=(
                "🛍️ *Your order is being prepared!*\n\n"
                "Let's complete your checkout — just a few quick steps.\n\n"
                "Tap below to continue 👇"
            ),
            buttons=[
                {"id": "btn_continue_checkout", "title": "Continue Checkout"},
                {"id": "btn_cancel", "title": "Cancel Order"},
            ],
        )


async def send_order_confirmed(to: str, order_id: int):
    """Send order confirmed message."""
    return await wa_client.send_text(
        to=to,
        body=(
            f"✅ *Order #{order_id} confirmed!* 🎉\n\n"
            f"Thank you for your order. We'll notify you when it ships.\n"
            f"Any questions? Just reply here 😊"
        ),
    )


async def send_tracking_update(to: str, order_id: int, tracking_link: str):
    """Send tracking update template/message."""
    try:
        return await wa_client.send_template(
            to=to,
            template_name="tracking_update",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(order_id)},
                        {"type": "text", "text": tracking_link},
                    ],
                }
            ],
        )
    except Exception:
        return await wa_client.send_text(
            to=to,
            body=(
                f"📦 Your order #{order_id} has been shipped!\n\n"
                f"Track your package:\n🔗 {tracking_link}\n\n"
                f"Estimated delivery: 3–5 business days.\n"
                f"Any questions? Just reply here 😊"
            ),
            preview_url=True,
        )


async def send_session_expired(to: str):
    """Send session expired template/message."""
    try:
        return await wa_client.send_template(
            to=to,
            template_name="session_expired",
        )
    except Exception:
        return await wa_client.send_text(
            to=to,
            body=(
                "⏰ Your checkout session has expired.\n\n"
                "Browse our catalog again to start a new order. 🛍️"
            ),
        )
