"""
Pincode serviceability checker.
Loads pincodes from data/serviceable_pincodes.txt at startup and provides
a fast in-memory lookup.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# In-memory set of serviceable pincodes
_serviceable_pincodes: set[str] = set()
_loaded = False


def _load_pincodes():
    """Load the pincode list from the data file."""
    global _serviceable_pincodes, _loaded

    file_path = Path(__file__).resolve().parent.parent / "data" / "serviceable_pincodes.txt"

    if not file_path.exists():
        logger.warning(f"[PINCODE] Serviceable pincodes file not found: {file_path}")
        _loaded = True
        return

    count = 0
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                _serviceable_pincodes.add(line)
                count += 1

    _loaded = True
    logger.info(f"[PINCODE] Loaded {count} serviceable pincodes")


def is_pincode_serviceable(pincode: str) -> bool:
    """
    Check if a pincode is in our serviceable area.
    Returns True if serviceable, False otherwise.
    If no pincodes are loaded (file missing/empty), defaults to True (allow all).
    """
    if not _loaded:
        _load_pincodes()

    # If no pincodes loaded, allow everything (fail-open)
    if not _serviceable_pincodes:
        logger.warning("[PINCODE] No serviceable pincodes loaded — allowing all")
        return True

    return pincode.strip() in _serviceable_pincodes


def reload_pincodes():
    """Reload the pincode list from disk (useful for admin hot-reload)."""
    global _serviceable_pincodes, _loaded
    _serviceable_pincodes = set()
    _loaded = False
    _load_pincodes()


def get_serviceable_count() -> int:
    """Return count of loaded serviceable pincodes."""
    if not _loaded:
        _load_pincodes()
    return len(_serviceable_pincodes)


# ── Per-phone pincode tracking (for greeting flow, no session needed) ──

# Phones currently waiting for a pincode response
_awaiting_pincode: set[str] = set()

# Phones with a validated serviceable pincode  {phone: pincode}
_validated_pincodes: dict[str, str] = {}


def mark_awaiting_pincode(phone: str):
    """Mark that we have asked this phone for their pincode."""
    _awaiting_pincode.add(phone)


def is_awaiting_pincode(phone: str) -> bool:
    """Check if this phone is expected to send a pincode."""
    return phone in _awaiting_pincode


def clear_awaiting_pincode(phone: str):
    """Remove phone from awaiting set."""
    _awaiting_pincode.discard(phone)


def set_validated_pincode(phone: str, pincode: str):
    """Store a validated pincode for a phone (in-memory cache)."""
    _validated_pincodes[phone] = pincode
    _awaiting_pincode.discard(phone)


def get_validated_pincode(phone: str) -> str | None:
    """Get the cached validated pincode for a phone, if any."""
    return _validated_pincodes.get(phone)
