import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..models import User
from ..reports import PERIODS, REPORTS, run_report
from ..security import require_viewer

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _key(key: str) -> str:
    if key not in REPORTS:
        raise HTTPException(404, "Unknown report")
    return key


@router.get("")
def list_reports(_: User = Depends(require_viewer)):
    return {"periods": PERIODS, "reports": [{"key": k, "title": r["title"], "description": r["description"],
                                             "periodic": r["periodic"], "icon": r["icon"]} for k, r in REPORTS.items()]}


@router.get("/{key}")
def get_report(key: str, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    return run_report(db, _key(key), days)


def _cell(value):
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value  # never a formula when opened in Excel
    return value


@router.get("/{key}/export.csv")
def export_report(key: str, request: Request, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                  user: User = Depends(require_viewer)):
    report = run_report(db, _key(key), days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["title"] for c in report["columns"]])
    for row in report["rows"]:
        w.writerow([_cell(row.get(c["key"])) for c in report["columns"]])
    audit(db, request, user.username, "report_export", target=report["title"], days=report["days"], rows=len(report["rows"]))
    db.commit()
    period = f"-{days}d" if report["periodic"] else ""
    name = f"vcentral-{key}{period}-{report['generated_at'][:10]}.csv"
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename={name}"})
