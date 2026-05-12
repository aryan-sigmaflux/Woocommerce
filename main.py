"""
WhatsApp Checkout Bot — WooCommerce Integration
FastAPI application init, router registration, and lifecycle events.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db.database import init_db
from scheduler import start_scheduler

# ── Logging Setup ───────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── App Lifecycle ───────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    logger.info("=" * 60)
    logger.info("  WhatsApp Checkout Bot — Starting...")
    logger.info("=" * 60)

    # Initialize database tables
    await init_db()
    logger.info("[STARTUP] Database tables initialized")

    # Start APScheduler
    start_scheduler()
    logger.info("[STARTUP] Scheduler started")

    logger.info(f"[STARTUP] App base URL: {settings.APP_BASE_URL}")
    logger.info(f"[STARTUP] WooCommerce: {settings.WOO_BASE_URL}")
    logger.info("[STARTUP] Ready to receive webhooks ✅")

    yield

    # Shutdown
    from scheduler import scheduler
    scheduler.shutdown(wait=False)
    logger.info("[SHUTDOWN] Scheduler stopped")
    logger.info("[SHUTDOWN] Goodbye 👋")


# ── FastAPI App ─────────────────────────────────────────────────────

app = FastAPI(
    title="WhatsApp Checkout Bot",
    description="WooCommerce × WhatsApp Catalog checkout automation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (for admin endpoints if accessed from a frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Register Routers ───────────────────────────────────────────────

from routers.whatsapp_webhook import router as whatsapp_router
from routers.woo_webhook import router as woo_router
from routers.payment_webhook import router as payment_router
from routers.admin import router as admin_router

app.include_router(whatsapp_router)
app.include_router(woo_router)
app.include_router(payment_router)
app.include_router(admin_router)

# ── Webhook fallback (Catches Meta hitting /webhook instead of /webhooks/whatsapp)
from routers.whatsapp_webhook import verify_webhook, receive_webhook
app.add_api_route("/webhook", verify_webhook, methods=["GET"])
app.add_api_route("/webhook", receive_webhook, methods=["POST"])


# ── Health Endpoints ────────────────────────────────────────────────


@app.get("/health", tags=["Health"])
async def health():
    """Service liveness check."""
    return {"status": "ok", "service": "whatsapp-checkout-bot"}


@app.get("/health/db", tags=["Health"])
async def health_db():
    """Database connectivity check."""
    from db.database import async_session_factory
    try:
        async with async_session_factory() as session:
            await session.execute("SELECT 1" if False else __import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}


@app.get("/health/woo", tags=["Health"])
async def health_woo():
    """WooCommerce API reachability check."""
    from clients.woocommerce import health_check
    ok = await health_check()
    return {
        "status": "ok" if ok else "error",
        "woocommerce": "reachable" if ok else "unreachable",
    }


# ── Root ────────────────────────────────────────────────────────────


@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "WhatsApp Checkout Bot",
        "version": "1.0.0",
        "docs": "/docs",
    }
