from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, iso, utcnow
from ..agents import is_online
from ..health import SEVERITY_RANK
from ..models import VM, Agent, User, VMHistory
from ..security import require_viewer
from ..state import get_state
from .network import subnet_usage

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DISK_WARN_PCT = 85


def agent_counts(db: Session) -> dict:
    agents = list(db.scalars(select(Agent).where(Agent.status == "active")))
    online = sum(1 for a in agents if is_online(a))
    return {"agents_online": online, "agents_offline": len(agents) - online,
            "agents_unlinked": sum(1 for a in agents if a.vm_id is None)}


def vm_changes(db: Session, since) -> list[dict]:
    """VMs created, deleted or re-added recently, newest first, with who created them."""
    rows = db.execute(select(VMHistory, VM).join(VM, VM.id == VMHistory.vm_id)
                      .where(VMHistory.ts >= since, VMHistory.kind.in_(("created", "deleted", "reappeared")))
                      .order_by(VMHistory.ts.desc(), VMHistory.id.desc()).limit(50)).all()
    return [{"ts": iso(h.ts), "kind": h.kind, "vm_id": vm.id, "vm_name": vm.name, "owner": vm.owner,
             "owner_source": vm.owner_source, "os_family": vm.os_family, "guest_os": vm.guest_os,
             "deleted": vm.is_deleted, "has_agent": vm.managed} for h, vm in rows]


def _brief(vm: VM, value_key: str) -> dict:
    return {"id": vm.id, "name": vm.name, "value": getattr(vm, value_key), "owner": vm.owner}


@router.get("")
def dashboard(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    vms = list(db.scalars(select(VM).where(VM.is_deleted.is_(False))))
    on = [v for v in vms if v.power_state == "poweredOn"]
    # Includes deleted critical VMs: their disappearance is itself a problem.
    attention = sorted(db.scalars(select(VM).where(VM.health != "ok")),
                       key=lambda v: (SEVERITY_RANK.get(v.health, 0), v.critical), reverse=True)
    power = Counter(v.power_state for v in vms)
    now = utcnow()

    since_30d = now - timedelta(days=30)
    lifecycle = {}
    for h in db.scalars(select(VMHistory).where(VMHistory.ts >= since_30d,
                                                VMHistory.kind.in_(("created", "deleted")))):
        day = h.ts.date().isoformat()
        lifecycle.setdefault(day, {"created": 0, "deleted": 0})[h.kind] += 1
    days = [(now - timedelta(days=i)).date().isoformat() for i in range(29, -1, -1)]
    lifecycle_30d = [{"date": d, **lifecycle.get(d, {"created": 0, "deleted": 0})} for d in days]

    since_7d = (now - timedelta(days=7)).date().isoformat()
    recent = db.scalars(select(VMHistory).order_by(VMHistory.ts.desc(), VMHistory.id.desc()).limit(12))
    names = {v.id: v.name for v in db.scalars(select(VM))}

    full_disks = sorted((v for v in on if (v.disk_used_pct_max or 0) >= DISK_WARN_PCT),
                        key=lambda v: v.disk_used_pct_max, reverse=True)

    return {
        "totals": {
            "total": len(vms),
            "powered_on": power.get("poweredOn", 0),
            "powered_off": power.get("poweredOff", 0),
            "suspended": power.get("suspended", 0),
            "owner_unknown": sum(1 for v in vms if not v.owner),
            "managed": sum(1 for v in vms if v.managed),
            "critical_vms": sum(1 for v in vms if v.critical),
            "health_critical": sum(1 for v in attention if v.health == "critical"),
            "health_warning": sum(1 for v in attention if v.health == "warning"),
            "no_ip": sum(1 for v in attention if any(p["code"] == "no_ip" for p in v.problems or [])),
            **agent_counts(db),
            "tools_not_running": sum(1 for v in on if v.tools_status != "guestToolsRunning"),
            "created_7d": sum(d["created"] for d in lifecycle_30d if d["date"] >= since_7d),
            "deleted_7d": sum(d["deleted"] for d in lifecycle_30d if d["date"] >= since_7d),
        },
        "allocated": {
            "vcpu_on": sum(v.num_cpu or 0 for v in on),
            "memory_gb_on": round(sum(v.memory_mb or 0 for v in on) / 1024, 1),
            "storage_gb": round(sum(v.storage_committed_gb or 0 for v in vms), 1),
        },
        "os_family": dict(Counter(v.os_family for v in vms)),
        "guest_os_top": Counter(v.guest_os or "Unknown" for v in vms).most_common(10),
        "top_cpu": [_brief(v, "cpu_pct") for v in sorted(on, key=lambda v: v.cpu_pct or 0, reverse=True)[:5]],
        "top_mem": [_brief(v, "mem_pct") for v in sorted(on, key=lambda v: v.mem_pct or 0, reverse=True)[:5]],
        "disk_warnings": {"threshold": DISK_WARN_PCT, "count": len(full_disks),
                          "items": [_brief(v, "disk_used_pct_max") for v in full_disks[:5]]},
        "attention": [{"id": v.id, "name": v.name, "critical": v.critical, "health": v.health,
                       "power_state": v.power_state, "problems": v.problems or []} for v in attention[:50]],
        "lifecycle_30d": lifecycle_30d,
        "vm_changes_7d": vm_changes(db, now - timedelta(days=7)),
        "recent_activity": [{"id": h.id, "ts": iso(h.ts), "kind": h.kind, "message": h.message,
                             "vm_id": h.vm_id, "vm_name": names.get(h.vm_id)} for h in recent],
        "subnets": [{k: s[k] for k in ("cidr", "usable", "used", "free", "conflicts", "used_pct")}
                    for s in subnet_usage(vms, get_settings().subnet_list)],
        "sync": get_state(db, "sync"),
    }
