"""Owners: suggestions for VMs whose creator is unknown, accepting them, and setting one owner for many VMs."""
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db, iso, utcnow
from ..models import VM, User, VMHistory
from ..security import require_operator, require_viewer
from ..state import get_state, set_state

router = APIRouter(prefix="/api", tags=["owners"])


@router.get("/owners")
def owners(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    vms = list(db.scalars(select(VM).where(VM.is_deleted.is_(False))))
    unknown = sorted((v for v in vms if not v.owner), key=lambda v: (
        {"high": 0, "medium": 1, "low": 2}.get(v.owner_suggestion_confidence, 3), v.name.lower()))
    sources = Counter(v.owner_source or "unknown" for v in vms)
    return {
        "stats": {"total": len(vms), "known": len(vms) - len(unknown), "unknown": len(unknown),
                  "suggested": sum(1 for v in unknown if v.owner_suggestion), "sources": dict(sources),
                  "confidence": dict(Counter(v.owner_suggestion_confidence for v in unknown if v.owner_suggestion))},
        "last_scan": get_state(db, "owner_scan"),
        "scan_pending": bool(get_state(db, "owner_scan_request").get("pending")),
        "items": [{"id": v.id, "name": v.name, "folder": v.folder, "power_state": v.power_state, "os_family": v.os_family,
                   "suggestion": v.owner_suggestion, "reason": v.owner_suggestion_reason,
                   "confidence": v.owner_suggestion_confidence} for v in unknown],
    }


@router.post("/owners/scan")
def scan(request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    set_state(db, "owner_scan_request", {"pending": True, "by": user.username, "requested_at": iso(utcnow())})
    audit(db, request, user.username, "owner_scan")
    db.commit()
    return {"ok": True}


def _set_owner(db: Session, vm: VM, owner: str | None, source: str | None, user: User) -> None:
    old = vm.owner
    vm.owner, vm.owner_source = owner, source
    vm.owner_checked = owner is not None
    vm.owner_suggestion = vm.owner_suggestion_reason = vm.owner_suggestion_confidence = None
    db.add(VMHistory(vm_id=vm.id, ts=utcnow(), kind="owner_changed",
                     message=f"Owner set to {owner or 'automatic'} by {user.username}"
                             + (" (accepted suggestion)" if source == "suggested" else ""),
                     data={"old": old, "new": owner, "source": source}))


class AcceptIn(BaseModel):
    vm_ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/owners/accept")
def accept(body: AcceptIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    done = 0
    for vm in db.scalars(select(VM).where(VM.id.in_(body.vm_ids), VM.owner.is_(None), VM.owner_suggestion.is_not(None))):
        _set_owner(db, vm, vm.owner_suggestion, "suggested", user)
        done += 1
    audit(db, request, user.username, "owner_accept_suggestions", vms=done)
    db.commit()
    return {"accepted": done}


class BulkOwnerIn(BaseModel):
    vm_ids: list[int] = Field(min_length=1, max_length=5000)
    owner: str = Field(max_length=255)  # "" = back to automatic detection


@router.post("/vms/bulk-owner")
def bulk_owner(body: BulkOwnerIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    owner = body.owner.strip() or None
    vms = list(db.scalars(select(VM).where(VM.id.in_(body.vm_ids))))
    if not vms:
        raise HTTPException(404, "No such VMs")
    for vm in vms:
        _set_owner(db, vm, owner, "manual" if owner else None, user)
    audit(db, request, user.username, "owner_bulk_set", owner=owner, vms=len(vms))
    db.commit()
    return {"updated": len(vms)}
