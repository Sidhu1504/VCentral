"""Infrastructure page: vCenter Server (details, health, services) and ESXi hosts (monitoring)."""
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..infra import ApplianceClient, ApplianceError, CORE_SERVICES, host_details, vcenter_overview
from ..models import VM, User
from ..security import require_admin, require_viewer
from ..vcenter import VCenterClient
from .vms import vcenter_ips

router = APIRouter(prefix="/api/infra", tags=["infrastructure"])


def _configured():
    s = get_settings()
    if not s.vcenter_host:
        raise HTTPException(400, "vCenter is not configured")
    return s


@router.get("/vcenter")
def vcenter(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    s = _configured()
    client = VCenterClient(s)
    try:
        out = vcenter_overview(client, s)
    finally:
        client.disconnect()
    ips = vcenter_ips(db)
    vm = next((v for v in db.scalars(select(VM).where(VM.is_deleted.is_(False)))
               if ips & (set(v.last_known_ips or []) | {v.primary_ip})), None) if ips else None
    out["vm"] = {"id": vm.id, "name": vm.name, "power_state": vm.power_state, "cpu_pct": vm.cpu_pct,
                 "mem_pct": vm.mem_pct, "esxi_host": vm.esxi_host} if vm else None
    out["ips"] = sorted(ips)
    return out


@router.post("/vcenter/services/{service}/{action}")
def vcenter_service(request: Request, service: str = Path(..., pattern=r"^[A-Za-z0-9._-]{1,100}$"),
                    action: str = Path(..., pattern="^(start|stop|restart)$"),
                    db: Session = Depends(get_db), user: User = Depends(require_admin)):
    app = ApplianceClient(_configured(), timeout=180)  # a restart can take minutes
    try:
        app.service_action(service, action)
    except ApplianceError as exc:
        audit(db, request, user.username, f"vcenter_service_{action}", target=service, success=False, error=str(exc)[:300])
        db.commit()
        raise HTTPException(exc.status if exc.status in (401, 403, 404) else 502, str(exc))
    finally:
        app.logout()
    audit(db, request, user.username, f"vcenter_service_{action}", target=service)
    db.commit()
    note = " vCenter (and vCentral's view of it) may be unavailable for a few minutes." if service in CORE_SERVICES else ""
    return {"ok": True, "message": f"{action.capitalize()} of {service} done.{note}"}


@router.get("/hosts")
def hosts(_: User = Depends(require_viewer)):
    client = VCenterClient(_configured())
    try:
        return {"items": host_details(client)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not read ESXi hosts from vCenter: {getattr(exc, 'msg', None) or exc}")
    finally:
        client.disconnect()
