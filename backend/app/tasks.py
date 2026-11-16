"""Tasks: run scripts (or agent updates) on many VMs through their agents.

A task becomes one TaskRun per target VM. Runs are handed to agents with their next report, each one signed
(see signing.py) so the agent can check it really comes from this vCentral and is meant for it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .agents import is_online, manifest, representative_agents
from .config import get_settings
from .db import iso, utcnow
from .models import VM, Agent, Schedule, Script, Task, TaskRun
from .scriptvars import VariableError, render
from .signing import sign

ACTIVE = ("pending", "sent", "running")
FINISHED = ("succeeded", "failed", "timeout", "cancelled", "skipped", "expired", "lost")
OUTPUT_LIMIT = 1_000_000  # characters kept per run
MAX_TIMEOUT = 6 * 3600


class TaskError(Exception):
    pass


# -- targets ---------------------------------------------------------------------------------------

def resolve_targets(db: Session, targets: dict) -> list[tuple[VM, Agent | None]]:
    """targets = {"vm_ids": [...]}  or  {"filter": {"os_family", "critical", "search", "only_online"}}"""
    agents = representative_agents(db)
    q = select(VM).where(VM.is_deleted.is_(False))
    if "vm_ids" in targets:
        ids = [int(i) for i in targets["vm_ids"]][:5000]
        if not ids:
            return []
        rows = list(db.scalars(q.where(VM.id.in_(ids)).order_by(VM.name)))
        return [(vm, agents.get(vm.id)) for vm in rows]
    f = targets.get("filter") or {}
    if f.get("os_family") in ("linux", "windows"):
        q = q.where(VM.os_family == f["os_family"])
    if f.get("critical"):
        q = q.where(VM.critical.is_(True))
    if f.get("search"):
        like = f"%{str(f['search']).strip()}%"
        q = q.where(or_(VM.name.ilike(like), VM.folder.ilike(like)))
    out = []
    for vm in db.scalars(q.order_by(VM.name)):
        agent = agents.get(vm.id)
        if agent is None or (f.get("only_online") and not is_online(agent)):
            continue  # filters only pick VMs that have an agent
        out.append((vm, agent))
    return out


def _script_for(task_linux: str | None, task_windows: str | None, family: str | None) -> tuple[str | None, str | None]:
    if family == "linux":
        return ("bash", task_linux) if task_linux and task_linux.strip() else (None, None)
    if family == "windows":
        return ("powershell", task_windows) if task_windows and task_windows.strip() else (None, None)
    return None, None


def _family(vm: VM, agent: Agent | None) -> str | None:
    return (agent.os_family if agent and agent.os_family in ("linux", "windows") else None) or vm.os_family


def preview(db: Session, targets: dict, linux_script: str | None, windows_script: str | None) -> dict:
    counts = {"total": 0, "will_run": 0, "linux": 0, "windows": 0, "offline": 0, "no_agent": 0, "no_script": 0}
    names = []
    for vm, agent in resolve_targets(db, targets):
        counts["total"] += 1
        if agent is None:
            counts["no_agent"] += 1
            continue
        family = _family(vm, agent)
        if _script_for(linux_script, windows_script, family)[0] is None:
            counts["no_script"] += 1
            continue
        counts["will_run"] += 1
        counts[family] += 1
        counts["offline"] += 0 if is_online(agent) else 1
        if len(names) < 50:
            names.append(vm.name)
    counts["names"] = names
    return counts


# -- creating tasks ----------------------------------------------------------------------------------

def create_task(db: Session, *, name: str, created_by: str, targets: dict, linux_script: str | None = None,
                windows_script: str | None = None, timeout: int = 300, script_id: int | None = None,
                schedule_id: int | None = None) -> Task:
    if not (linux_script or "").strip() and not (windows_script or "").strip():
        raise TaskError("Enter a Linux and/or a Windows script")
    timeout = max(10, min(int(timeout), MAX_TIMEOUT))
    resolved = resolve_targets(db, targets)  # check before writing anything
    if not resolved:
        raise TaskError("No VMs match these targets")
    now = utcnow()
    task = Task(name=name.strip()[:255] or "Task", kind="script", linux_script=linux_script or None,
                windows_script=windows_script or None, timeout_seconds=timeout, script_id=script_id,
                schedule_id=schedule_id, targets=targets, created_by=created_by, created_at=now,
                expires_at=now + timedelta(minutes=get_settings().task_expiry_minutes))
    db.add(task)
    db.flush()
    for vm, agent in resolved:
        run = TaskRun(task_id=task.id, vm_id=vm.id, agent_id=agent.id if agent else None, vm_name=vm.name,
                      os_family=_family(vm, agent), status="pending", output="", created_at=now, updated_at=now)
        if agent is None:
            run.status, run.error, run.finished_at = "skipped", "No vCentral agent on this VM", now
        elif _script_for(task.linux_script, task.windows_script, run.os_family)[0] is None:
            label = {"linux": "Linux", "windows": "Windows"}.get(run.os_family, "this OS")
            run.status, run.error, run.finished_at = "skipped", f"No {label} script in this task", now
        db.add(run)
    db.flush()
    return task


def create_update_task(db: Session, *, created_by: str, agent_ids: list[int] | None) -> Task:
    """Update agents (0.4.0 and later) to the version this server carries."""
    m = manifest()
    latest = m.get("version")
    if not latest:
        raise TaskError("No agent builds on this server")
    now = utcnow()
    q = select(Agent).where(Agent.status == "active")
    if agent_ids is not None:
        q = q.where(Agent.id.in_(agent_ids))
    # "update all" (agent_ids None) means "all outdated"
    agents = [a for a in db.scalars(q) if agent_ids is not None or a.agent_version != latest]
    if not agents:
        raise TaskError("All agents are already up to date")
    task = Task(name=f"Update agents to {latest}", kind="update", timeout_seconds=600, targets={"agent_ids": agent_ids},
                created_by=created_by, created_at=now,
                expires_at=now + timedelta(minutes=get_settings().task_expiry_minutes))
    db.add(task)
    db.flush()
    for a in agents:
        run = TaskRun(task_id=task.id, vm_id=a.vm_id, agent_id=a.id, vm_name=a.hostname or a.agent_uuid,
                      os_family=a.os_family, status="pending", output="", created_at=now, updated_at=now)
        if a.agent_version == latest:
            run.status, run.error, run.finished_at = "skipped", f"Already on {latest}", now
        elif not a.platform or a.platform not in (m.get("files") or {}):
            run.status, run.finished_at = "skipped", now
            run.error = "Installed before vCentral 0.4.0, which added self-update, so it cannot update itself. Reinstall it once (VM page → Install agent); later updates are automatic."
        db.add(run)
    db.flush()
    return task


# -- agent side ----------------------------------------------------------------------------------------

def _payload(run: TaskRun, task: Task, agent: Agent, now: datetime) -> dict:
    base = {"v": 1, "run_id": run.id, "agent_id": agent.agent_uuid, "kind": task.kind,
            "issued_at": iso(now), "expires_at": iso(task.expires_at), "timeout": task.timeout_seconds}
    if task.kind == "update":
        info = manifest()["files"][agent.platform]
        return {**base, "version": manifest()["version"], "path": f"/api/agent/download/{agent.platform}",
                "sha256": info["sha256"], "size": info.get("size")}
    shell, script = _script_for(task.linux_script, task.windows_script, run.os_family)
    return {**base, "shell": shell, "script": script}


def deliver(db: Session, agent: Agent, limit: int = 5) -> list[dict]:
    """Signed runs for this agent (marked "sent"). Called from the agent's report."""
    now = utcnow()
    rows = db.execute(select(TaskRun, Task).join(Task, Task.id == TaskRun.task_id).where(
        TaskRun.agent_id == agent.id, TaskRun.status == "pending", Task.expires_at > now)
        .order_by(TaskRun.id).limit(limit)).all()
    out = []
    for run, task in rows:
        if task.kind == "update" and (agent.platform not in (manifest().get("files") or {})):
            run.status, run.error, run.finished_at = "skipped", "No build for this agent's platform", now
            continue
        out.append(sign(_payload(run, task, agent, now)))
        run.status, run.sent_at, run.updated_at = "sent", now, now
    return out


