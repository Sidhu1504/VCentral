"""Settings admins can change on the Settings page (stored in the database, applied without a restart).

Environment variables provide the defaults. Values are cached for a few seconds because they are read on
every agent report and every online/offline check.
"""
import threading
import time

from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .state import get_state, set_state

CHOICES = {
    "sync_interval_seconds": [20, 30, 60, 120, 300],
    "agent_report_interval_seconds": [10, 20, 30, 60],
    "agent_inventory_interval_seconds": [900, 1800, 3600, 21600],
    "ip_scan_interval_minutes": [0, 5, 15, 30, 60],
}

_lock = threading.Lock()
_cache: tuple[float, dict] | None = None
CACHE_SECONDS = 5


def defaults() -> dict:
    s = get_settings()
    return {k: getattr(s, k) for k in CHOICES}


def _load(db: Session) -> dict:
    values = defaults()
    stored = get_state(db, "settings")
    for key, allowed in CHOICES.items():
        if stored.get(key) in allowed:
            values[key] = stored[key]
    return values


def runtime() -> dict:
    global _cache
    with _lock:
        if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return dict(_cache[1])
    with SessionLocal() as db:
        values = _load(db)
    with _lock:
        _cache = (time.monotonic(), values)
    return dict(values)


def save_runtime(db: Session, changes: dict) -> dict:
    global _cache
    for key, value in changes.items():
        if key not in CHOICES or value not in CHOICES[key]:
            raise ValueError(f"{key} must be one of {CHOICES.get(key)}")
    values = {**_load(db), **changes}
    set_state(db, "settings", values)
    with _lock:
        _cache = None
    return values


def offline_after_seconds() -> int:
    """An agent counts as offline after missing three reports."""
    return max(3 * runtime()["agent_report_interval_seconds"] + 10, 30)
