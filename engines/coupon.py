"""
Coupon engine — live validation against WooCommerce Coupons API.
Supports: percentage, fixed_cart, fixed_product discount types.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from clients import woocommerce as woo_client

logger = logging.getLogger(__name__)


class CouponValidationError(Exception):
    """Raised when a coupon fails validation."""
    pass


async def validate_coupon(
    code: str,
    cart: list[dict],
    subtotal: Decimal,
    phone: str,
) -> dict:
    """
    Validate a coupon code against WooCommerce.
    
    Args:
        code: Coupon code entered by user
        cart: List of cart items [{"woo_product_id": int, "qty": int, ...}]
        subtotal: Cart subtotal before discount
        phone: E.164 phone for per-user usage check
    
    Returns:
        Coupon data dict from WooCommerce if valid.
    
    Raises:
        CouponValidationError with user-friendly reason.
    """
    coupon = await woo_client.get_coupon_by_code(code)
    if not coupon:
        raise CouponValidationError("This coupon code doesn't exist. Please check and try again.")

    # Check expiry
    date_expires = coupon.get("date_expires")
    if date_expires:
        try:
            expires_dt = datetime.fromisoformat(date_expires.replace("Z", "+00:00"))
            if expires_dt < datetime.now(timezone.utc):
                raise CouponValidationError("This coupon has expired.")
        except (ValueError, TypeError):
            pass

    # Check overall usage limit
    usage_limit = coupon.get("usage_limit")
    usage_count = coupon.get("usage_count", 0)
    if usage_limit and usage_count >= usage_limit:
        raise CouponValidationError("This coupon has reached its usage limit.")

    # Check per-user usage limit
    usage_limit_per_user = coupon.get("usage_limit_per_user")
    if usage_limit_per_user:
        user_orders = await woo_client.get_orders_by_phone(phone)
        user_usage = sum(
            1 for order in user_orders
            for cl in order.get("coupon_lines", [])
            if cl.get("code", "").lower() == code.lower()
        )
        if user_usage >= usage_limit_per_user:
            raise CouponValidationError("You've already used this coupon the maximum number of times.")

    # Check minimum amount
    min_amount = coupon.get("minimum_amount")
    if min_amount and Decimal(str(min_amount)) > subtotal:
        raise CouponValidationError(
            f"Minimum order amount for this coupon is ₹{min_amount}. Your cart is ₹{subtotal}."
        )

    # Check maximum amount
    max_amount = coupon.get("maximum_amount")
    if max_amount:
        max_amount_dec = Decimal(str(max_amount))
        if max_amount_dec > 0 and max_amount_dec < subtotal:
            raise CouponValidationError(
                f"Maximum order amount for this coupon is ₹{max_amount_dec:,.2f}."
            )

    # Check product restrictions
    allowed_products = coupon.get("product_ids", [])
    excluded_products = coupon.get("excluded_product_ids", [])
    cart_product_ids = [item.get("woo_product_id") for item in cart]

    if allowed_products:
        if not any(pid in allowed_products for pid in cart_product_ids):
            raise CouponValidationError("This coupon doesn't apply to any items in your cart.")

    if excluded_products:
        if all(pid in excluded_products for pid in cart_product_ids):
            raise CouponValidationError("This coupon is not valid for the items in your cart.")

    return coupon


def calculate_discount(
    coupon: dict,
    cart: list[dict],
    subtotal: Decimal,
) -> tuple[Decimal, str]:
    """
    Calculate discount amount from a validated coupon.
    
    Returns:
        (discount_amount, discount_type)
    """
    discount_type = coupon.get("discount_type", "")
    amount = Decimal(str(coupon.get("amount", "0")))

    if discount_type == "percent":
        discount = subtotal * (amount / Decimal("100"))
        return discount.quantize(Decimal("0.01")), "percentage"

    elif discount_type == "fixed_cart":
        discount = min(amount, subtotal)  # Don't discount more than subtotal
        return discount.quantize(Decimal("0.01")), "fixed_cart"

    elif discount_type == "fixed_product":
        # amount × qty for each matched product
        allowed_products = coupon.get("product_ids", [])
        discount = Decimal("0")
        for item in cart:
            pid = item.get("woo_product_id")
            qty = item.get("qty", 1)
            if not allowed_products or pid in allowed_products:
                discount += amount * qty
        discount = min(discount, subtotal)
        return discount.quantize(Decimal("0.01")), "fixed_product"

    else:
        logger.warning(f"[COUPON] Unknown discount type: {discount_type}")
        return Decimal("0"), discount_type
