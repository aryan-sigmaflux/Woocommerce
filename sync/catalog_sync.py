"""
Catalog Sync Engine — keeps WhatsApp Business Catalog in sync with WooCommerce.

Runs on:
  - Webhook triggers (instant, per-product)
  - APScheduler full sync (every 6 hours)
"""

import logging

from clients import woocommerce as woo_client
from clients import meta_commerce
from sync.product_transformer import transform_product

logger = logging.getLogger(__name__)


# ── Full Sync (scheduled every 6h) ──────────────────────────────────


async def full_catalog_sync():
    """
    Pull all published products from WooCommerce, batch upsert to Meta catalog.
    Also detects orphaned items in Meta (deleted from Woo) and removes them.
    """
    logger.info("")
    logger.info("🔄" + "=" * 58)
    logger.info("🔄  FULL CATALOG SYNC — STARTED")
    logger.info("🔄" + "=" * 58)
    logger.info("")

    try:
        # 1. Fetch all published products from WooCommerce
        woo_products = await woo_client.get_all_products_paginated()
        logger.info(f"[SYNC] Fetched {len(woo_products)} products from WooCommerce")

        if not woo_products:
            logger.warning("[SYNC] No products found in WooCommerce. Skipping sync.")
            return {"synced": 0, "deleted": 0, "errors": 0}

        # 2. Transform all products to Meta format
        meta_items = []
        errors = 0
        for p in woo_products:
            try:
                meta_items.append(transform_product(p))
            except Exception as e:
                logger.error(f"[SYNC] Error transforming product {p.get('id')}: {e}")
                errors += 1

        # 3. Batch upsert to Meta in chunks of 1000
        #    (Must happen BEFORE product set creation — Meta rejects empty sets)
        for i in range(0, len(meta_items), 1000):
            chunk = meta_items[i:i + 1000]
            try:
                await meta_commerce.batch_upsert(chunk)
                logger.info(f"[SYNC] Batch upserted {len(chunk)} items (offset {i})")
            except Exception as e:
                logger.error(f"[SYNC] Batch upsert failed at offset {i}: {e}")
                errors += len(chunk)

        # 4. Ensure Meta Product Sets exist for all categories
        #    (Done after upsert so filter-based sets find matching products)
        try:
            await meta_commerce.sync_product_sets(meta_items)
        except Exception as e:
            logger.error(f"[SYNC] Error syncing product sets: {e}")

        # 4. Detect and delete orphaned items from Meta catalog
        deleted = 0
        try:
            meta_retailer_ids = await meta_commerce.get_all_catalog_items()
            woo_retailer_ids = {
                (p.get("sku", "") or f"woo_{p['id']}")
                for p in woo_products
            }
            orphans = set(meta_retailer_ids) - woo_retailer_ids

            if orphans:
                logger.info(f"[SYNC] Found {len(orphans)} orphaned items to delete: {orphans}")
            else:
                logger.info("[SYNC] No orphaned items found")

            for orphan_id in orphans:
                try:
                    await meta_commerce.delete_item(orphan_id)
                    deleted += 1
                except Exception as e:
                    logger.error(f"[SYNC] Failed to delete orphan {orphan_id}: {e}")
                    errors += 1

            if orphans:
                logger.info("")
                logger.info("🗑️" + "=" * 58)
                logger.info(f"🗑️  SYNC: DELETING {len(orphans)} ORPHANED PRODUCTS")
                for oid in orphans:
                    logger.info(f"🗑️  → {oid}")
                logger.info("🗑️" + "=" * 58)
                logger.info("")
        except Exception as e:
            logger.error(f"[SYNC] Failed to check for orphans: {e}")

        result = {
            "synced": len(meta_items) - errors,
            "deleted": deleted,
            "errors": errors,
        }
        logger.info("")
        logger.info("✅" + "=" * 58)
        logger.info(f"✅  FULL CATALOG SYNC — COMPLETED")
        logger.info(f"✅  Synced: {result['synced']} | Deleted: {result['deleted']} | Errors: {result['errors']}")
        logger.info("✅" + "=" * 58)
        logger.info("")
        return result

    except Exception as e:
        logger.error("")
        logger.error("❌" + "=" * 58)
        logger.error(f"❌  FULL CATALOG SYNC — FAILED: {e}")
        logger.error("❌" + "=" * 58)
        logger.error("")
        raise


# ── Real-time Sync (webhook-triggered) ──────────────────────────────


async def sync_product_created(woo_product_id: int):
    """Fetch product from Woo and push to Meta catalog."""
    logger.info(f"[SYNC] ✅ Syncing CREATED product #{woo_product_id} to Meta catalog...")
    product = await woo_client.get_product(woo_product_id)
    item = transform_product(product)
    # Upsert first, then sync sets (Meta rejects empty product sets)
    await meta_commerce.upsert_single_item(item)
    await meta_commerce.sync_product_sets([item])


async def sync_product_updated(woo_product_id: int):
    """Fetch updated product from Woo and update in Meta catalog."""
    logger.info(f"[SYNC] ✏️ Syncing UPDATED product #{woo_product_id} to Meta catalog...")
    product = await woo_client.get_product(woo_product_id)
    item = transform_product(product)
    # Upsert first, then sync sets (Meta rejects empty product sets)
    await meta_commerce.upsert_single_item(item)
    await meta_commerce.sync_product_sets([item])


async def sync_product_deleted(woo_product_id: int, product_sku: str = None):
    """Delete product from Meta catalog."""
    logger.info(f"[SYNC] 🗑️ Syncing DELETED product #{woo_product_id} — removing from Meta catalog...")
    
    # Try to use provided SKU
    retailer_id = ""
    if product_sku and product_sku != "N/A":
        retailer_id = product_sku
    else:
        # Fetch product to get SKU before it's fully gone (if not provided)
        try:
            product = await woo_client.get_product(woo_product_id)
            retailer_id = product.get("sku", "")
        except Exception:
            pass
            
    if not retailer_id:
        retailer_id = f"woo_{woo_product_id}"
        
    await meta_commerce.delete_item(retailer_id)


# ── Status ──────────────────────────────────────────────────────────

# In-memory last sync status (updated by the scheduler)
_last_sync_result = None


def set_last_sync_result(result):
    global _last_sync_result
    _last_sync_result = result


def get_last_sync_result():
    return _last_sync_result
