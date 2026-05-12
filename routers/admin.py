"""
Admin API Router — session management, sync control, address lookup.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from bot import session_manager
from sync import catalog_sync
from clients import woocommerce as woo_client
from utils.phone import normalise_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Catalog Sync ────────────────────────────────────────────────────


@router.post("/sync/full")
async def trigger_full_sync():
    """Trigger a manual full catalog sync."""
    try:
        result = await catalog_sync.full_catalog_sync()
        catalog_sync.set_last_sync_result(result)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"[ADMIN] Full sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sync/status")
async def sync_status():
    """Get last sync run info."""
    result = catalog_sync.get_last_sync_result()
    if result:
        return {"status": "ok", "last_sync": result}
    return {"status": "ok", "last_sync": None, "message": "No sync has run yet"}


# ── Sessions ────────────────────────────────────────────────────────


@router.get("/sessions")
async def list_active_sessions(db: AsyncSession = Depends(get_db)):
    """List all active (non-terminal) checkout sessions."""
    sessions = await session_manager.get_all_active_sessions(db)
    return {
        "count": len(sessions),
        "sessions": [
            {
                "id": str(s.id),
                "phone": s.phone,
                "state": s.state,
                "woo_pending_order_id": s.woo_pending_order_id,
                "subtotal": float(s.subtotal) if s.subtotal else 0,
                "total": float(s.total) if s.total else 0,
                "payment_method": s.payment_method,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            }
            for s in sessions
        ],
    }


@router.get("/sessions/{phone}")
async def get_session(phone: str, db: AsyncSession = Depends(get_db)):
    """Get active session for a phone number."""
    phone = normalise_phone(phone)
    session = await session_manager.get_active_session(db, phone)
    if not session:
        raise HTTPException(status_code=404, detail="No active session found")
    return {
        "id": str(session.id),
        "phone": session.phone,
        "state": session.state,
        "cart": session.cart,
        "woo_pending_order_id": session.woo_pending_order_id,
        "selected_address_id": str(session.selected_address_id) if session.selected_address_id else None,
        "coupon_code": session.coupon_code,
        "discount_amount": float(session.discount_amount) if session.discount_amount else 0,
        "payment_method": session.payment_method,
        "subtotal": float(session.subtotal) if session.subtotal else 0,
        "total": float(session.total) if session.total else 0,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


@router.delete("/sessions/{phone}")
async def force_close_session(phone: str, db: AsyncSession = Depends(get_db)):
    """Force-close a stuck session."""
    phone = normalise_phone(phone)
    session = await session_manager.get_active_session(db, phone)
    if not session:
        raise HTTPException(status_code=404, detail="No active session found")

    await session_manager.cancel_session(db, session)
    return {"status": "cancelled", "session_id": str(session.id)}


# ── Pending Orders (Incomplete Checkouts) ──────────────────────────


@router.get("/sessions/pending")
async def list_pending_sessions(db: AsyncSession = Depends(get_db)):
    """
    List all sessions where cart was sent to user but checkout is incomplete.
    These are orders in progress — the user started but hasn't finished.
    """
    sessions = await session_manager.get_pending_sessions(db)

    # Categorize by checkout stage
    def _stage_label(state: str) -> str:
        if state in ("WELCOME_SENT",):
            return "Cart Sent"
        elif state in ("COLLECTING_PINCODE_CHECK",):
            return "Checking Pincode"
        elif state.startswith("COLLECTING_ADDRESS") or state in ("ADDRESS_CHECK", "ADDRESS_CONFIRM"):
            return "Collecting Address"
        elif state in ("COUPON_PROMPT", "COLLECTING_COUPON"):
            return "Applying Coupon"
        elif state in ("ORDER_SUMMARY", "PAYMENT_METHOD_SELECT"):
            return "Selecting Payment"
        elif state in ("PAYMENT_LINK_SENT",):
            return "Awaiting Payment"
        elif state in ("ORDER_CONFIRM_PROMPT",):
            return "Confirming Order"
        else:
            return "In Progress"

    return {
        "count": len(sessions),
        "sessions": [
            {
                "id": str(s.id),
                "phone": s.phone,
                "state": s.state,
                "stage": _stage_label(s.state),
                "cart_items": len([i for i in (s.cart or []) if not i.get("_temp_address")]),
                "subtotal": float(s.subtotal) if s.subtotal else 0,
                "total": float(s.total) if s.total else 0,
                "payment_method": s.payment_method,
                "coupon_code": s.coupon_code,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "idle_minutes": int(
                    (datetime.now(timezone.utc) - s.updated_at).total_seconds() / 60
                ) if s.updated_at else None,
            }
            for s in sessions
        ],
    }


# ── Pincode Serviceability ─────────────────────────────────────────


@router.post("/pincodes/reload")
async def reload_pincodes():
    """Hot-reload the serviceable pincodes list from disk."""
    from utils.pincode_service import reload_pincodes, get_serviceable_count
    reload_pincodes()
    return {"status": "ok", "count": get_serviceable_count()}


@router.get("/pincodes/check/{pincode}")
async def check_pincode(pincode: str):
    """Check if a pincode is serviceable."""
    from utils.pincode_service import is_pincode_serviceable, get_serviceable_count
    return {
        "pincode": pincode,
        "serviceable": is_pincode_serviceable(pincode),
        "total_loaded": get_serviceable_count(),
    }


# ── Addresses ───────────────────────────────────────────────────────


@router.get("/users/{phone}/addresses")
async def get_user_addresses(phone: str, db: AsyncSession = Depends(get_db)):
    """Get saved addresses for a phone number."""
    phone = normalise_phone(phone)
    addresses = await session_manager.get_addresses_for_phone(db, phone)
    return {
        "phone": phone,
        "count": len(addresses),
        "addresses": [
            {
                "id": str(a.id),
                "full_name": a.full_name,
                "line1": a.line1,
                "line2": a.line2,
                "city": a.city,
                "state": a.state,
                "pincode": a.pincode,
                "label": a.label,
                "is_last_used": a.is_last_used,
            }
            for a in addresses
        ],
    }
