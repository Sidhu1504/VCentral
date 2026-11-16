"""Endpoints called by agents (not by browsers). Authentication is a per-agent bearer secret."""
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents import PLATFORMS, latest_agent_version, link_agent, manifest, new_secret, refresh_managed, sha256_hex
from ..audit import audit
from ..config import get_settings
from ..db import get_db, utcnow
from .. import signing
from ..models import Agent, AgentMetric, EnrollmentToken, TaskRun
from ..runtime import runtime
from ..tasks import cancellations, deliver, record_progress, record_result
from ..security import LoginThrottle, client_ip

router = APIRouter(prefix="/api/agent", tags=["agent protocol"])
enroll_throttle = LoginThrottle(max_failures=10, window=900, lockout=900)

OUTPUT_CHUNK = 1_100_000  # per request; the run keeps at most tasks.OUTPUT_LIMIT characters
SCRIPTS =Path(__file__).resolve().parent.parent / "agent_scripts"

Str = Annotated[str, Field(max_length=255)]
LongStr = Annotated[str, Field(max_length=2000)]


class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")  # newer agents may send more fields


class FactsIn(Loose):
    hostname: Str = ""
    os_family: Str = ""
    os_name: Str = ""
    os_version: Str = ""
    kernel_version: Str = ""
    arch: Str = ""
    bios_uuid: Str = ""
    serial_number: Str = ""
    macs: list[Str] | None = Field(None, max_length=64)
    ips: list[Str] | None = Field(None, max_length=256)
    agent_version: Str = ""


class EnrollIn(Loose):
    token: str = Field(max_length=200)
    facts: FactsIn


class DiskIn(Loose):
    mount: LongStr
    fstype: Str = ""
    total_gb: float = 0
    used_gb: float = 0
    used_pct: float = 0


class MetricsIn(Loose):
    cpu_pct: float | None = None
    mem_pct: float | None = None
    mem_total_mb: int | None = None
    mem_used_mb: int | None = None
    swap_pct: float | None = None
    load1: float | None = None
    load5: float | None = None
    load15: float | None = None
    uptime_seconds: int | None = None
    process_count: int | None = None
    disks: list[DiskIn] | None = Field(None, max_length=200)
    disk_read_bps: float | None = None
    disk_write_bps: float | None = None
    net_rx_bps: float | None = None
    net_tx_bps: float | None = None


class ProcIn(Loose):
    pid: int
    name: Str = ""
    user: Str = ""
    cpu_pct: float = 0
    mem_mb: float = 0


class TopIn(Loose):
    by_cpu: list[ProcIn] | None = Field(None, max_length=20)
    by_mem: list[ProcIn] | None = Field(None, max_length=20)


class InventoryIn(Loose):
    facts: FactsIn
    cpu_model: Str = ""
    cpu_cores: int = 0
    mem_total_mb: int = 0
    boot_time: Str | None = None
    timezone: Str = ""
    interfaces: list[dict] | None = Field(None, max_length=64)
    software: list[dict] | None = Field(None, max_length=20000)
    services: list[dict] | None = Field(None, max_length=5000)
    listening_ports: list[dict] | None = Field(None, max_length=5000)
    errors: list[LongStr] | None = Field(None, max_length=20)


class ReportIn(Loose):
    agent_version: Str = ""
    platform: Str = ""
    bios_uuid: Str = ""
    ips: list[Str] | None = Field(None, max_length=256)
    metrics: MetricsIn
    top_processes: TopIn | None = None
    inventory: InventoryIn | None = None


def _apply_facts(agent: Agent, f: FactsIn) -> None:
    agent.hostname = f.hostname or agent.hostname
    agent.os_family = f.os_family or agent.os_family
    agent.os_name = f.os_name or agent.os_name
    agent.os_version = f.os_version or agent.os_version
    agent.kernel_version = f.kernel_version or agent.kernel_version
    agent.arch = f.arch or agent.arch
    agent.serial_number = f.serial_number or agent.serial_number
    if f.bios_uuid and not agent.bios_uuid:
        agent.bios_uuid = f.bios_uuid.lower()
    if f.macs is not None:
        agent.macs = sorted({m.lower() for m in f.macs if m})
    if f.ips is not None:
        agent.ips = f.ips


