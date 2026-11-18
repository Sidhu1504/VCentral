import csv
import io
from datetime import timedelta

from pyVmomi import vim, vmodl
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, func
from sqlalchemy.orm import Session

from ..agents import agent_state
from ..audit import audit
from .. import signing
from ..toolsinstall import fix_tools
from ..vcenter import VCenterClient
from ..config import get_settings
from ..db import get_db, iso, utcnow
from ..health import evaluate_health, tools_state
from ..runtime import offline_after_seconds
from ..state import get_state
from ..models import VM, Agent, User, VMHistory, VMMetric, VMMetricHourly
from ..security import require_operator, require_viewer
from .agents_admin import agent_detail, agent_summary
from .series import downsample, raw_points

router = APIRouter(prefix="/api/vms", tags=["vms"])

SORTABLE = {
    "name": VM.name, "power_state": VM.power_state, "guest_os": VM.guest_os, "primary_ip": VM.primary_ip,
    "owner": VM.owner, "cpu_pct": VM.cpu_pct, "mem_pct": VM.mem_pct, "disk_used_pct_max": VM.disk_used_pct_max,
    "num_cpu": VM.num_cpu, "memory_mb": VM.memory_mb, "storage_committed_gb": VM.storage_committed_gb,
    "first_seen_at": VM.first_seen_at, "vcenter_created_at": VM.vcenter_created_at, "esxi_host": VM.esxi_host,
}

RANGES = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24),
          "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}


def agent_states(db: Session, vm_ids) -> dict[int, str]:
    """vm_id -> "online"/"offline" for VMs with an active agent (online wins if there are several)."""
    out: dict[int, str] = {}
    ids = [i for i in vm_ids if i]
    if not ids:
        return out
    for a in db.scalars(select(Agent).where(Agent.vm_id.in_(ids), Agent.status == "active")):
        if out.get(a.vm_id) != "online":
            out[a.vm_id] = agent_state(a)
    return out


def vcenter_ips(db: Session) -> set[str]:
    """IPs of vCenter Server itself (stored by the sync), to label the VM that runs it."""
    return {i["ip"] for i in get_state(db, "network_infra").get("items", []) if i.get("kind") == "vcenter"}


def vm_summary(vm: VM, agent: str | None = None, vc_ips: set[str] | None = None) -> dict:
    ips = set(vm.last_known_ips or []) | ({vm.primary_ip} if vm.primary_ip else set())
    return {
        "agent": agent or "none",
        "role": "vcenter" if vc_ips and ips & vc_ips else None,
        "id": vm.id, "name": vm.name, "power_state": vm.power_state, "os_family": vm.os_family,
        "guest_os": vm.guest_os, "primary_ip": vm.primary_ip,
        "last_known_ip": (vm.last_known_ips or [None])[0], "hostname": vm.hostname,
        "owner": vm.owner, "owner_source": vm.owner_source, "num_cpu": vm.num_cpu, "memory_mb": vm.memory_mb,
        "storage_committed_gb": vm.storage_committed_gb, "cpu_pct": vm.cpu_pct, "mem_pct": vm.mem_pct,
        "disk_used_pct_max": vm.disk_used_pct_max, "tools_status": vm.tools_status,
        "esxi_host": vm.esxi_host, "cluster": vm.cluster, "folder": vm.folder, "managed": vm.managed,
        "is_deleted": vm.is_deleted, "first_seen_at": iso(vm.first_seen_at),
        "vcenter_created_at": iso(vm.vcenter_created_at), "deleted_at": iso(vm.deleted_at),
        "critical": vm.critical, "health": vm.health, "problems": vm.problems or [],
        "tools_version_status": vm.tools_version_status, "tools": tools_state(vm),
    }


def vm_detail(db: Session, vm: VM) -> dict:
    agents = list(db.scalars(select(Agent).where(Agent.vm_id == vm.id)
                             .order_by((Agent.status == "active").desc(), Agent.last_seen_at.desc().nulls_last())))
    current = agents[0] if agents else None
    return {
        **vm_summary(vm, agent_states(db, [vm.id]).get(vm.id), vcenter_ips(db)),
        "agent_detail": agent_detail(current, vm.name) if current else None,
        "other_agents": [agent_summary(a, vm.name) for a in agents[1:]],
        "uuid": vm.uuid, "bios_uuid": vm.bios_uuid, "moid": vm.moid, "guest_id": vm.guest_id,
        "ip_addresses": vm.ip_addresses, "last_known_ips": vm.last_known_ips,
        "mac_addresses": vm.mac_addresses, "storage_provisioned_gb": vm.storage_provisioned_gb,
        "guest_disks": vm.guest_disks, "tools_version_status": vm.tools_version_status,
        "datastores": vm.datastores, "annotation": vm.annotation, "custom_attributes": vm.custom_attributes,
        "notes": vm.notes, "cpu_usage_mhz": vm.cpu_usage_mhz, "mem_active_mb": vm.mem_active_mb,
        "uptime_seconds": vm.uptime_seconds, "boot_time": iso(vm.boot_time),
        "last_seen_at": iso(vm.last_seen_at),
    }


