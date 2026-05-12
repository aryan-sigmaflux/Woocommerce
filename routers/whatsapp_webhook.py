"""
WhatsApp Webhook Router.
GET  /webhooks/whatsapp — Meta verification (hub.challenge)
POST /webhooks/whatsapp — Inbound messages + delivery status
"""

import logging

from fastapi import APIRouter, Request, Query, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.database import get_db
from bot import session_manager
from bot.state_machine import route_message
from clients import whatsapp as wa_client
from clients import woocommerce as woo_client
from utils.phone import normalise_phone
from utils.signature import verify_meta_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhook"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta webhook verification endpoint.
    Returns hub.challenge if verify_token matches.
    """
    logger.info(f"[WA-HOOK] Verify attempt: mode={hub_mode}, token_received={hub_verify_token}, token_expected={settings.META_VERIFY_TOKEN}")
    if hub_mode == "subscribe" and hub_verify_token and hub_verify_token.strip() == settings.META_VERIFY_TOKEN.strip():
        logger.info("[WA-HOOK] Webhook verified successfully")
        return int(hub_challenge) if hub_challenge else ""
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("")
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Process incoming WhatsApp messages and status updates.
    """
    body = await request.json()

    # Process each entry
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Skip non-message events (statuses, errors, etc.)
            messages = value.get("messages", [])
            if not messages:
                continue

            for message in messages:
                await _process_message(db, message, value)

    return {"status": "ok"}