@router.post("/enroll")
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if enroll_throttle.retry_after("enroll", ip):
        raise HTTPException(429, "Too many failed enrollment attempts from this address; try again later")
    now = utcnow()
    token = db.scalar(select(EnrollmentToken).where(EnrollmentToken.token_hash == sha256_hex(body.token.strip())))
    valid = (token is not None and not token.revoked
             and (token.expires_at is None or token.expires_at > now)
             and (token.max_uses is None or token.uses < token.max_uses))
    if not valid:
        enroll_throttle.failed("enroll", ip)
        audit(db, request, None, "agent_enroll", target=body.facts.hostname or None, success=False,
              reason="invalid, expired or used-up token")
        db.commit()
        raise HTTPException(401, "Invalid, expired or used-up enrollment token")

    secret = new_secret("vca_")
    agent = Agent(agent_uuid=str(uuid.uuid4()), secret_hash=sha256_hex(secret), status="active",
                  enrolled_at=now, enrolled_with=token.name, enrolled_from_ip=ip, last_seen_at=None,
                  macs=[], ips=[], metrics={}, top_processes={}, inventory={}, inventory_requested=True,
                  agent_version=body.facts.agent_version or None)
    _apply_facts(agent, body.facts)
    db.add(agent)
    db.flush()

    # A reinstall on the same machine replaces the previous agent record.
    if agent.bios_uuid:
        for old in db.scalars(select(Agent).where(Agent.bios_uuid == agent.bios_uuid, Agent.status == "active",
                                                  Agent.id != agent.id)):
            old.status, old.revoked_reason, old.revoked_at = "revoked", "replaced by reinstall", now
            refresh_managed(db, [old.vm_id])

    token.uses += 1
    token.last_used_at = now
    link_agent(db, agent)
    enroll_throttle.succeeded("enroll", ip)
    audit(db, request, None, "agent_enroll", target=agent.hostname, token=token.name, agent_id=agent.agent_uuid)
    db.commit()
    return {
        "agent_id": agent.agent_uuid,
        "secret": secret,
        "reusable_token": token.max_uses is None or token.max_uses > 1,
        "report_interval": runtime()["agent_report_interval_seconds"],
        "task_public_key": signing.public_key_b64(),
    }


class TokenCheckIn(Loose):
    token: str = Field(max_length=200)


@router.post("/token-check")
def token_check(body: TokenCheckIn, request: Request, db: Session = Depends(get_db)):
    """Used by `install --template`: is this token valid and reusable? Does not use it up."""
    ip = client_ip(request)
    if enroll_throttle.retry_after("enroll", ip):
        raise HTTPException(429, "Too many failed attempts from this address; try again later")
    now = utcnow()
    t = db.scalar(select(EnrollmentToken).where(EnrollmentToken.token_hash == sha256_hex(body.token.strip())))
    reason = None
    if t is None:
        reason = "unknown token"
    elif t.revoked:
        reason = "token was revoked"
    elif t.expires_at is not None and t.expires_at <= now:
        reason = "token has expired"
    elif t.max_uses is not None and t.uses >= t.max_uses:
        reason = "token is used up"
    if reason:
        enroll_throttle.failed("enroll", ip)
        return {"valid": False, "reusable": False, "reason": reason}
    return {"valid": True, "reusable": t.max_uses is None or t.max_uses - t.uses > 1, "reason": None,
            "expires_at": t.expires_at.isoformat() + "Z" if t.expires_at else None}


