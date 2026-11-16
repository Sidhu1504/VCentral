from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditLog
from .security import client_ip


def audit(db: Session, request: Request | None, username: str | None, action: str,
          target: str | None = None, success: bool = True, **detail) -> None:
    """Add an audit entry to the session; the caller commits."""
    db.add(AuditLog(
        username=username, action=action, target=target, detail=detail, success=success,
        source_ip=client_ip(request) if request is not None else None,
    ))
