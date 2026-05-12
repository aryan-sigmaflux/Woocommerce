"""
Tracking Link Monitor.
APScheduler job that polls WooCommerce order notes every 15 minutes,
detects tracking links, and sends WhatsApp notifications.
"""

import re
import logging

from clients import woocommerce as woo_client
from clients import whatsapp as wa_client

logger = logging.getLogger(__name__)

# Tracking URL patterns
_TRACKING_KEYWORDS = [
    "track", "tracking", "shiprocket", "delhivery", "dtdc",
    "bluedart", "ecom", "ekart", "xpressbees",
]
_URL_PATTERN = re.compile(r'https?://[^\s<>"]+', re.IGNORECASE)


def _extract_tracking_link(notes: list[dict]) -> str | None:
    """
    Scan order notes for tracking links.
    Priority: URLs containing tracking keywords, then any raw URL.
    """
    for note in notes:
        note_text = note.get("note", "")
        urls = _URL_PATTERN.findall(note_text)

        for url in urls:
            url_lower = url.lower()
            if any(kw in url_lower for kw in _TRACKING_KEYWORDS):
                return url

    # Fallback: return any URL from notes
    for note in notes:
        note_text = note.get("note", "")
        urls = _URL_PATTERN.findall(note_text)
        if urls:
            return urls[0]

    return None


def _get_order_meta(order: dict, key: str) -> str | None:
    """Extract a meta value from a WooCommerce order."""
    for meta in order.get("meta_data", []):
        if meta.get("key") == key:
            return str(meta.get("value", ""))
    return None


async def poll_tracking_updates():
    """
    Main polling job — runs every 15 minutes.
    
    1. Fetch orders with _wa_tracking_sent = false
    2. For each, check order notes for tracking links
    3. Send WhatsApp message with tracking link
    4. Update order meta to prevent duplicates
    """
    logger.info("[TRACKING] Polling for tracking updates...")

    try:
        # Get orders that are processing/completed and haven't had tracking sent
        # WooCommerce meta_key filtering may require custom plugin support,
        # so we fetch recent orders and filter client-side
        orders = await woo_client.get_orders(
            status="processing,completed",
            per_page=50,
            orderby="date",
            order="desc",
        )

        checked = 0
        sent = 0

        for order in orders:
            # Check if this is a WhatsApp order and tracking hasn't been sent
            is_wa_order = _get_order_meta(order, "_whatsapp_order") == "true"
            tracking_sent = _get_order_meta(order, "_wa_tracking_sent")

            if not is_wa_order or tracking_sent == "true":
                continue

            checked += 1
            order_id = order["id"]

            # Fetch order notes
            try:
                notes = await woo_client.get_order_notes(order_id)
            except Exception as e:
                logger.error(f"[TRACKING] Failed to fetch notes for order #{order_id}: {e}")
                continue

            # Look for tracking link
            tracking_link = _extract_tracking_link(notes)
            if not tracking_link:
                continue

            # Get customer phone
            phone = order.get("billing", {}).get("phone", "")
            if not phone:
                logger.warning(f"[TRACKING] No phone for order #{order_id}")
                continue

            from utils.phone import normalise_phone
            phone = normalise_phone(phone)

            # Send WhatsApp message
            message = (
                f"📦 Your order #{order_id} has been shipped!\n\n"
                f"Track your package:\n"
                f"🔗 {tracking_link}\n\n"
                f"Estimated delivery: 3–5 business days.\n"
                f"Any questions? Just reply here 😊"
            )

            try:
                await wa_client.send_text(phone, message, preview_url=True)
                sent += 1
                logger.info(f"[TRACKING] Sent tracking for order #{order_id} to {phone}")
            except Exception as e:
                logger.error(f"[TRACKING] Failed to send tracking for order #{order_id}: {e}")
                continue

            # Mark as sent in WooCommerce order meta
            try:
                await woo_client.update_order(order_id, {
                    "meta_data": [{"key": "_wa_tracking_sent", "value": "true"}]
                })
            except Exception as e:
                logger.error(f"[TRACKING] Failed to update meta for order #{order_id}: {e}")

        logger.info(f"[TRACKING] Poll complete: checked={checked}, sent={sent}")

    except Exception as e:
        logger.error(f"[TRACKING] Polling failed: {e}")
