"""Self sign-up policy (Settings page). Accounts created this way are always Viewers."""
import logging

from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .notify import chat_configured, email_configured, send_chat, send_email
from .state import get_state, set_state

log = logging.getLogger("vcentral.signup")
KEYS = ("enabled", "requires_approval")


def get_signup_policy(db: Session) -> dict:
    s = get_settings()
    policy = {"enabled": s.signup_enabled, "requires_approval": s.signup_requires_approval}
    stored = get_state(db, "signup")
    for k in KEYS:
        if isinstance(stored.get(k), bool):
            policy[k] = stored[k]
    return policy


def save_signup_policy(db: Session, changes: dict) -> dict:
    policy = {**get_signup_policy(db), **{k: bool(v) for k, v in changes.items() if k in KEYS}}
    set_state(db, "signup", policy)
    return policy


def notify_admins(username: str, full_name: str | None) -> None:
    """Tell the admin team a sign-up is waiting (background task; failures are only logged)."""
    s = get_settings()
    who = f"{full_name} ({username})" if full_name else username
    link = f"{s.public_url.rstrip('/')}/users" if s.public_url else "the Users page"
    text = f"New vCentral sign-up waiting for approval: {who}. Approve or reject it on {link}."
    try:
        with SessionLocal() as db:
            if chat_configured():
                send_chat(db, text, "vCentral sign-up request", kind="signup")
            if email_configured() and s.admin_email_list:
                send_email(db, s.admin_email_list, "vCentral sign-up request", text, kind="signup")
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not notify admins about a sign-up")