def current_agent(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> Agent:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing agent credentials")
    agent = db.scalar(select(Agent).where(Agent.secret_hash == sha256_hex(authorization[7:].strip())))
    if agent is None or agent.status != "active":
        raise HTTPException(401, "Unknown or revoked agent")
    return agent


@router.post("/report")
def report(body: ReportIn, request: Request, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
    reported = body.bios_uuid.lower()
    if agent.bios_uuid and reported and reported != agent.bios_uuid:
        # The agent's credentials were copied to another machine (cloned VM).
        raise HTTPException(409, "Machine identity does not match this agent; re-enrollment required")

    now = utcnow()
    rt = runtime()
    m = body.metrics
    if body.ips is not None:  # sent with every report since agent 0.3.0: lets vCentral spot a lost IP quickly
        agent.ips = body.ips
    agent.last_seen_at, agent.last_ip = now, client_ip(request)
    agent.agent_version = body.agent_version or agent.agent_version
    agent.platform = body.platform or agent.platform
    agent.metrics = {**m.model_dump(), "ts": now.isoformat(timespec="seconds") + "Z"}
    agent.top_processes = body.top_processes.model_dump() if body.top_processes else {}
    disk_max = max((d.used_pct for d in m.disks or []), default=None)
    db.add(AgentMetric(agent_id=agent.id, ts=now, cpu_pct=m.cpu_pct, mem_pct=m.mem_pct, swap_pct=m.swap_pct,
                       disk_used_pct_max=disk_max, disk_read_bps=m.disk_read_bps, disk_write_bps=m.disk_write_bps,
                       net_rx_bps=m.net_rx_bps, net_tx_bps=m.net_tx_bps, load1=m.load1))
    if body.inventory is not None:
        inv = body.inventory
        _apply_facts(agent, inv.facts)
        agent.inventory = inv.model_dump(exclude={"facts"})
        agent.inventory_at = now
        agent.inventory_requested = False
        link_agent(db, agent)
    tasks, cancel = [], []
    if signing.available():
        tasks = deliver(db, agent)
        cancel = cancellations(db, agent)
    db.commit()
    return {
        "report_interval": rt["agent_report_interval_seconds"],
        "inventory_interval": rt["agent_inventory_interval_seconds"],
        "send_inventory": agent.inventory_requested,
        "latest_version": latest_agent_version(),
        "task_public_key": signing.public_key_b64(),
        "tasks": tasks,
        "cancel": cancel,
    }


class ProgressIn(Loose):
    output: str = Field("", max_length=OUTPUT_CHUNK)


class ResultIn(Loose):
    exit_code: int | None = None
    output: str = Field("", max_length=OUTPUT_CHUNK)
    error: str | None = Field(None, max_length=4000)
    timed_out: bool = False
    cancelled: bool = False


def _run_for(db: Session, run_id: int, agent: Agent) -> TaskRun:
    run = db.get(TaskRun, run_id)
    if run is None or run.agent_id != agent.id:
        raise HTTPException(404, "Unknown task")
    return run


@router.post("/tasks/{run_id}/progress")
def task_progress(run_id: int, body: ProgressIn, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
    run = _run_for(db, run_id, agent)
    if run.status in ("pending", "sent", "running"):
        record_progress(run, body.output)
        agent.last_seen_at = utcnow()
        db.commit()
    return {"cancel": run.cancel_requested}


@router.post("/tasks/{run_id}/result")
def task_result(run_id: int, body: ResultIn, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
    run = _run_for(db, run_id, agent)
    if run.status in ("pending", "sent", "running"):
        record_result(run, exit_code=body.exit_code, chunk=body.output, error=body.error, timed_out=body.timed_out,
                      cancelled=body.cancelled)
        db.commit()
    return {"ok": True}


# -- installers and binaries (public: they contain no secrets) ----------------------------------------

@router.get("/install.sh", response_class=PlainTextResponse)
def install_sh():
    return PlainTextResponse((SCRIPTS / "install.sh").read_text(), media_type="text/x-shellscript")


@router.get("/install.ps1", response_class=PlainTextResponse)
def install_ps1():
    return PlainTextResponse((SCRIPTS / "install.ps1").read_text(), media_type="text/plain")


@router.get("/manifest")
def agent_manifest():
    return manifest()


@router.get("/download/{platform}")
def download(platform: str):
    name = PLATFORMS.get(platform)
    agent_dir = get_settings().agent_dir
    if name is None or not agent_dir:
        raise HTTPException(404, "Unknown platform or agent binaries not available on this server")
    path = Path(agent_dir) / name
    if not path.is_file():
        raise HTTPException(404, f"Agent binary for {platform} is not available on this server")
    return FileResponse(path, media_type="application/octet-stream", filename=name)
