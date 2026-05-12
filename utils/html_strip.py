"""
Strip HTML tags from WooCommerce product descriptions.
"""

import re
from html import unescape


def strip_html(html_string: str) -> str:
    """Remove HTML tags and decode entities, returning plain text."""
    if not html_string:
        return ""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', html_string)
    # Decode HTML entities
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text
