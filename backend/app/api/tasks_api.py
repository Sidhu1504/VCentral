"""Tasks, script library, schedules and agent updates.

Roles: admins write ad-hoc scripts and edit the library and schedules; operators run library scripts,
cancel and see output; viewers see task status only (output can contain sensitive data).
"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import signing
from ..audit import audit
from ..db import get_db, iso, utcnow
from ..models import Schedule, Script, Task, TaskRun, User
from ..security import require_admin, require_operator, require_viewer
from ..tasks import (FINISHED, TaskError, cancel_task, create_task, create_update_task, next_fire, preview,
                     summarize, validate_cron)

router = APIRouter(prefix="/api", tags=["tasks"])

ScriptText = Field(None, max_length=200_000)


def _need_signing() -> None:
    try:
        signing.private_key()
    except signing.SigningUnavailable as exc:
        raise HTTPException(503, str(exc))


def _digest(*parts: str | None) -> str:
    return hashlib.sha256("\x00".join(p or "" for p in parts).encode()).hexdigest()[:16]


class Targets(BaseModel):
    vm_ids: list[int] | None = Field(None, max_length=5000)
    os_family: str | None = Field(None, pattern="^(linux|windows)$")
    critical: bool = False
    search: str | None = Field(None, max_length=255)
    only_online: bool = False

    def as_dict(self) -> dict:
        if self.vm_ids is not None:
            return {"vm_ids": self.vm_ids}
        return {"filter": {"os_family": self.os_family, "critical": self.critical, "search": self.search,
                           "only_online": self.only_online}}


class TaskIn(BaseModel):
    name: str = Field("", max_length=255)
    targets: Targets
    script_id: int | None = None  # run a library script (operators may do this)
    linux_script: str | None = ScriptText  # ad-hoc scripts: admins only
    windows_script: str | None = ScriptText
    timeout_seconds: int = Field(300, ge=10, le=6 * 3600)


def _resolve_content(db: Session, body: TaskIn, user: User) -> tuple[str, str | None, str | None, int, Script | None]:
    if body.script_id is not None:
        script = db.get(Script, body.script_id)
        if script is None:
            raise HTTPException(404, "Script not found")
        return body.name or script.name, script.linux_script, script.windows_script, script.timeout_seconds, script
    if user.role != "admin":
        raise HTTPException(403, "Only admins can run their own scripts; operators can run scripts from the library")
    return body.name or "Ad-hoc script", body.linux_script, body.windows_script, body.timeout_seconds, None


def run_out(r: TaskRun, with_output: bool = False) -> dict:
    d = {"id": r.id, "task_id": r.task_id, "vm_id": r.vm_id, "vm_name": r.vm_name, "os_family": r.os_family,
         "status": r.status, "exit_code": r.exit_code, "error": r.error, "cancel_requested": r.cancel_requested,
         "created_at": iso(r.created_at), "sent_at": iso(r.sent_at), "started_at": iso(r.started_at),
         "finished_at": iso(r.finished_at), "output_size": len(r.output or ""), "output_truncated": r.output_truncated}
    if with_output:
        d["output"] = r.output or ""
    return d


def task_out(t: Task, runs: list[TaskRun], with_scripts: bool) -> dict:
    d = {"id": t.id, "name": t.name, "kind": t.kind, "created_by": t.created_by, "created_at": iso(t.created_at),
         "expires_at": iso(t.expires_at), "timeout_seconds": t.timeout_seconds, "script_id": t.script_id,
         "schedule_id": t.schedule_id, "targets": t.targets, **summarize(runs)}
    if with_scripts:
        d["linux_script"], d["windows_script"] = t.linux_script, t.windows_script
    return d


@router.get("/tasks/status")
def tasks_status(_: User = Depends(require_viewer)):
    try:
        signing.private_key()
        return {"enabled": True, "reason": None}
    except signing.SigningUnavailable as exc:
        return {"enabled": False, "reason": str(exc)}


@router.get("/tasks")
def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
               db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    total = db.scalar(select(func.count()).select_from(Task))
    tasks = list(db.scalars(select(Task).order_by(Task.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    runs: dict[int, list[TaskRun]] = {t.id: [] for t in tasks}
    if tasks:
        for r in db.scalars(select(TaskRun).where(TaskRun.task_id.in_(list(runs)))):
            runs[r.task_id].append(r)
    return {"items": [task_out(t, runs[t.id], False) for t in tasks], "total": total}


@router.post("/tasks/preview")
def preview_task(body: TaskIn, db: Session = Depends(get_db), _: User = Depends(require_operator)):
    """How many VMs a task would run on (no permission check on content: nothing is run)."""
    linux, windows = body.linux_script, body.windows_script
    if body.script_id is not None:
        script = db.get(Script, body.script_id)
        if script is None:
            raise HTTPException(404, "Script not found")
        linux, windows = script.linux_script, script.windows_script
    return preview(db, body.targets.as_dict(), linux, windows)


@router.post("/tasks", status_code=201)
def new_task(body: TaskIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    _need_signing()
    name, linux, windows, timeout, script = _resolve_content(db, body, user)
    try:
        task = create_task(db, name=name, created_by=user.username, targets=body.targets.as_dict(),
                           linux_script=linux, windows_script=windows, timeout=timeout,
                           script_id=script.id if script else None)
    except TaskError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id == task.id)))
    audit(db, request, user.username, "task_create", target=task.name, task_id=task.id,
          script=script.name if script else "ad-hoc", script_digest=_digest(linux, windows),
          vms=len(runs), will_run=sum(1 for r in runs if r.status == "pending"))
    db.commit()
    return task_out(task, runs, True)


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_viewer)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id == task.id).order_by(TaskRun.vm_name)))
    return {**task_out(task, runs, user.role in ("admin", "operator")), "runs": [run_out(r) for r in runs]}


@router.get("/task-runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)):
    run = db.get(TaskRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run_out(run, with_output=True)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    n = cancel_task(db, task)
    audit(db, request, user.username, "task_cancel", target=task.name, task_id=task.id, runs=n)
    db.commit()
    return {"cancelled": n}


class RerunIn(BaseModel):
    only_failed: bool = True


@router.post("/tasks/{task_id}/rerun", status_code=201)
def rerun(task_id: int, body: RerunIn, request: Request, db: Session = Depends(get_db),
          user: User = Depends(require_operator)):
    _need_signing()
    task = db.get(Task, task_id)
    if task is None or task.kind != "script":
        raise HTTPException(404, "Task not found")
    if task.script_id is None and user.role != "admin":
        raise HTTPException(403, "Only admins can re-run ad-hoc scripts")
    runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id == task.id)))
    wanted = {"failed", "timeout", "lost", "expired", "cancelled"} if body.only_failed else set(FINISHED) | {"succeeded"}
    vm_ids = [r.vm_id for r in runs if r.vm_id and r.status in wanted]
    if not vm_ids:
        raise HTTPException(400, "Nothing to re-run")
    try:
        new = create_task(db, name=f"{task.name} (re-run)", created_by=user.username, targets={"vm_ids": vm_ids},
                          linux_script=task.linux_script, windows_script=task.windows_script,
                          timeout=task.timeout_seconds, script_id=task.script_id)
    except TaskError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, request, user.username, "task_rerun", target=new.name, task_id=new.id, from_task=task.id, vms=len(vm_ids))
    db.commit()
    return {"id": new.id}


@router.get("/vms/{vm_id}/task-runs")
def vm_runs(vm_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    rows = db.execute(select(TaskRun, Task.name).join(Task, Task.id == TaskRun.task_id)
                      .where(TaskRun.vm_id == vm_id).order_by(TaskRun.id.desc()).limit(20)).all()
    return [{**run_out(r), "task_name": name} for r, name in rows]


# -- agent updates --------------------------------------------------------------------------------------

class UpdateIn(BaseModel):
    agent_ids: list[int] | None = None  # None = every outdated agent


@router.post("/agents/update", status_code=201)
def update_agents(body: UpdateIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    _need_signing()
    try:
        task = create_update_task(db, created_by=user.username, agent_ids=body.agent_ids)
    except TaskError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id == task.id)))
    audit(db, request, user.username, "agent_update", target=task.name, task_id=task.id, agents=len(runs))
    db.commit()
    return task_out(task, runs, False)


# -- script library ----------------------------------------------------------------------------------

class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)
    linux_script: str | None = ScriptText
    windows_script: str | None = ScriptText
    timeout_seconds: int = Field(300, ge=10, le=6 * 3600)


def script_out(s: Script) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description, "linux_script": s.linux_script,
            "windows_script": s.windows_script, "timeout_seconds": s.timeout_seconds, "created_by": s.created_by,
            "created_at": iso(s.created_at), "updated_by": s.updated_by, "updated_at": iso(s.updated_at)}


@router.get("/scripts")
def list_scripts(db: Session = Depends(get_db), _: User = Depends(require_operator)):
    return [script_out(s) for s in db.scalars(select(Script).order_by(Script.name))]


def _check_script(db: Session, body: ScriptIn, script_id: int | None = None) -> None:
    if not (body.linux_script or "").strip() and not (body.windows_script or "").strip():
        raise HTTPException(400, "Enter a Linux and/or a Windows script")
    clash = db.scalar(select(Script).where(func.lower(Script.name) == body.name.strip().lower()))
    if clash and clash.id != script_id:
        raise HTTPException(409, "A script with this name already exists")


@router.post("/scripts", status_code=201)
def create_script(body: ScriptIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    _check_script(db, body)
    s = Script(name=body.name.strip(), description=body.description, linux_script=body.linux_script or None,
               windows_script=body.windows_script or None, timeout_seconds=body.timeout_seconds,
               created_by=user.username, updated_by=user.username)
    db.add(s)
    audit(db, request, user.username, "script_create", target=s.name, digest=_digest(s.linux_script, s.windows_script))
    db.commit()
    return script_out(s)


@router.put("/scripts/{script_id}")
def update_script(script_id: int, body: ScriptIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    s = db.get(Script, script_id)
    if s is None:
        raise HTTPException(404, "Script not found")
    _check_script(db, body, script_id)
    s.name, s.description = body.name.strip(), body.description
    s.linux_script, s.windows_script = body.linux_script or None, body.windows_script or None
    s.timeout_seconds, s.updated_by, s.updated_at = body.timeout_seconds, user.username, utcnow()
    audit(db, request, user.username, "script_update", target=s.name, digest=_digest(s.linux_script, s.windows_script))
    db.commit()
    return script_out(s)


@router.delete("/scripts/{script_id}", status_code=204)
def delete_script(script_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    s = db.get(Script, script_id)
    if s is None:
        raise HTTPException(404, "Script not found")
    audit(db, request, user.username, "script_delete", target=s.name)
    db.execute(delete(Schedule).where(Schedule.script_id == s.id))  # its schedules go with it
    db.execute(update(Task).where(Task.script_id == s.id).values(script_id=None))  # past tasks keep their copy
    db.delete(s)
    db.commit()


# -- schedules ---------------------------------------------------------------------------------------

class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cron: str = Field(min_length=9, max_length=64)
    script_id: int
    targets: Targets
    enabled: bool = True


def schedule_out(s: Schedule, script_name: str | None) -> dict:
    return {"id": s.id, "name": s.name, "enabled": s.enabled, "cron": s.cron, "script_id": s.script_id,
            "script_name": script_name, "targets": s.targets, "created_by": s.created_by,
            "next_run_at": iso(s.next_run_at), "last_run_at": iso(s.last_run_at), "last_task_id": s.last_task_id}


@router.get("/schedules")
def list_schedules(db: Session = Depends(get_db), _: User = Depends(require_operator)):
    names = dict(db.execute(select(Script.id, Script.name)).all())
    return [schedule_out(s, names.get(s.script_id)) for s in db.scalars(select(Schedule).order_by(Schedule.name))]


def _apply_schedule(db: Session, s: Schedule, body: ScheduleIn) -> None:
    if db.get(Script, body.script_id) is None:
        raise HTTPException(404, "Script not found")
    try:
        s.cron = validate_cron(body.cron)
    except TaskError as exc:
        raise HTTPException(400, str(exc))
    s.name, s.script_id, s.targets, s.enabled = body.name.strip(), body.script_id, body.targets.as_dict(), body.enabled
    s.next_run_at = next_fire(s.cron, utcnow())


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    s = Schedule(created_by=user.username)
    _apply_schedule(db, s, body)
    db.add(s)
    audit(db, request, user.username, "schedule_create", target=s.name, cron=s.cron, script_id=s.script_id)
    db.commit()
    return schedule_out(s, db.get(Script, s.script_id).name)


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_admin)):
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(404, "Schedule not found")
    _apply_schedule(db, s, body)
    audit(db, request, user.username, "schedule_update", target=s.name, cron=s.cron, enabled=s.enabled)
    db.commit()
    return schedule_out(s, db.get(Script, s.script_id).name)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(404, "Schedule not found")
    audit(db, request, user.username, "schedule_delete", target=s.name)
    db.delete(s)
    db.commit()


@router.post("/schedules/{schedule_id}/run", status_code=201)
def run_schedule_now(schedule_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_operator)):
    _need_signing()
    s = db.get(Schedule, schedule_id)
    script = db.get(Script, s.script_id) if s else None
    if s is None or script is None:
        raise HTTPException(404, "Schedule not found")
    try:
        task = create_task(db, name=f"{s.name} (run now)", created_by=user.username, targets=s.targets,
                           linux_script=script.linux_script, windows_script=script.windows_script,
                           timeout=script.timeout_seconds, script_id=script.id, schedule_id=s.id)
    except TaskError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    s.last_run_at, s.last_task_id = utcnow(), task.id
    audit(db, request, user.username, "schedule_run", target=s.name, task_id=task.id)
    db.commit()
    return {"id": task.id}