def _filtered(search, power, os_family, owner_unknown, status, owner, agent=None, critical=False, health=None):
    q = select(VM)
    if critical:
        q = q.where(VM.critical.is_(True))
    if health == "problem":
        q = q.where(VM.health != "ok")
    elif health in ("warning", "critical"):
        q = q.where(VM.health == health)
    if agent:
        cutoff = utcnow() - timedelta(seconds=offline_after_seconds())
        active = select(Agent.id).where(Agent.vm_id == VM.id, Agent.status == "active")
        online = active.where(Agent.last_seen_at >= cutoff)
        if agent == "none":
            q = q.where(~active.exists())
        elif agent == "online":
            q = q.where(online.exists())
        elif agent == "offline":
            q = q.where(active.exists(), ~online.exists())
    if status == "active":
        q = q.where(VM.is_deleted.is_(False))
    elif status == "deleted":
        q = q.where(VM.is_deleted.is_(True))
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(VM.name.ilike(like), VM.primary_ip.ilike(like), VM.hostname.ilike(like),
                        VM.guest_os.ilike(like), VM.owner.ilike(like), VM.folder.ilike(like)))
    if power:
        q = q.where(VM.power_state == power)
    if os_family:
        q = q.where(VM.os_family == os_family)
    if owner:
        q = q.where(VM.owner == owner)
    if owner_unknown:
        q = q.where(VM.owner.is_(None))
    return q


@router.get("")
def list_vms(
    search: str | None = None, power: str | None = None, os_family: str | None = None,
    owner: str | None = None, owner_unknown: bool = False,
    agent: str | None = Query(None, pattern="^(none|online|offline)$"),
    critical: bool = False, health: str | None = Query(None, pattern="^(problem|warning|critical)$"),
    status: str = Query("active", pattern="^(active|deleted|all)$"),
    sort: str = "name", order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db), _: User = Depends(require_viewer),
):
    q = _filtered(search, power, os_family, owner_unknown, status, owner, agent, critical, health)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    col = SORTABLE.get(sort, VM.name)
    col = col.desc() if order == "desc" else col.asc()
    rows = list(db.scalars(q.order_by(col.nulls_last(), VM.id).offset((page - 1) * page_size).limit(page_size)))
    states = agent_states(db, [v.id for v in rows])
    vc_ips = vcenter_ips(db)
    return {"items": [vm_summary(v, states.get(v.id), vc_ips) for v in rows], "total": total, "page": page,
            "page_size": page_size}