async def _process_message(db: AsyncSession, message: dict, value: dict):
    """Process a single incoming WhatsApp message."""
    # Extract sender info
    from_number = message.get("from", "")
    wamid = message.get("id", "")
    msg_type = message.get("type", "")

    if not from_number or not wamid:
        return

    # Ignore system messages, reactions, etc.
    if msg_type not in ("text", "interactive", "button", "order", "image", "document", "audio", "video", "sticker"):
        logger.info(f"[WA-HOOK] Ignoring non-actionable message type: {msg_type}")
        return

    phone = normalise_phone(from_number)

    # Deduplicate by wamid
    if await session_manager.is_wamid_seen(db, wamid):
        logger.debug(f"[WA-HOOK] Duplicate wamid: {wamid}")
        return

    # Extract message content
    message_text = ""
    button_id = None
    list_id = None

    if msg_type == "text":
        message_text = message.get("text", {}).get("body", "")
    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        interactive_type = interactive.get("type", "")
        if interactive_type == "button_reply":
            button_id = interactive.get("button_reply", {}).get("id", "")
            message_text = interactive.get("button_reply", {}).get("title", "")
        elif interactive_type == "list_reply":
            list_id = interactive.get("list_reply", {}).get("id", "")
            message_text = interactive.get("list_reply", {}).get("title", "")
        elif interactive_type == "nfm_reply":
            # Native Flow Message Reply
            nfm_reply = interactive.get("nfm_reply", {})
            import json
            response_json_str = nfm_reply.get("response_json", "{}")
            try:
                flow_data = json.loads(response_json_str)
                # Store it as text but stringified so the state machine can parse it
                message_text = f"FLOW_SUBMIT:{response_json_str}"
            except json.JSONDecodeError:
                message_text = ""
    elif msg_type == "button":
        # Template button callbacks
        button_id = message.get("button", {}).get("payload", "")
        message_text = message.get("button", {}).get("text", "")
    elif msg_type == "order":
        from decimal import Decimal
        order_payload = message.get("order", {})
        product_items = order_payload.get("product_items", [])
        
        cart = []
        subtotal = Decimal("0")
        woo_line_items = []
        for item in product_items:
            retailer_id = item.get("product_retailer_id", "")
            qty = item.get("quantity", 1)
            price_str = item.get("item_price", "0")
            unit_price = Decimal(price_str)

            # Clean retailer_id: "woo_123" -> 123 (int)
            raw_id = retailer_id.replace("woo_", "")
            woo_id = int(raw_id) if raw_id.isdigit() else raw_id

            cart.append({
                "woo_product_id": woo_id,
                "name": f"Product ({retailer_id})",
                "qty": qty,
                "unit_price": float(unit_price),
            })
            subtotal += unit_price * qty

            # Build WooCommerce line item
            line_item = {"quantity": qty}
            if isinstance(woo_id, int):
                line_item["product_id"] = woo_id
            else:
                line_item["sku"] = str(woo_id)
            woo_line_items.append(line_item)

        # Create a "pending" order on WooCommerce immediately
        woo_pending_order_id = None
        try:
            pending_order_data = {
                "status": "pending",
                "billing": {
                    "phone": f"+{phone}" if not phone.startswith("+") else phone,
                },
                "line_items": woo_line_items,
                "meta_data": [
                    {"key": "_whatsapp_order", "value": "true"},
                    {"key": "_whatsapp_checkout_status", "value": "in_progress"},
                ],
            }
            woo_order = await woo_client.create_order(pending_order_data)
            woo_pending_order_id = woo_order.get("id")
            
            # Update local cart with actual product names from WooCommerce
            if woo_order and "line_items" in woo_order:
                woo_names = {}
                for item in woo_order.get("line_items", []):
                    if item.get("product_id"):
                        woo_names[item["product_id"]] = item.get("name")
                    if item.get("sku"):
                        woo_names[str(item["sku"])] = item.get("name")
                        
                for cart_item in cart:
                    woo_id = cart_item["woo_product_id"]
                    if woo_id in woo_names:
                        cart_item["name"] = woo_names[woo_id]
                    elif str(woo_id) in woo_names:
                        cart_item["name"] = woo_names[str(woo_id)]
                        
            logger.info(f"[WA-HOOK] Created pending WooCommerce order #{woo_pending_order_id} for {phone}")
        except Exception as e:
            logger.error(f"[WA-HOOK] Failed to create pending Woo order for {phone}: {e}")

        # Create session
        session = await session_manager.create_session(
            db=db,
            phone=phone,
            cart=cart,
            woo_pending_order_id=woo_pending_order_id,
            subtotal=subtotal,
        )
        
        await session_manager.log_message(db, phone, "inbound", "placed an order from catalog", wamid, msg_type, session.id)
        
        # Advance to pincode check first (which then goes to address)
        from bot.states.pincode import enter_pincode_check
        await enter_pincode_check(db, session)
        return

    # ── Greeting keyword (works with or without an active session) ────
    _GREETINGS = {"hi", "hello", "hey", "hii", "hiii", "heya", "hola", "namaste", "start"}
    if message_text and message_text.strip().lower() in _GREETINGS:
        # Only send intro if no active session
        active_session = await session_manager.get_active_session(db, phone)
        if not active_session:
            logger.info(f"[WA-HOOK] Greeting received from {phone}")
            await session_manager.log_message(
                db, phone, "inbound", message_text, wamid, msg_type
            )

            # Check if user already has a validated pincode
            from utils.pincode_service import (
                get_validated_pincode, is_pincode_serviceable,
                mark_awaiting_pincode,
            )

            # Check in-memory cache first
            cached_pincode = get_validated_pincode(phone)
            if cached_pincode and is_pincode_serviceable(cached_pincode):
                # Returning user with valid pincode — straight to catalog
                await wa_client.send_text(
                    phone,
                    "👋 *Welcome back to Sigmaflux!*\n\n"
                    "We're glad you're here! Browse our catalog and place an order directly on WhatsApp.\n\n"
                    "🚚 *Delivery Info:*\n"
                    "  • Orders above ₹500 → *FREE delivery* ✨\n"
                    "  • Orders below ₹500 → ₹30 delivery charge\n\n"
                    "Tap below to explore our products 👇"
                )
                await _send_catalog(phone)
                return

            # Check saved addresses from DB
            addresses = await session_manager.get_addresses_for_phone(db, phone)
            if addresses:
                last_used = next((a for a in addresses if a.is_last_used), addresses[0])
                if is_pincode_serviceable(last_used.pincode):
                    from utils.pincode_service import set_validated_pincode
                    set_validated_pincode(phone, last_used.pincode)
                    await wa_client.send_text(
                        phone,
                        "👋 *Welcome back to Sigmaflux!*\n\n"
                        "We're glad you're here! Browse our catalog and place an order directly on WhatsApp.\n\n"
                        "🚚 *Delivery Info:*\n"
                        "  • Orders above ₹500 → *FREE delivery* ✨\n"
                        "  • Orders below ₹500 → ₹30 delivery charge\n\n"
                        "Tap below to explore our products 👇"
                    )
                    await _send_catalog(phone)
                    return

            # No validated pincode — ask for it
            mark_awaiting_pincode(phone)
            await wa_client.send_text(
                phone,
                "👋 *Welcome to Sigmaflux!*\n\n"
                "Before we get started, please share your *delivery pincode* "
                "so we can check if we deliver to your area. 📍\n\n"
                "Just type your 6-digit pincode below 👇"
            )
            return

    # ── Catalog keyword (works with or without an active session) ────
    if message_text and message_text.strip().lower() in ("catalogue", "catalog"):
        logger.info(f"[WA-HOOK] Catalog requested by {phone}")
        await session_manager.log_message(
            db, phone, "inbound", message_text, wamid, msg_type
        )
        await _send_catalog(phone)
        return

    # ── Pincode response (user replied with pincode, no active session) ────
    from utils.pincode_service import is_awaiting_pincode
    if is_awaiting_pincode(phone):
        await session_manager.log_message(
            db, phone, "inbound", message_text, wamid, msg_type
        )
        pincode = message_text.strip() if message_text else ""

        if not pincode or not pincode.isdigit() or len(pincode) != 6:
            await wa_client.send_text(
                phone,
                "⚠️ Please enter a valid 6-digit pincode.\n\n"
                "Example: *600001*"
            )
            return

        from utils.pincode_service import (
            is_pincode_serviceable, set_validated_pincode, clear_awaiting_pincode,
        )

        if not is_pincode_serviceable(pincode):
            clear_awaiting_pincode(phone)
            logger.info(f"[WA-HOOK] Pincode {pincode} NOT serviceable for {phone}")
            await wa_client.send_text(
                phone,
                f"😔 *Sorry, delivery is not available at pincode {pincode}.*\n\n"
                "We currently deliver only to select areas. "
                "We're expanding soon — stay tuned! 🚀\n\n"
                "Say *hi* anytime to check again with a different pincode."
            )
            return

        # Serviceable! Save and show catalog
        set_validated_pincode(phone, pincode)
        logger.info(f"[WA-HOOK] Pincode {pincode} validated for {phone} ✅")
        await wa_client.send_text(
            phone,
            f"✅ Great news! We deliver to *{pincode}*!\n\n"
            "🚚 *Delivery Info:*\n"
            "  • Orders above ₹500 → *FREE delivery* ✨\n"
            "  • Orders below ₹500 → ₹30 delivery charge\n\n"
            "Browse our catalog and place your order! 🛍️"
        )
        await _send_catalog(phone)
        return

    # Find active session
    session = await session_manager.get_active_session(db, phone)

    if not session:
        # No active session — send intro and ask for pincode
        from utils.pincode_service import get_validated_pincode, mark_awaiting_pincode

        cached_pincode = get_validated_pincode(phone)
        if cached_pincode:
            # They have a pincode but no session — just show catalog
            await wa_client.send_text(
                phone,
                "👋 Hi there! You don't have an active checkout in progress.\n\n"
                "🚚 *Delivery Info:*\n"
                "  • Orders above ₹500 → *FREE delivery* ✨\n"
                "  • Orders below ₹500 → ₹30 delivery charge\n\n"
                "Browse our catalog and place an order to get started! 🛍️"
            )
        else:
            # No pincode on file — ask for it
            mark_awaiting_pincode(phone)
            await wa_client.send_text(
                phone,
                "👋 *Welcome to Sigmaflux!*\n\n"
                "Before we get started, please share your *delivery pincode* "
                "so we can check if we deliver to your area. 📍\n\n"
                "Just type your 6-digit pincode below 👇"
            )
        # Log the message anyway
        await session_manager.log_message(
            db, phone, "inbound", message_text, wamid, msg_type
        )
        return

    # Log inbound message
    await session_manager.log_message(
        db, phone, "inbound", message_text, wamid, msg_type, session.id
    )

    # If the session is in WELCOME_SENT and user hasn't received the welcome
    # message yet (session was created silently by WooCommerce webhook),
    # send the welcome message now — it will auto-advance to pincode check.
    if session.state == "WELCOME_SENT" and session.unexpected_input_count == 0:
        from bot.states.welcome import send_welcome_message
        logger.info(f"[WA-HOOK] First message from {phone} with pending session — sending welcome")
        await send_welcome_message(db, session)
        return

    # Route to state machine
    await route_message(db, session, message_text, button_id, list_id)


async def _send_catalog(phone: str):
    """Fetch a product retailer_id for the thumbnail and send the catalog."""
    from clients import woocommerce as woo_client

    thumbnail_id = None
    try:
        # Grab the first published product for the catalog thumbnail
        products = await woo_client.get_all_products(per_page=1, page=1)
        if products:
            product = products[0]
            thumbnail_id = product.get("sku", "") or f"woo_{product['id']}"
            logger.info(f"[WA-HOOK] Using thumbnail retailer_id: {thumbnail_id}")
    except Exception as e:
        logger.warning(f"[WA-HOOK] Could not fetch thumbnail product: {e}")

    await wa_client.send_catalog(
        to=phone,
        body="🛍️ Here's our full catalog! Tap below to browse and shop.",
        thumbnail_product_retailer_id=thumbnail_id,
        footer="Powered by Sigmaflux",
    )