def cancellations(db: Session, agent: Agent) -> list[int]:
    return list(db.scalars(select(TaskRun.id).where(TaskRun.agent_id == agent.id, TaskRun.cancel_requested.is_(True),
                                                    TaskRun.status.in_(("sent", "running")))))


def _append(run: TaskRun, text: str) -> None:
    if not text or run.output_truncated:
        return
    room = OUTPUT_LIMIT - len(run.output or "")
    if len(text) > room:
        text, run.output_truncated = text[:room], True
    run.output = (run.output or "") + text


def record_progress(run: TaskRun, chunk: str) -> None:
    now = utcnow()
    if run.status in ("sent", "pending"):
        run.status, run.started_at = "running", run.started_at or now
    _append(run, chunk)
    run.updated_at = now


def record_result(run: TaskRun, *, exit_code: int | None, chunk: str, error: str | None, timed_out: bool,
                  cancelled: bool) -> None:
    now = utcnow()
    _append(run, chunk)
    run.exit_code = exit_code
    run.error = error[:2000] if error else None
    if cancelled:
        run.status = "cancelled"
    elif timed_out:
        run.status = "timeout"
    elif error and exit_code is None:
        run.status = "failed"
    else:
        run.status = "succeeded" if exit_code == 0 else "failed"
    run.started_at = run.started_at or run.sent_at or now
    run.finished_at = run.updated_at = now


