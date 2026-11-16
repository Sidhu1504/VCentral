from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, iso
from ..models import VM, AuditLog, User, VMHistory
from ..security import require_admin, require_viewer

router = APIRouter(prefix="/api", tags=["activity"])


@router.get("/activity")
def activity(kind: str | None = None, search: str | None = None,
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
             db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    q = select(VMHistory, VM.name).join(VM, VM.id == VMHistory.vm_id)
    if kind:
        q = q.where(VMHistory.kind == kind)
    if search:
        q = q.where(VM.name.ilike(f"%{search.strip()}%"))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.order_by(VMHistory.ts.desc(), VMHistory.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    items = [{"id": h.id, "ts": iso(h.ts), "kind": h.kind, "message": h.message, "data": h.data,
              "vm_id": h.vm_id, "vm_name": name} for h, name in rows]
    return {"items": items, "total": total}


@router.get("/audit")
def audit_log(username: str | None = None, action: str | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
              db: Session = Depends(get_db), _: User = Depends(require_admin)):
    q = select(AuditLog)
    if username:
        q = q.where(AuditLog.username == username)
    if action:
        q = q.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditLog.ts.desc(), AuditLog.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    items = [{"id": a.id, "ts": iso(a.ts), "username": a.username, "action": a.action, "target": a.target,
              "detail": a.detail, "source_ip": a.source_ip, "success": a.success} for a in rows]
    return {"items": items, "total": total}
