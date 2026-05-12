"""
WooCommerce Webhook Router.
POST /webhooks/woo/order   — New order → create session (silent, no WhatsApp msg)
POST /webhooks/woo/product — Product CRUD → catalog sync
"""

import logging
from decimal import Decimal

from fastapi import APIRouter, Request, Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.database import get_db
from bot import session_manager
# NOTE: We do NOT send proactive WhatsApp messages from WooCommerce webhooks.
# The bot only responds when the user messages first on WhatsApp.
from clients import woocommerce as woo_client
from sync import catalog_sync
from utils.phone import normalise_phone
from utils.signature import verify_woo_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/woo", tags=["WooCommerce Webhooks"])


# ── Order Webhook ───────────────────────────────────────────────────


@router.post("/order")
async def handle_order_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_wc_webhook_signature: str = Header(None, alias="X-WC-Webhook-Signature"),
    x_wc_webhook_topic: str = Header(None, alias="X-WC-Webhook-Topic"),
):
    """
    WooCommerce order.created webhook.
    Creates a checkout session and sends the welcome message.
    """
    raw_body = await request.body()

    # Verify signature
    if x_wc_webhook_signature:
        if not verify_woo_signature(raw_body, x_wc_webhook_signature, settings.WOO_WEBHOOK_SECRET):
            logger.warning("[WOO-HOOK] Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Get body JSON safely
    try:
        body = await request.json()
    except Exception:
        # This happens during the initial WooCommerce "ping" test
        logger.info("[WOO-HOOK] Received non-JSON or empty payload (likely a ping)")
        return {"status": "ok", "message": "Ping received"}

    # Skip if no order ID
    order_id = body.get("id")
    if not order_id:
        return {"status": "skipped", "reason": "no order id"}

    # Skip orders created by our own bot (pending orders from WhatsApp catalog flow)
    meta_data = body.get("meta_data", [])
    for meta in meta_data:
        if meta.get("key") == "_whatsapp_order" and meta.get("value") == "true":
            logger.info(f"[WOO-HOOK] Order #{order_id} is a WhatsApp bot order, skipping")
            return {"status": "skipped", "reason": "whatsapp bot order"}

    # Extract phone from billing
    billing = body.get("billing", {})
    raw_phone = billing.get("phone", "")
    if not raw_phone:
        logger.warning(f"[WOO-HOOK] Order #{order_id} has no billing phone")
        return {"status": "skipped", "reason": "no phone"}

    phone = normalise_phone(raw_phone)
    logger.info(f"[WOO-HOOK] Order #{order_id} received for {phone}")

    # Check for existing active session
    existing_session = await session_manager.get_active_session(db, phone)
    if existing_session:
        # Duplicate webhook or user has checkout in progress
        logger.info(f"[WOO-HOOK] Active session exists for {phone}, skipping")
        return {"status": "skipped", "reason": "active session exists"}

    # Extract cart items from line_items
    line_items = body.get("line_items", [])
    cart = []
    subtotal = Decimal("0")

    for item in line_items:
        product_id = item.get("product_id")
        name = item.get("name", "Item")
        qty = item.get("quantity", 1)
        unit_price = Decimal(str(item.get("price", "0")))

        # Validate stock
        try:
            product = await woo_client.get_product(product_id)
            stock_status = product.get("stock_status", "instock")
            if stock_status != "instock":
                logger.warning(f"[WOO-HOOK] Product {product_id} out of stock, skipping from cart")
                continue
        except Exception as e:
            logger.error(f"[WOO-HOOK] Failed to validate product {product_id}: {e}")
            # Include it anyway — WooCommerce already accepted the order

        cart.append({
            "woo_product_id": product_id,
            "name": name,
            "qty": qty,
            "unit_price": float(unit_price),
        })
        subtotal += unit_price * qty

    if not cart:
        logger.warning(f"[WOO-HOOK] Order #{order_id} has no valid items after stock check")
        return {"status": "skipped", "reason": "all items out of stock"}

    # Create checkout session (silent — NO WhatsApp message sent)
    # The bot will respond when the user messages on WhatsApp
    session = await session_manager.create_session(
        db=db,
        phone=phone,
        cart=cart,
        woo_pending_order_id=order_id,
        subtotal=subtotal,
    )

    logger.info(f"[WOO-HOOK] Session created silently for {phone} | order #{order_id} — waiting for user to message")

    return {"status": "ok", "session_id": str(session.id)}


# ── Product Webhook ─────────────────────────────────────────────────


@router.post("/product")
async def handle_product_webhook(
    request: Request,
    x_wc_webhook_signature: str = Header(None, alias="X-WC-Webhook-Signature"),
    x_wc_webhook_topic: str = Header(None, alias="X-WC-Webhook-Topic"),
):
    """
    WooCommerce product.created / product.updated / product.deleted webhook.
    Triggers real-time catalog sync to Meta Commerce API.
    """
    raw_body = await request.body()

    # Verify signature
    if x_wc_webhook_signature:
        if not verify_woo_signature(raw_body, x_wc_webhook_signature, settings.WOO_WEBHOOK_SECRET):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Get body JSON safely
    try:
        body = await request.json()
    except Exception:
        logger.info("[WOO-HOOK] Received non-JSON or empty product payload (likely a ping)")
        return {"status": "ok", "message": "Ping received"}

    product_id = body.get("id")
    product_name = body.get("name", "Unknown")
    product_sku = body.get("sku", "N/A")
    product_price = body.get("price", "N/A")
    product_status = body.get("status", "N/A")

    if not product_id:
        return {"status": "skipped", "reason": "no product id"}

    topic = x_wc_webhook_topic or ""

    try:
        if "deleted" in topic:
            logger.info("")
            logger.info("🗑️" + "=" * 58)
            logger.info(f"🗑️  PRODUCT DELETED")
            logger.info(f"🗑️  ID: {product_id} | Name: {product_name}")
            logger.info(f"🗑️  SKU: {product_sku}")
            logger.info("🗑️" + "=" * 58)
            logger.info("")
            await catalog_sync.sync_product_deleted(product_id, product_sku)

        elif "created" in topic:
            logger.info("")
            logger.info("✅" + "=" * 58)
            logger.info(f"✅  PRODUCT ADDED")
            logger.info(f"✅  ID: {product_id} | Name: {product_name}")
            logger.info(f"✅  SKU: {product_sku} | Price: ₹{product_price}")
            logger.info(f"✅  Status: {product_status}")
            logger.info("✅" + "=" * 58)
            logger.info("")
            await catalog_sync.sync_product_created(product_id)

        elif "updated" in topic:
            logger.info("")
            logger.info("✏️" + "=" * 58)
            logger.info(f"✏️  PRODUCT EDITED")
            logger.info(f"✏️  ID: {product_id} | Name: {product_name}")
            logger.info(f"✏️  SKU: {product_sku} | Price: ₹{product_price}")
            logger.info(f"✏️  Status: {product_status}")
            logger.info("✏️" + "=" * 58)
            logger.info("")
            await catalog_sync.sync_product_updated(product_id)

        else:
            logger.info("")
            logger.info("🔄" + "=" * 58)
            logger.info(f"🔄  PRODUCT UPDATED (unknown topic)")
            logger.info(f"🔄  ID: {product_id} | Name: {product_name}")
            logger.info(f"🔄  SKU: {product_sku} | Price: ₹{product_price}")
            logger.info("🔄" + "=" * 58)
            logger.info("")
            await catalog_sync.sync_product_updated(product_id)

        return {"status": "ok", "product_id": product_id}

    except Exception as e:
        logger.error(f"[WOO-HOOK] ❌ Product sync FAILED for #{product_id} ({product_name}): {e}")
        return {"status": "error", "detail": str(e)}
