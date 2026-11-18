"""Onboarding for the admin team: install the agent (through VMware Tools, or a one-time command),
the queue of VMs without an agent, and the digest sent to the team."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..health import tools_state
from ..agents import certificate_fingerprint, new_secret, sha256_hex
from ..audit import audit
from ..config import get_settings
from ..db import get_db, iso, utcnow
from ..guestops import PushError, platform_for, start_push
from ..models import VM, EnrollmentToken, NotificationLog, PushJob, User
from ..notify import chat_configured, email_configured
from ..onboarding import get_policy, is_unmanaged, run_digest
from ..security import require_operator, require_viewer
from ..state import get_state

router = APIRouter(prefix="/api", tags=["onboarding"])


class PushIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


def job_out(j: PushJob) -> dict:
    return {"id": j.id, "vm_id": j.vm_id, "status": j.status, "requested_by": j.requested_by, "guest_user": j.guest_user,
            "steps": j.steps, "error": j.error, "output": j.output, "created_at": iso(j.created_at),
            "finished_at": iso(j.finished_at)}


def server_url(request: Request) -> str:
    """The address agents should use: VCENTRAL_PUBLIC_URL, else the address this browser used."""
    configured = get_settings().public_url.rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


def _vm(db: Session, vm_id: int) -> VM:
    vm = db.get(VM, vm_id)
    if vm is None or vm.is_deleted:
        raise HTTPException(404, "VM not found")
    return vm


@router.post("/vms/{vm_id}/push")
def push(vm_id: int, body: PushIn, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_operator)):
    """Install the agent through VMware Tools. The guest password is used in memory only and never stored."""
    vm = _vm(db, vm_id)
    if vm.power_state != "poweredOn":
        raise HTTPException(400, "The VM must be powered on")
    if vm.tools_status != "guestToolsRunning":
        raise HTTPException(400, "VMware Tools is not running in this VM; use the install command instead")
    try:
        platform_for(vm)
    except PushError as exc:
        raise HTTPException(400, str(exc))
    if db.scalar(select(PushJob).where(PushJob.vm_id == vm.id, PushJob.status.in_(("queued", "running")))):
        raise HTTPException(409, "An installation is already running for this VM")
    job = PushJob(vm_id=vm.id, requested_by=user.username, guest_user=body.username.strip(), status="queued", steps=[])
    db.add(job)
    audit(db, request, user.username, "agent_push", target=vm.name, guest_user=body.username.strip())
    db.commit()
    start_push(job.id, body.username.strip(), body.password, server_url(request))
    return job_out(job)


@router.post("/vms/{vm_id}/install-command")
def install_command(vm_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    vm = _vm(db, vm_id)
    token = new_secret("vct_")
    db.add(EnrollmentToken(name=f"{vm.name} (by {user.username})"[:128], token_hash=sha256_hex(token), prefix=token[:10],
                           created_by=user.username, max_uses=1, expires_at=utcnow() + timedelta(hours=24)))
    audit(db, request, user.username, "agent_install_command", target=vm.name)
    db.commit()
    try:
        platform = platform_for(vm)
    except PushError:
        platform = None
    return {"token": token, "fingerprint": certificate_fingerprint(), "server": server_url(request),
            "platform": platform, "os_family": vm.os_family, "expires_in_hours": 24}


@router.get("/vms/{vm_id}/push-jobs")
def vm_push_jobs(vm_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    return [job_out(j) for j in db.scalars(select(PushJob).where(PushJob.vm_id == vm_id)
                                           .order_by(PushJob.id.desc()).limit(10))]


@router.get("/push-jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    job = db.get(PushJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job_out(job)


@router.get("/onboarding")
def onboarding(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    now = utcnow()
    vms = list(db.scalars(select(VM).where(VM.is_deleted.is_(False))))
    on = [v for v in vms if v.power_state == "poweredOn"]
    queue = sorted((v for v in vms if is_unmanaged(v)), key=lambda v: v.unmanaged_since or now)
    policy = get_policy(db)
    overdue = timedelta(days=policy["overdue_after_days"])
    jobs = {j.vm_id: j for j in db.scalars(select(PushJob).order_by(PushJob.id))}  # latest per VM
    s = get_settings()
    return {
        "stats": {
            "powered_on": len(on), "managed_on": sum(1 for v in on if v.managed), "unmanaged": len(queue),
            "overdue": sum(1 for v in queue if v.unmanaged_since and now - v.unmanaged_since >= overdue),
            "no_owner": sum(1 for v in queue if not v.owner),
            "tools_running": sum(1 for v in queue if v.tools_status == "guestToolsRunning"),
        },
        "policy": policy,
        "channels": {"email": email_configured() and bool(s.admin_email_list), "chat": chat_configured()},
        "last_digest": get_state(db, "onboarding_digest").get("sent_at"),
        "items": [{
            "id": v.id, "name": v.name, "os_family": v.os_family, "guest_os": v.guest_os, "primary_ip": v.primary_ip,
            "tools_status": v.tools_status, "tools_version_status": v.tools_version_status, "tools": tools_state(v), "power_state": v.power_state, "owner": v.owner, "critical": v.critical,
            "unmanaged_since": iso(v.unmanaged_since),
            "overdue": bool(v.unmanaged_since and now - v.unmanaged_since >= overdue),
            "last_push": job_out(jobs[v.id]) if v.id in jobs else None,
        } for v in queue],
    }


@router.post("/onboarding/digest")
def send_digest_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    result = run_digest(db, force=True)
    audit(db, request, user.username, "send_digest", **result)
    db.commit()
    return result


@router.get("/notifications")
def notifications(db: Session = Depends(get_db), _: User = Depends(require_operator)):
    rows = db.scalars(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(200))
    return [{"id": n.id, "ts": iso(n.ts), "channel": n.channel, "recipient": n.recipient, "kind": n.kind,
             "subject": n.subject, "vm_count": len(n.vm_ids or []), "success": n.success, "error": n.error} for n in rows]