@router.get("/export.csv")
def export_csv(
    request: Request,
    search: str | None = None, power: str | None = None, os_family: str | None = None,
    owner: str | None = None, owner_unknown: bool = False,
    agent: str | None = Query(None, pattern="^(none|online|offline)$"),
    critical: bool = False, health: str | None = Query(None, pattern="^(problem|warning|critical)$"),
    status: str = Query("active", pattern="^(active|deleted|all)$"),
    db: Session = Depends(get_db), user: User = Depends(require_viewer),
):
    q = _filtered(search, power, os_family, owner_unknown, status, owner, agent, critical, health)
    rows = list(db.scalars(q.order_by(VM.name)))
    states = agent_states(db, [v.id for v in rows])
    cols = ["name", "critical", "health", "problems", "power_state", "agent", "os_family", "guest_os", "primary_ip", "last_known_ip", "hostname", "owner",
            "owner_source", "num_cpu", "memory_mb", "storage_committed_gb", "cpu_pct", "mem_pct",
            "disk_used_pct_max", "tools_status", "esxi_host", "cluster", "folder", "vcenter_created_at",
            "first_seen_at", "is_deleted", "deleted_at"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for vm in rows:
        row = vm_summary(vm, states.get(vm.id))
        row["problems"] = "; ".join(p["message"] for p in row["problems"])
        # Guard against spreadsheet formula injection from VM names/notes set by other users.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in row.items()})
    audit(db, request, user.username, "export_vms", status=status)
    db.commit()
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=vcentral-vms.csv"})


def _get_vm(db: Session, vm_id: int) -> VM:
    vm = db.get(VM, vm_id)
    if vm is None:
        raise HTTPException(404, "VM not found")
    return vm


@router.get("/{vm_id}")
def get_vm(vm_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    return vm_detail(db, _get_vm(db, vm_id))


class VMUpdate(BaseModel):
    owner: str | None = Field(None, max_length=255)  # "" clears a manual owner
    notes: str | None = Field(None, max_length=5000)
    critical: bool | None = None  # critical VMs must always be up: any problem is raised as critical


@router.patch("/{vm_id}")
def update_vm(vm_id: int, body: VMUpdate, request: Request,
              db: Session = Depends(get_db), user: User = Depends(require_operator)):
    vm = _get_vm(db, vm_id)
    changes = {}
    if body.owner is not None:
        new_owner = body.owner.strip()
        if new_owner:
            changes["owner"] = {"old": vm.owner, "new": new_owner}
            vm.owner, vm.owner_source, vm.owner_checked = new_owner, "manual", True
        elif vm.owner_source == "manual":
            changes["owner"] = {"old": vm.owner, "new": None}
            # Back to automatic detection on the next sync.
            vm.owner, vm.owner_source, vm.owner_checked = None, None, False
        if "owner" in changes:
            db.add(VMHistory(vm_id=vm.id, ts=utcnow(), kind="owner_changed",
                             message=f"Owner set to {changes['owner']['new'] or 'automatic'} by {user.username}",
                             data=changes["owner"]))
    if body.notes is not None:
        changes["notes"] = True
        vm.notes = body.notes.strip() or None
    if body.critical is not None and body.critical != vm.critical:
        changes["critical"] = body.critical
        vm.critical = body.critical
        db.add(VMHistory(vm_id=vm.id, ts=utcnow(), kind="critical_changed",
                         message=f"{'Marked critical' if body.critical else 'No longer critical'} by {user.username}",
                         data={"critical": body.critical}))
    if changes:
        audit(db, request, user.username, "update_vm", target=vm.name, **changes)
        db.commit()
        if "critical" in changes:
            evaluate_health(db, [vm.id])  # show the new severity right away
    return vm_detail(db, vm)


@router.post("/{vm_id}/wake")
def wake_vm(vm_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    """Wake a guest from idle sleep by pressing F15 on its keyboard through vCenter (no network or login)."""
    vm = _get_vm(db, vm_id)
    if vm.is_deleted or vm.power_state != "poweredOn":
        raise HTTPException(400, "The VM is not powered on")
    client = VCenterClient(get_settings())
    try:
        client.wake(vm.uuid)
    except vim.fault.NoPermission:
        raise HTTPException(403, "vCentral's vCenter account needs the privilege Virtual machine > Interaction > "
                                 "Inject USB HID scan codes")
    except vmodl.fault.MethodNotFound:
        raise HTTPException(501, "This vCenter / ESXi version cannot send key presses to VMs (needs vSphere 6.5 or later). "
                                 "Open the VM console and press a key instead.")
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"vCenter could not send the key press: {getattr(exc, 'msg', None) or exc}")
    finally:
        client.disconnect()
    db.add(VMHistory(vm_id=vm.id, ts=utcnow(), kind="wake", message=f"Wake-up key press sent by {user.username}"))
    audit(db, request, user.username, "vm_wake", target=vm.name)
    db.commit()
    return {"ok": True, "message": "Key press sent. If the VM was asleep, VMware Tools and its IP come back within about a minute."}


class ToolsFixIn(BaseModel):
    vm_ids: list[int] = Field(..., min_length=1, max_length=500)


@router.post("/tools/install")
def install_tools(body: ToolsFixIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    """Install, start or upgrade VMware Tools on these VMs, as far as vCenter and their agents allow (see toolsinstall.py)."""
    vms = list(db.scalars(select(VM).where(VM.id.in_(body.vm_ids))))
    if not vms:
        raise HTTPException(404, "VM not found")
    try:
        signing.private_key()
        signing_ok = True
    except signing.SigningUnavailable:
        signing_ok = False
    client = VCenterClient(get_settings())
    try:
        out = fix_tools(db, client, vms, user.username, signing_ok)
    finally:
        client.disconnect()
    counts = {}
    for r in out["results"]:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    audit(db, request, user.username, "tools_install", target=vms[0].name if len(vms) == 1 else f"{len(vms)} VMs",
          task_id=out["task_id"], **counts)
    db.commit()
    return out


@router.get("/{vm_id}/metrics")
def vm_metrics(vm_id: int, range: str = Query("24h", pattern="^(1h|6h|24h|7d|30d|90d)$"),
               db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    _get_vm(db, vm_id)
    now = utcnow()
    since = now - RANGES[range]
    if range in ("1h", "6h", "24h"):
        rows = db.scalars(select(VMMetric).where(VMMetric.vm_id == vm_id, VMMetric.ts >= since)
                          .order_by(VMMetric.ts))
        points = downsample(raw_points(rows, ("cpu_pct", "mem_pct", "disk_used_pct_max")), since, now)
        return {"range": range, "resolution": "raw", "points": points}
    rows = db.scalars(select(VMMetricHourly).where(VMMetricHourly.vm_id == vm_id, VMMetricHourly.ts >= since)
                      .order_by(VMMetricHourly.ts))
    points = [{"ts": iso(r.ts), "cpu_pct": r.cpu_pct_avg, "cpu_pct_max": r.cpu_pct_max, "mem_pct": r.mem_pct_avg,
               "mem_pct_max": r.mem_pct_max, "disk_used_pct_max": r.disk_used_pct_max} for r in rows]
    return {"range": range, "resolution": "hourly", "points": points}


@router.get("/{vm_id}/history")
def vm_history(vm_id: int, limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    _get_vm(db, vm_id)
    rows = db.scalars(select(VMHistory).where(VMHistory.vm_id == vm_id)
                      .order_by(VMHistory.ts.desc(), VMHistory.id.desc()).limit(limit))
    return [{"id": h.id, "ts": iso(h.ts), "kind": h.kind, "message": h.message, "data": h.data} for h in rows]
