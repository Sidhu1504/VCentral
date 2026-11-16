from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db, iso, utcnow
from ..models import NotificationLog, User
from ..notify import chat_configured, email_configured, send_chat, send_email
from ..onboarding import get_policy, save_policy
from ..runtime import CHOICES, defaults, offline_after_seconds, runtime, save_runtime
from ..security import require_admin, require_operator, require_viewer
from ..state import get_state, set_state

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
def status(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    s = get_settings()
    return {
        "vcenter_host": s.vcenter_host,
        "sync_interval_seconds": runtime()["sync_interval_seconds"],
        "sync": get_state(db, "sync"),
        "sync_request": get_state(db, "sync_request"),
        "subnets": [str(n) for n in s.subnet_list],
    }


@router.post("/sync")
def request_sync(request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    set_state(db, "sync_request", {"pending": True, "by": user.username, "requested_at": iso(utcnow())})
    audit(db, request, user.username, "request_sync")
    db.commit()
    return {"ok": True}


def _settings_out(db: Session) -> dict:
    s = get_settings()
    return {"values": runtime(), "defaults": defaults(), "choices": CHOICES,
            "agent_offline_after_seconds": offline_after_seconds(),
            "onboarding": get_policy(db),
            "notifications": {"email": email_configured(), "smtp_host": s.smtp_host or None, "smtp_from": s.smtp_from or None,
                              "chat": chat_configured(), "admin_emails": s.admin_email_list,
                              "public_url": s.public_url or None}}


@router.get("/settings")
def get_runtime_settings(db: Session = Depends(get_db), _: User = Depends(require_operator)):
    return _settings_out(db)


class PolicyIn(BaseModel):
    enabled: bool | None = None
    include_after_days: int | None = None
    every_days: int | None = None
    overdue_after_days: int | None = None


class SettingsIn(BaseModel):
    sync_interval_seconds: int | None = None
    agent_report_interval_seconds: int | None = None
    agent_inventory_interval_seconds: int | None = None
    onboarding: PolicyIn | None = None


@router.put("/settings")
def put_runtime_settings(body: SettingsIn, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_admin)):
    changes = body.model_dump(exclude_none=True, exclude={"onboarding"})
    policy = body.onboarding.model_dump(exclude_none=True) if body.onboarding else {}
    try:
        save_runtime(db, changes)
        if policy:
            save_policy(db, policy)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, request, user.username, "update_settings", **changes, **{f"onboarding.{k}": v for k, v in policy.items()})
    db.commit()
    return _settings_out(db)


class TestIn(BaseModel):
    channel: str = Field(pattern="^(email|chat)$")
    to: str | None = Field(None, max_length=255)  # email address; defaults to your own or the admin list


@router.post("/notifications/test")
def test_notification(body: TestIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    subject = "vCentral test message"
    text = f"This is a test from vCentral, sent by {user.username}. If you can read it, notifications work."
    if body.channel == "email":
        to = [body.to] if body.to else ([user.email] if user.email else get_settings().admin_email_list)
        if not to:
            raise HTTPException(400, "Enter an address to send the test to")
        ok = send_email(db, to, subject, text, kind="test")
    else:
        ok = send_chat(db, text, subject, kind="test")
    audit(db, request, user.username, "test_notification", channel=body.channel, success=ok)
    db.commit()
    last = db.scalar(select(NotificationLog).order_by(NotificationLog.id.desc()))
    return {"ok": ok, "error": None if ok else last.error}