def expire_runs(db: Session) -> int:
    """Housekeeping: runs never picked up expire; runs that went silent are marked lost."""
    now = utcnow()
    changed = 0
    for run, task in db.execute(select(TaskRun, Task).join(Task, Task.id == TaskRun.task_id)
                                .where(TaskRun.status.in_(ACTIVE))).all():
        if run.status == "pending" and task.expires_at <= now:
            run.status, run.error = "expired", "The agent did not pick up the task in time (offline?)"
        elif run.status in ("sent", "running") and run.updated_at < now - timedelta(seconds=task.timeout_seconds + 600):
            run.status, run.error = "lost", "No result from the agent (restarted or lost connection?)"
        else:
            continue
        run.finished_at = run.updated_at = now
        changed += 1
    db.commit()
    return changed


def cancel_task(db: Session, task: Task) -> int:
    now = utcnow()
    n = 0
    for run in db.scalars(select(TaskRun).where(TaskRun.task_id == task.id, TaskRun.status.in_(ACTIVE))):
        if run.status == "pending":
            run.status, run.error, run.finished_at = "cancelled", "Cancelled before it started", now
        else:
            run.cancel_requested = True  # the agent stops it on its next report
        run.updated_at = now
        n += 1
    return n


def summarize(runs: list[TaskRun]) -> dict:
    counts = {s: 0 for s in ACTIVE + FINISHED}
    for r in runs:
        counts[r.status] = counts.get(r.status, 0) + 1
    active = sum(counts[s] for s in ACTIVE)
    if active:
        state = "running"
    elif counts["failed"] or counts["timeout"] or counts["lost"] or counts["expired"]:
        state = "failed" if not counts["succeeded"] else "partial"
    elif counts["succeeded"]:
        state = "succeeded"
    else:
        state = "cancelled" if counts["cancelled"] else "skipped"
    return {"state": state, "total": len(runs), "counts": counts}


# -- schedules -----------------------------------------------------------------------------------------

def next_fire(cron: str, after: datetime) -> datetime:
    trigger = CronTrigger.from_crontab(cron, timezone="UTC")
    nxt = trigger.get_next_fire_time(None, after.replace(tzinfo=timezone.utc))
    return nxt.astimezone(timezone.utc).replace(tzinfo=None)


def validate_cron(cron: str) -> str:
    cron = " ".join(cron.split())
    try:
        CronTrigger.from_crontab(cron, timezone="UTC")
    except ValueError as exc:
        raise TaskError(f"Invalid schedule '{cron}': {exc}")
    if len(cron.split()) != 5:
        raise TaskError("Use 5 fields: minute hour day-of-month month day-of-week")
    return cron


def run_due_schedules(db: Session) -> int:
    now = utcnow()
    started = 0
    for sch in db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)):
        script = db.get(Script, sch.script_id)
        sch.last_run_at, sch.next_run_at = now, next_fire(sch.cron, now)
        if script is None:
            continue
        try:
            linux, windows = render(script.linux_script, script.windows_script, sch.variables)
            task = create_task(db, name=f"{sch.name} (scheduled)", created_by=f"schedule:{sch.created_by}",
                               targets=sch.targets, linux_script=linux,
                               windows_script=windows, timeout=script.timeout_seconds,
                               script_id=script.id, schedule_id=sch.id)
            sch.last_task_id = task.id
            started += 1
        except (TaskError, VariableError):
            pass  # e.g. no VM matches right now, or the script gained a variable the schedule has no value for
    db.commit()
    return started
