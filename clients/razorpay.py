"""
Razorpay payment links client.
Handles creating, fetching, and cancelling payment links.
"""

import logging
import time
from typing import Optional

import httpx

from config import settings
from utils.retry import async_retry

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.razorpay.com/v1"
_AUTH = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(auth=_AUTH, timeout=30.0)


@async_retry()
async def create_payment_link(
    amount_paise: int,
    customer_name: str,
    customer_phone: str,
    description: str = "WhatsApp Order",
    expire_minutes: int = None,
) -> dict:
    """
    Create a Razorpay payment link.
    
    Args:
        amount_paise: Amount in paise (₹100 = 10000)
        customer_name: Customer name for the link
        customer_phone: E.164 phone with + prefix
        description: Payment description
        expire_minutes: Link expiry in minutes (default from settings)
    
    Returns:
        Razorpay payment link response including `id`, `short_url`
    """
    if expire_minutes is None:
        expire_minutes = settings.PAYMENT_LINK_EXPIRY_MINUTES

    expire_by = int(time.time()) + (expire_minutes * 60)

    payload = {
        "amount": amount_paise,
        "currency": settings.CURRENCY,
        "description": description,
        "customer": {
            "name": customer_name,
            "contact": f"+{customer_phone}" if not customer_phone.startswith("+") else customer_phone,
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "expire_by": expire_by,
    }

    async with _client() as client:
        r = await client.post(f"{_BASE_URL}/payment_links", json=payload)
        r.raise_for_status()
        data = r.json()
        logger.info(f"[RAZORPAY] Payment link created: {data.get('id')} | {data.get('short_url')}")
        return data


@async_retry()
async def get_payment_link(link_id: str) -> dict:
    """Fetch a payment link status."""
    async with _client() as client:
        r = await client.get(f"{_BASE_URL}/payment_links/{link_id}")
        r.raise_for_status()
        return r.json()


@async_retry()
async def cancel_payment_link(link_id: str) -> dict:
    """Cancel a payment link."""
    async with _client() as client:
        r = await client.post(f"{_BASE_URL}/payment_links/{link_id}/cancel")
        r.raise_for_status()
        logger.info(f"[RAZORPAY] Payment link cancelled: {link_id}")
        return r.json()


async def is_payment_captured(link_id: str) -> tuple[bool, Optional[str]]:
    """
    Check if payment has been captured.
    Returns (is_captured, payment_id).
    """
    try:
        data = await get_payment_link(link_id)
        status = data.get("status", "")
        payments = data.get("payments", {})

        if status == "paid":
            # Try to extract the payment_id
            payment_items = payments.get("items", []) if isinstance(payments, dict) else []
            payment_id = payment_items[0].get("id") if payment_items else None
            return True, payment_id

        return False, None
    except Exception as e:
        logger.error(f"[RAZORPAY] Error checking payment: {e}")
        return False, None


@async_retry()
async def refund_payment(payment_id: str, amount_paise: int = None) -> dict:
    """
    Issue a refund for a captured payment.
    If amount_paise is None, full refund.
    """
    payload = {}
    if amount_paise:
        payload["amount"] = amount_paise

    async with _client() as client:
        r = await client.post(
            f"{_BASE_URL}/payments/{payment_id}/refund",
            json=payload,
        )
        r.raise_for_status()
        logger.info(f"[RAZORPAY] Refund issued for payment: {payment_id}")
        return r.json()
