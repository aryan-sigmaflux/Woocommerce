"""
HMAC signature verification for WooCommerce, Meta, and Razorpay webhooks.
"""

import hashlib
import hmac


def verify_woo_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify WooCommerce webhook signature.
    WooCommerce sends X-WC-Webhook-Signature as base64-encoded HMAC-SHA256.
    """
    import base64
    expected = base64.b64encode(
        hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)


def verify_meta_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    """
    Verify Meta X-Hub-Signature-256 header.
    Format: sha256=<hex_digest>
    """
    expected = "sha256=" + hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_razorpay_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify Razorpay webhook X-Razorpay-Signature header.
    HMAC-SHA256 hex digest.
    """
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
