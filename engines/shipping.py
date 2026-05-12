"""
Delivery engine — free delivery above ₹500, else flat ₹30.
Every part of the bot calls calculate_shipping() as a single function.
To switch to zone/weight-based logic, only this file changes.
"""

from decimal import Decimal
from config import settings

# Free delivery threshold (in INR)
FREE_DELIVERY_THRESHOLD = Decimal("500")


def calculate_shipping(cart: list[dict] = None, address: dict = None, subtotal: Decimal = None) -> float:
    """
    Calculate delivery cost.
    
    Rules:
        - Cart subtotal >= ₹500 → Free delivery
        - Cart subtotal <  ₹500 → Flat ₹30 delivery charge
    
    Args:
        cart: list of cart items (for weight-based logic / subtotal calc)
        address: delivery address dict (for zone-based logic)
        subtotal: pre-calculated subtotal (if available)
    
    Returns:
        Delivery cost as float.
    """
    if subtotal is None and cart:
        subtotal = Decimal("0")
        for item in cart:
            price = Decimal(str(item.get("unit_price", 0)))
            qty = item.get("qty", 1)
            subtotal += price * qty

    if subtotal is not None and subtotal >= FREE_DELIVERY_THRESHOLD:
        return 0.0

    return float(settings.SHIPPING_FLAT_AMOUNT)


def is_pincode_serviceable(pincode: str) -> bool:
    """
    Check if delivery is available for the given pincode.
    
    Implement your logic here. You can:
    1. Check against a hardcoded list: `return pincode in ['400001', '400002']`
    2. Check against a database or external API (make function async if needed).
    
    For now, this returns True for any valid 6-digit number.
    """
    if not pincode or not pincode.isdigit() or len(pincode) != 6:
        return False
        
    # TODO: Add your serviceable pincodes logic here:
    # blocked_pincodes = ["123456", "654321"]
    # if pincode in blocked_pincodes:
    #     return False
    
    return True
