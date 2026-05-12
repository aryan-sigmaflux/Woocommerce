"""
WooCommerce REST API v3 client.
Handles products, orders, coupons, order notes, and order meta.
"""

import logging
from typing import Any, Optional
from decimal import Decimal

import httpx

from config import settings
from utils.retry import async_retry

logger = logging.getLogger(__name__)

# Base auth tuple for WooCommerce REST API
_AUTH = (settings.WOO_CONSUMER_KEY, settings.WOO_CONSUMER_SECRET)
_BASE = f"{settings.WOO_BASE_URL}/wp-json/wc/v3"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(auth=_AUTH, timeout=30.0)


# ── Products ────────────────────────────────────────────────────────


@async_retry()
async def get_product(product_id: int) -> dict:
    """Fetch a single product by ID."""
    async with _client() as client:
        r = await client.get(f"{_BASE}/products/{product_id}")
        r.raise_for_status()
        return r.json()


@async_retry()
async def get_all_products(per_page: int = 100, page: int = 1, **params) -> list[dict]:
    """Paginated product listing."""
    async with _client() as client:
        r = await client.get(
            f"{_BASE}/products",
            params={"per_page": per_page, "page": page, "status": "publish", **params}
        )
        r.raise_for_status()
        return r.json()


async def get_all_products_paginated() -> list[dict]:
    """Fetch ALL published products across all pages."""
    all_products = []
    page = 1
    while True:
        batch = await get_all_products(per_page=100, page=page)
        if not batch:
            break
        all_products.extend(batch)
        page += 1
    return all_products


@async_retry()
async def get_product_by_sku(sku: str) -> Optional[dict]:
    """Fetch product data by SKU."""
    async with _client() as client:
        r = await client.get(f"{_BASE}/products", params={"sku": sku})
        r.raise_for_status()
        products = r.json()
        return products[0] if products else None


async def create_order(order_data: dict) -> dict:
    """Create a new WooCommerce order without auto-retry on 400."""
    async with _client() as client:
        try:
            r = await client.post(f"{_BASE}/orders", json=order_data)
            r.raise_for_status()
            logger.info(f"[WOO] Order created: #{r.json().get('id')}")
            return r.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[WOO] Order creation error: {e.response.status_code} - {e.response.text}")
            # If it's a 400, it's a logic error (invalid coupon, etc.), don't retry in decorator
            raise e
        except Exception as e:
            # For other errors (network etc.), we'll let the user decide or use a non-decorated retry
            logger.error(f"[WOO] Unexpected error creating order: {e}")
            raise e


@async_retry()
async def update_order(order_id: int, data: dict) -> dict:
    """Update an existing order (status, meta, etc.)."""
    async with _client() as client:
        r = await client.put(f"{_BASE}/orders/{order_id}", json=data)
        r.raise_for_status()
        return r.json()


@async_retry()
async def get_order(order_id: int) -> dict:
    """Fetch a single order by ID."""
    async with _client() as client:
        r = await client.get(f"{_BASE}/orders/{order_id}")
        r.raise_for_status()
        return r.json()


@async_retry()
async def get_orders(**params) -> list[dict]:
    """Query orders with arbitrary filters."""
    async with _client() as client:
        r = await client.get(f"{_BASE}/orders", params=params)
        r.raise_for_status()
        return r.json()


# ── Order Notes ─────────────────────────────────────────────────────


@async_retry()
async def get_order_notes(order_id: int) -> list[dict]:
    """Fetch all notes for an order."""
    async with _client() as client:
        r = await client.get(f"{_BASE}/orders/{order_id}/notes")
        r.raise_for_status()
        return r.json()


# ── Coupons ─────────────────────────────────────────────────────────


@async_retry()
async def get_coupon_by_code(code: str) -> Optional[dict]:
    """Fetch a coupon by its code. Returns None if not found."""
    async with _client() as client:
        r = await client.get(f"{_BASE}/coupons", params={"code": code})
        r.raise_for_status()
        coupons = r.json()
        return coupons[0] if coupons else None


@async_retry()
async def get_all_active_coupons(per_page: int = 20) -> list[dict]:
    """Fetch all active (published) coupons from WooCommerce."""
    async with _client() as client:
        r = await client.get(
            f"{_BASE}/coupons",
            params={"per_page": per_page, "status": "publish"}
        )
        r.raise_for_status()
        return r.json()


# ── Product Categories ──────────────────────────────────────────────


@async_retry()
async def get_categories(per_page: int = 100) -> list[dict]:
    """Fetch all product categories."""
    async with _client() as client:
        r = await client.get(
            f"{_BASE}/products/categories",
            params={"per_page": per_page}
        )
        r.raise_for_status()
        return r.json()


# ── Customers ───────────────────────────────────────────────────────


@async_retry()
async def get_orders_by_phone(phone: str) -> list[dict]:
    """
    Get orders for a phone number — used for per-user coupon usage checks.
    WooCommerce search supports billing phone.
    """
    async with _client() as client:
        r = await client.get(
            f"{_BASE}/orders",
            params={"search": phone, "per_page": 100}
        )
        r.raise_for_status()
        return r.json()


# ── Health Check ────────────────────────────────────────────────────


async def health_check() -> bool:
    """Check if WooCommerce API is reachable."""
    try:
        async with _client() as client:
            r = await client.get(f"{_BASE}/system_status", params={"_fields": "environment"})
            return r.status_code == 200
    except Exception:
        return False
