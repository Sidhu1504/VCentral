from sqlalchemy.orm import Session

from .db import utcnow
from .models import SystemState


def get_state(db: Session, key: str) -> dict:
    row = db.get(SystemState, key)
    return dict(row.value) if row else {}


def set_state(db: Session, key: str, value: dict) -> None:
    row = db.get(SystemState, key)
    if row is None:
        db.add(SystemState(key=key, value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()
