"""
Razorpay Payment Webhook + Callback Router.
POST /api/payments/razorpay/webhook  — payment.captured event
GET  /api/payments/razorpay/callback — post-payment redirect (UX fallback)
"""

import logging

from fastapi import APIRouter, Request, Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.database import get_db
from bot import session_manager
from bot.states.payment import handle_payment_captured
from clients import razorpay as rzp_client
from utils.signature import verify_razorpay_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments/razorpay", tags=["Razorpay"])


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_razorpay_signature: str = Header(None, alias="X-Razorpay-Signature"),
):
    """
    Razorpay webhook — handles payment.captured events.
    Looks up session by payment_link_id and auto-confirms the order.
    """
    raw_body = await request.body()

    # Verify signature
    if x_razorpay_signature:
        if not verify_razorpay_signature(
            raw_body, x_razorpay_signature, settings.RAZORPAY_WEBHOOK_SECRET
        ):
            logger.warning("[RZP-HOOK] Invalid Razorpay webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    body = await request.json()
    event = body.get("event", "")

    if event != "payment_link.paid":
        # We only care about successful payments
        logger.debug(f"[RZP-HOOK] Ignoring event: {event}")
        return {"status": "ignored"}

    payload = body.get("payload", {})
    payment_link = payload.get("payment_link", {}).get("entity", {})
    payment = payload.get("payment", {}).get("entity", {})

    link_id = payment_link.get("id", "")
    payment_id = payment.get("id", "")

    if not link_id:
        logger.warning("[RZP-HOOK] No payment_link_id in webhook")
        return {"status": "skipped"}

    # Find session by Razorpay link ID
    session = await session_manager.get_session_by_razorpay_link(db, link_id)

    if not session:
        logger.warning(f"[RZP-HOOK] No session found for link {link_id}")

        # Check if session expired — if so, issue refund
        # This handles edge case 18.8 from the spec
        try:
            if payment_id:
                await rzp_client.refund_payment(payment_id)
                logger.info(f"[RZP-HOOK] Refunded orphan payment {payment_id}")
        except Exception as e:
            logger.error(f"[RZP-HOOK] Failed to refund orphan payment: {e}")

        return {"status": "refunded_orphan"}

    if session.state in session_manager.TERMINAL_STATES:
        logger.info(f"[RZP-HOOK] Session already terminal for link {link_id}")
        return {"status": "already_completed"}

    # Process the payment
    logger.info(f"[RZP-HOOK] Payment captured: link={link_id} payment={payment_id}")
    await handle_payment_captured(db, session, payment_id)

    return {"status": "ok"}


@router.get("/callback")
async def razorpay_callback(
    request: Request,
    razorpay_payment_id: str = None,
    razorpay_payment_link_id: str = None,
    razorpay_payment_link_reference_id: str = None,
    razorpay_payment_link_status: str = None,
    razorpay_signature: str = None,
):
    """
    Post-payment redirect callback (UX fallback).
    The actual processing happens via the webhook — this is just for user experience.
    """
    return {
        "message": "Payment received! Check WhatsApp for your order confirmation. ✅",
        "status": razorpay_payment_link_status,
    }
