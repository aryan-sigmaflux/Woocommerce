"""
APScheduler job registration.
- Full catalog sync every 6 hours
- Tracking link poller every 15 minutes
- Session expiry cleanup every 30 minutes
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _run_full_catalog_sync():
    """Wrapper for full catalog sync job."""
    from sync.catalog_sync import full_catalog_sync, set_last_sync_result
    try:
        result = await full_catalog_sync()
        set_last_sync_result(result)
    except Exception as e:
        logger.error(f"[SCHEDULER] Full catalog sync failed: {e}")


async def _run_tracking_poller():
    """Wrapper for tracking link poller job."""
    from monitors.tracking_monitor import poll_tracking_updates
    try:
        await poll_tracking_updates()
    except Exception as e:
        logger.error(f"[SCHEDULER] Tracking poller failed: {e}")


async def _run_session_cleanup():
    """Expire stale sessions silently (no WhatsApp messages sent)."""
    from db.database import async_session_factory
    from bot.session_manager import get_expired_sessions, cancel_session

    try:
        async with async_session_factory() as db:
            expired = await get_expired_sessions(db)
            for session in expired:
                # Silently cancel — do NOT send WhatsApp message
                # The bot only messages when the user messages first
                await cancel_session(db, session)
                logger.info(f"[SCHEDULER] Expired session {session.id} for {session.phone} (silent)")

            await db.commit()

        if expired:
            logger.info(f"[SCHEDULER] Cleaned up {len(expired)} expired sessions")
    except Exception as e:
        logger.error(f"[SCHEDULER] Session cleanup failed: {e}")


def start_scheduler():
    """Register all scheduled jobs and start the scheduler."""
    # Full catalog sync every 6 hours
    scheduler.add_job(
        _run_full_catalog_sync,
        trigger=IntervalTrigger(hours=6),
        id="full_catalog_sync",
        name="Full Catalog Sync",
        replace_existing=True,
    )

    # Tracking link poller every 15 minutes
    scheduler.add_job(
        _run_tracking_poller,
        trigger=IntervalTrigger(minutes=15),
        id="tracking_poller",
        name="Tracking Link Poller",
        replace_existing=True,
    )

    # Session expiry cleanup every 30 minutes
    scheduler.add_job(
        _run_session_cleanup,
        trigger=IntervalTrigger(minutes=30),
        id="session_cleanup",
        name="Session Expiry Cleanup",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[SCHEDULER] All jobs registered and scheduler started")
