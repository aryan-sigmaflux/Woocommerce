"""
manual_update.py
Run this script manually to force a full catalog sync from WooCommerce to Meta/WhatsApp.
"""

import asyncio
import logging
import sys

from sync.catalog_sync import full_catalog_sync

# Setup simple logging for the console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

async def main():
    print("Starting manual full catalog sync...")
    try:
        result = await full_catalog_sync()
        print(f"\n✅ Catalog sync completed successfully!")
        print(f"   Synced: {result.get('synced', 0)}")
        print(f"   Deleted: {result.get('deleted', 0)}")
        print(f"   Errors: {result.get('errors', 0)}")
    except Exception as e:
        print(f"\n❌ Catalog sync failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
