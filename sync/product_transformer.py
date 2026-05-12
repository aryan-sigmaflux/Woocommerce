"""
Product field transformer: WooCommerce → Meta Commerce API format.
"""

import json
import logging
from pathlib import Path

from utils.html_strip import strip_html

logger = logging.getLogger(__name__)

# Load category mapping
_CATEGORY_MAP_PATH = Path(__file__).parent / "category_map.json"
try:
    _CATEGORY_MAP = json.loads(_CATEGORY_MAP_PATH.read_text())
except Exception:
    logger.warning("[SYNC] category_map.json not found, using defaults")
    _CATEGORY_MAP = {"default": "Generic"}


def _get_meta_category(woo_categories: list[dict]) -> str:
    """Map the first WooCommerce category name to a Meta taxonomy category."""
    if not woo_categories:
        return _CATEGORY_MAP.get("default", "Generic")

    woo_cat_name = woo_categories[0].get("name", "")
    return _CATEGORY_MAP.get(woo_cat_name, _CATEGORY_MAP.get("default", "Generic"))


def transform_product(woo_product: dict) -> dict:
    """
    Transform a WooCommerce product dict into a Meta Commerce catalog item dict.
    
    Field mapping per spec:
        sku             → retailer_id / id (fallback: woo_{id})
        name            → name
        short_description / description → description (HTML stripped)
        sale_price / price → price (in paise × 100)
        "INR"           → currency
        stock_status    → availability
        images[0].src   → image_url
        permalink       → url
        categories[0]   → category (via category_map)
        "new"           → condition
    """
    woo_id = woo_product.get("id")
    sku = woo_product.get("sku", "") or f"woo_{woo_id}"
    name = woo_product.get("name", "Untitled Product")

    # Description: prefer short_description, fallback to description
    description = woo_product.get("short_description") or woo_product.get("description", "")
    description = strip_html(description) or name

    # Price in paise (× 100)
    raw_price = woo_product.get("sale_price") or woo_product.get("price") or "0"
    try:
        price_paise = int(float(raw_price) * 100)
    except (ValueError, TypeError):
        price_paise = 0

    # Stock / availability
    stock_status = woo_product.get("stock_status", "instock")
    availability = "in stock" if stock_status == "instock" else "out of stock"

    # Image
    images = woo_product.get("images", [])
    image_url = images[0].get("src", "") if images else ""

    # Category
    categories = woo_product.get("categories", [])
    category = _get_meta_category(categories)

    # Brand: use store name or fallback
    brand = "Sigmaflux"

    raw_category = categories[0].get("name", "Uncategorized") if categories else "Uncategorized"

    return {
        "retailer_id": sku,
        "id": sku,
        "title": name[:150],
        "description": description[:5000],
        "price": str(price_paise),
        "currency": "INR",
        "availability": availability,
        "image_link": image_url,
        "link": woo_product.get("permalink", ""),
        "brand": brand,
        "category": category,
        "condition": "new",
        # Use custom_label_0 for dynamic Product Set categorization
        "custom_label_0": raw_category,
        "woo_categories": categories,
    }
