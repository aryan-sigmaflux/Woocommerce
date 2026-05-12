"""
Phone number normalisation to E.164 format (Indian numbers).
"""

import re


def normalise_phone(raw: str) -> str:
    """
    Normalise an Indian phone number to E.164 without the '+' prefix.
    
    Examples:
        '09820123456'   → '919820123456'
        '9820123456'    → '919820123456'
        '+919820123456' → '919820123456'
        '919820123456'  → '919820123456'
        '0091982012345' → '919820123456'  (strip country prefix with leading zeros)
    """
    # Strip all non-digit characters
    digits = re.sub(r'\D', '', raw)

    # Remove leading zeros
    digits = digits.lstrip('0')

    # If starts with 91 and remaining is 10 digits → already E.164
    if digits.startswith('91') and len(digits) == 12:
        return digits

    # If exactly 10 digits, prepend 91
    if len(digits) == 10:
        return f'91{digits}'

    # Fallback — return cleaned digits
    return digits
