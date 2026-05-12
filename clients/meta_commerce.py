"""
Meta Commerce API client for WhatsApp Business Catalog sync.
Handles batch upsert, single item operations, deletion, and product sets (categories).
"""

import logging

import httpx

from config import settings
from utils.retry import async_retry

logger = logging.getLogger(__name__)

_GRAPH_BASE = f"https://graph.facebook.com/{settings.META_API_VERSION}"
_CATALOG_ID = settings.META_CATALOG_ID
_HEADERS = {
    "Authorization": f"Bearer {settings.META_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60.0)


# ── Batch Upsert (up to 1000 items per call) ────────────────────────


@async_retry(max_retries=3, base_delay=60.0, multiplier=2.0)
async def batch_upsert(items: list[dict]) -> dict:
    """
    Batch upsert items to the Meta Commerce catalog.
    `allow_upsert: true` creates new items and updates existing ones.
    
    items: list of item dicts with at least `retailer_id`, `name`, etc.
    Max 1000 per call.
    """
    requests = []
    for item in items:
        # Meta prefers '100.00 INR' format for price in Batch API
        try:
            price_paise = int(item.get("price", "0"))
            price_decimal = price_paise / 100
            price_string = f"{price_decimal:.2f} {item.get('currency', 'INR')}"
        except Exception:
            price_string = "0.00 INR"

        data = item.copy()
        data["price"] = price_string

        # Remove fields that belong at request level, not inside data
        data.pop("retailer_id", None)
        data.pop("currency", None)
        # Remove woo_categories — used internally for product set mapping
        data.pop("woo_categories", None)

        requests.append({
            "method": "CREATE",
            "retailer_id": item["retailer_id"],
            "data": data,
        })

    payload = {
        "item_type": "PRODUCT_ITEM",
        "allow_upsert": True,
        "requests": requests,
    }

    async with _client() as client:
        r = await client.post(
            f"{_GRAPH_BASE}/{_CATALOG_ID}/items_batch",
            json=payload,
            headers=_HEADERS,
        )
        
        if r.status_code != 200:
            logger.error(f"[CATALOG] Batch API failed with {r.status_code}: {r.text}")
            r.raise_for_status()

        result = r.json()
        logger.info(f"[CATALOG] Batch upsert: {len(items)} items submitted")
        
        # Log validation handles/errors so we can detect silent rejections
        validation_status = result.get("validation_status", [])
        if validation_status:
            for vs in validation_status:
                if vs.get("errors"):
                    logger.warning(f"[CATALOG] Validation error for {vs.get('retailer_id', '?')}: {vs['errors']}")
        
        # Log full response at debug level for troubleshooting
        logger.debug(f"[CATALOG] Batch response: {result}")
        return result


# ── Single Item Upsert ──────────────────────────────────────────────


@async_retry()
async def upsert_single_item(item: dict) -> dict:
    """Upsert a single product to the catalog."""
    return await batch_upsert([item])


# ── Delete Item ─────────────────────────────────────────────────────


@async_retry()
async def delete_item(retailer_id: str) -> dict:
    """Delete a single item from the catalog by retailer_id."""
    payload = {
        "item_type": "PRODUCT_ITEM",
        "requests": [
            {
                "method": "DELETE",
                "data": {
                    "id": retailer_id
                }
            }
        ]
    }

    async with _client() as client:
        r = await client.post(
            f"{_GRAPH_BASE}/{_CATALOG_ID}/items_batch",
            json=payload,
            headers=_HEADERS,
        )
        if r.status_code != 200:
            logger.error(f"[CATALOG] Delete failed for {retailer_id} — {r.status_code}: {r.text}")
            r.raise_for_status()
        logger.info(f"[CATALOG] Deleted item: {retailer_id}")
        return r.json()


# ── Get All Catalog Items (for orphan detection during full sync) ───


@async_retry()
async def get_all_catalog_items() -> list[str]:
    """Fetch all retailer_ids currently in the Meta catalog."""
    retailer_ids = []
    url = f"{_GRAPH_BASE}/{_CATALOG_ID}/products"
    params = {"fields": "retailer_id", "limit": 500}

    async with _client() as client:
        while url:
            r = await client.get(url, params=params, headers=_HEADERS)
            r.raise_for_status()
            data = r.json()

            for item in data.get("data", []):
                if "retailer_id" in item:
                    retailer_ids.append(item["retailer_id"])

            # Pagination
            paging = data.get("paging", {})
            url = paging.get("next")
            params = {}  # next URL already has params

    return retailer_ids


# ── Product Sets (Categories) ──────────────────────────────────────


# In-memory cache: { "category_name": "product_set_id" }
_product_set_cache: dict[str, str] = {}


@async_retry()
async def get_all_product_sets() -> dict[str, str]:
    """
    Fetch all product sets from the catalog.
    Returns: { "set_name": "set_id" }
    """
    product_sets = {}
    url = f"{_GRAPH_BASE}/{_CATALOG_ID}/product_sets"
    params = {"fields": "id,name", "limit": 500}

    async with _client() as client:
        while url:
            r = await client.get(url, params=params, headers=_HEADERS)
            r.raise_for_status()
            data = r.json()

            for ps in data.get("data", []):
                product_sets[ps["name"]] = ps["id"]

            paging = data.get("paging", {})
            url = paging.get("next")
            params = {}

    return product_sets


@async_retry()
async def ensure_product_set_exists(name: str) -> str:
    """
    Create a product set (category) in the Meta catalog if it doesn't exist.
    Uses a dynamic filter on custom_label_0 matching the category name,
    which automatically pulls in all products configured with this category.
    """
    import json
    filter_obj = {"custom_label_0": {"eq": name}}

    payload = {
        "name": name,
        "filter": json.dumps(filter_obj),
    }

    async with _client() as client:
        r = await client.post(
            f"{_GRAPH_BASE}/{_CATALOG_ID}/product_sets",
            data=payload,
            headers={"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"},
        )
        if r.status_code != 200:
            logger.error(f"[CATALOG] Create product set failed: {r.status_code}: {r.text}")
            r.raise_for_status()

        result = r.json()
        set_id = result.get("id")
        logger.info(f"[CATALOG] Created product set '{name}' → {set_id}")
        return set_id


async def sync_product_sets(products: list[dict]):
    """
    Sync WooCommerce categories → Meta Product Sets.
    
    Extracts all unique categories from the given products, checks if a Meta 
    Product Set exists for them, and creates them if they don't.
    (Existing sets don't need updates because the filter dynamically pulls products 
    by custom_label_0).
    """
    # Find all unique categories from products
    categories = set()
    for product in products:
        cats = product.get("woo_categories", [])
        for cat in cats:
            cat_name = cat.get("name", "")
            if cat_name:
                categories.add(cat_name)

    if not categories:
        return

    # Fetch existing product sets
    existing_sets = await get_all_product_sets()

    for cat_name in categories:
        if cat_name not in existing_sets:
            try:
                set_id = await ensure_product_set_exists(cat_name)
                existing_sets[cat_name] = set_id
            except Exception as e:
                logger.error(f"[CATALOG] Failed to create product set '{cat_name}': {e}")

    logger.info(f"[CATALOG] Synced {len(categories)} product sets (categories)")
