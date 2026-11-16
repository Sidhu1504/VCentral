"""Agent management for the UI: list/inspect agents, link to VMs, revoke, enrollment tokens."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..agents import (PLATFORMS, agent_state, certificate_fingerprint, latest_agent_version, link_agent, manifest,
                      new_secret, refresh_managed, sha256_hex)
from ..audit import audit
from ..config import get_settings
from ..db import get_db, iso, utcnow
from ..models import VM, Agent, AgentMetric, AgentMetricHourly, EnrollmentToken, User
from ..security import require_admin, require_operator, require_viewer
from .series import downsample, raw_points

router = APIRouter(prefix="/api", tags=["agents"])

RANGES = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24),
          "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}


def agent_summary(a: Agent, vm_name: str | None = None) -> dict:
    latest = latest_agent_version()
    return {
        "id": a.id, "agent_id": a.agent_uuid, "state": agent_state(a), "hostname": a.hostname,
        "os_family": a.os_family, "os_name": a.os_name, "arch": a.arch, "agent_version": a.agent_version,
        "platform": a.platform, "can_self_update": bool(a.platform),
        "outdated": bool(latest and a.agent_version and a.agent_version != latest),
        "vm_id": a.vm_id, "vm_name": vm_name, "link_source": a.link_source, "ips": a.ips,
        "last_seen_at": iso(a.last_seen_at), "last_ip": a.last_ip, "enrolled_at": iso(a.enrolled_at),
        "enrolled_with": a.enrolled_with, "revoked_reason": a.revoked_reason, "revoked_at": iso(a.revoked_at),
    }


def agent_detail(a: Agent, vm_name: str | None = None) -> dict:
    inv = a.inventory or {}
    return {
        **agent_summary(a, vm_name),
        "bios_uuid": a.bios_uuid, "serial_number": a.serial_number, "macs": a.macs,
        "kernel_version": a.kernel_version, "os_version": a.os_version, "enrolled_from_ip": a.enrolled_from_ip,
        "metrics": a.metrics, "top_processes": a.top_processes,
        "inventory_at": iso(a.inventory_at), "inventory_requested": a.inventory_requested,
        "system": {k: inv.get(k) for k in ("cpu_model", "cpu_cores", "mem_total_mb", "boot_time", "timezone")},
        "counts": {k: len(inv.get(k) or []) for k in ("software", "services", "listening_ports")},
        "inventory_errors": inv.get("errors") or [],
    }


def _get(db: Session, agent_id: int) -> Agent:
    a = db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(404, "Agent not found")
    return a


def _vm_names(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.execute(select(VM.id, VM.name).where(VM.id.in_(ids))).all())


@router.get("/agents")
def list_agents(state: str | None = Query(None, pattern="^(online|offline|revoked|active)$"),
                linked: bool | None = None, search: str | None = None,
                db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    q = select(Agent)
    if state == "revoked":
        q = q.where(Agent.status == "revoked")
    elif state:
        q = q.where(Agent.status == "active")
    if linked is not None:
        q = q.where(Agent.vm_id.is_not(None) if linked else Agent.vm_id.is_(None))
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Agent.hostname.ilike(like), Agent.os_name.ilike(like), Agent.last_ip.ilike(like)))
    agents = list(db.scalars(q.order_by(Agent.hostname, Agent.id)))
    if state in ("online", "offline"):
        agents = [a for a in agents if agent_state(a) == state]
    names = _vm_names(db, [a.vm_id for a in agents])
    return [agent_summary(a, names.get(a.vm_id)) for a in agents]


@router.get("/agents/install-info")
def install_info(_: User = Depends(require_operator)):
    m = manifest()
    return {
        "fingerprint": certificate_fingerprint(),
        "version": m.get("version"),
        "platforms": sorted(p for p in PLATFORMS if p in (m.get("files") or {})),
        "files": m.get("files") or {},
    }


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    a = _get(db, agent_id)
    return agent_detail(a, _vm_names(db, [a.vm_id]).get(a.vm_id))


@router.get("/agents/{agent_id}/inventory/{section}")
def get_inventory(agent_id: int, section: str, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    if section not in ("software", "services", "listening_ports", "interfaces"):
        raise HTTPException(404, "Unknown inventory section")
    return (_get(db, agent_id).inventory or {}).get(section) or []


@router.get("/agents/{agent_id}/metrics")
def agent_metrics(agent_id: int, range: str = Query("24h", pattern="^(1h|6h|24h|7d|30d|90d)$"),
                  db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    _get(db, agent_id)
    now = utcnow()
    since = now - RANGES[range]
    cols = ("cpu_pct", "mem_pct", "disk_used_pct_max", "disk_read_bps", "disk_write_bps", "net_rx_bps", "net_tx_bps")
    if range in ("1h", "6h", "24h"):
        rows = db.scalars(select(AgentMetric).where(AgentMetric.agent_id == agent_id, AgentMetric.ts >= since)
                          .order_by(AgentMetric.ts))
        return {"range": range, "resolution": "raw", "points": downsample(raw_points(rows, cols), since, now)}
    rows = db.scalars(select(AgentMetricHourly).where(AgentMetricHourly.agent_id == agent_id,
                                                      AgentMetricHourly.ts >= since).order_by(AgentMetricHourly.ts))
    return {"range": range, "resolution": "hourly", "points": [{
        "ts": iso(r.ts), "cpu_pct": r.cpu_pct_avg, "mem_pct": r.mem_pct_avg, "disk_used_pct_max": r.disk_used_pct_max,
        "disk_read_bps": r.disk_read_bps, "disk_write_bps": r.disk_write_bps,
        "net_rx_bps": r.net_rx_bps, "net_tx_bps": r.net_tx_bps} for r in rows]}


@router.post("/agents/{agent_id}/refresh-inventory")
def refresh_inventory(agent_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_operator)):
    a = _get(db, agent_id)
    a.inventory_requested = True
    audit(db, request, user.username, "agent_refresh_inventory", target=a.hostname)
    db.commit()
    return {"ok": True}


@router.post("/agents/{agent_id}/revoke")
def revoke_agent(agent_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_operator)):
    a = _get(db, agent_id)
    if a.status != "revoked":
        a.status, a.revoked_reason, a.revoked_at = "revoked", f"revoked by {user.username}", utcnow()
        refresh_managed(db, [a.vm_id])
        audit(db, request, user.username, "agent_revoke", target=a.hostname, agent_id=a.agent_uuid)
        db.commit()
    return agent_detail(a, _vm_names(db, [a.vm_id]).get(a.vm_id))


class LinkIn(BaseModel):
    vm_id: int | None = None  # None = unlink and stop automatic linking; use "auto" endpoint to undo


@router.post("/agents/{agent_id}/link")
def link(agent_id: int, body: LinkIn, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_operator)):
    a = _get(db, agent_id)
    old = a.vm_id
    if body.vm_id is not None:
        vm = db.get(VM, body.vm_id)
        if vm is None or vm.is_deleted:
            raise HTTPException(404, "VM not found")
    a.vm_id, a.link_source = body.vm_id, "manual"
    refresh_managed(db, [old, a.vm_id])
    audit(db, request, user.username, "agent_link", target=a.hostname, vm_id=body.vm_id)
    db.commit()
    return agent_detail(a, _vm_names(db, [a.vm_id]).get(a.vm_id))


@router.post("/agents/{agent_id}/link/auto")
def link_auto(agent_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    a = _get(db, agent_id)
    a.link_source = None
    link_agent(db, a)
    audit(db, request, user.username, "agent_link", target=a.hostname, vm_id=a.vm_id, mode="auto")
    db.commit()
    return agent_detail(a, _vm_names(db, [a.vm_id]).get(a.vm_id))


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = _get(db, agent_id)
    if a.status != "revoked":
        raise HTTPException(400, "Revoke the agent before deleting it")
    db.execute(delete(AgentMetric).where(AgentMetric.agent_id == a.id))
    db.execute(delete(AgentMetricHourly).where(AgentMetricHourly.agent_id == a.id))
    audit(db, request, user.username, "agent_delete", target=a.hostname, agent_id=a.agent_uuid)
    db.delete(a)
    db.commit()


# -- enrollment tokens -----------------------------------------------------------------------------

def token_out(t: EnrollmentToken) -> dict:
    now = utcnow()
    if t.revoked:
        state = "revoked"
    elif t.expires_at and t.expires_at <= now:
        state = "expired"
    elif t.max_uses is not None and t.uses >= t.max_uses:
        state = "used up"
    else:
        state = "active"
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "created_by": t.created_by,
            "created_at": iso(t.created_at), "expires_at": iso(t.expires_at), "max_uses": t.max_uses,
            "uses": t.uses, "last_used_at": iso(t.last_used_at), "state": state}


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_in_hours: int | None = Field(24 * 7, ge=1, le=24 * 365)  # None = never
    max_uses: int | None = Field(None, ge=1, le=100000)  # None = unlimited


@router.get("/agent-tokens")
def list_tokens(db: Session = Depends(get_db), _: User = Depends(require_operator)):
    return [token_out(t) for t in db.scalars(select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()))]


@router.post("/agent-tokens", status_code=201)
def create_token(body: TokenIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    token = new_secret("vct_")
    t = EnrollmentToken(name=body.name.strip(), token_hash=sha256_hex(token), prefix=token[:10],
                        created_by=user.username, max_uses=body.max_uses,
                        expires_at=utcnow() + timedelta(hours=body.expires_in_hours) if body.expires_in_hours else None)
    db.add(t)
    audit(db, request, user.username, "agent_token_create", target=t.name, max_uses=body.max_uses,
          expires_in_hours=body.expires_in_hours)
    db.commit()
    return {**token_out(t), "token": token}  # shown once


@router.post("/agent-tokens/{token_id}/revoke")
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    t = db.get(EnrollmentToken, token_id)
    if t is None:
        raise HTTPException(404, "Token not found")
    t.revoked = True
    audit(db, request, user.username, "agent_token_revoke", target=t.name)
    db.commit()
    return token_out(t)
