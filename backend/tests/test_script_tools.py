"""Script variables, starter scripts, library import, and task results as CSV."""
import csv
import io

from app.db import SessionLocal, utcnow
from app.models import AuditLog, Schedule, TaskRun
from app.scriptvars import find, quote_powershell, render
from app.starter_scripts import STARTER_SCRIPTS
from app.tasks import run_due_schedules

from .test_api import H
from .test_tasks import client, report, verify, vm_id  # noqa: F401 - fixture

LIB = {"name": "Restart svc", "linux_script": "systemctl restart -- {{SERVICE}}",
       "windows_script": "Restart-Service -Name {{ service }} -Force", "timeout_seconds": 60}


def test_render_and_find():
    assert find("a {{SERVICE}} b {{ Port }}", "{{service}}") == ["SERVICE", "PORT"]
    linux, windows = render("echo {{V}}", "Write-Output {{V}}", {"v": "it's $(id)"})
    assert linux == "echo 'it'\\''s $(id)'"
    assert windows == "Write-Output 'it''s $(id)'"
    assert quote_powershell("‘x’") == "'‘‘x’’'"  # PowerShell's other single quotes are escaped too
    assert render("no vars", None, None) == ("no vars", None)


def test_variables_in_tasks(client):
    s = client.post("/api/scripts", headers=H, json=LIB).json()
    assert s["variables"] == ["SERVICE"]
    body = {"script_id": s["id"], "targets": {"vm_ids": [vm_id("web01"), vm_id("win01")]}}
    r = client.post("/api/tasks", headers=H, json=body)
    assert r.status_code == 400 and "SERVICE" in r.json()["detail"]
    r = client.post("/api/tasks", headers=H, json={**body, "variables": {"SERVICE": "nginx; reboot"}})
    assert r.status_code == 201
    task = r.json()
    assert task["linux_script"] == "systemctl restart -- 'nginx; reboot'"  # one literal argument, never a command
    assert task["windows_script"] == "Restart-Service -Name 'nginx; reboot' -Force"
    payload = verify(report(client, "web01"))[0]
    assert payload["script"] == "systemctl restart -- 'nginx; reboot'"  # what the agent receives (signed)
    with SessionLocal() as db:
        entry = db.query(AuditLog).filter_by(action="task_create").order_by(AuditLog.id.desc()).first()
        assert entry.detail["variables"] == {"SERVICE": "nginx; reboot"}
    # Re-runs reuse the filled-in script, with no need to type the values again.
    with SessionLocal() as db:
        for run in db.query(TaskRun).filter_by(task_id=task["id"]):
            run.status = "failed"
        db.commit()
    rerun = client.post(f"/api/tasks/{task['id']}/rerun", headers=H, json={"only_failed": True})
    assert rerun.status_code == 201
    assert client.get(f"/api/tasks/{rerun.json()['id']}").json()["linux_script"] == "systemctl restart -- 'nginx; reboot'"


def test_schedule_variables(client):
    s = client.post("/api/scripts", headers=H, json=LIB).json()
    body = {"name": "nightly", "cron": "0 2 * * *", "script_id": s["id"], "targets": {"vm_ids": [vm_id("web01")]}}
    assert client.post("/api/schedules", headers=H, json=body).status_code == 400
    sch = client.post("/api/schedules", headers=H, json={**body, "variables": {"service": "nginx", "extra": "x"}}).json()
    assert sch["variables"] == {"SERVICE": "nginx"}  # only the script's own variables are kept
    task_id = client.post(f"/api/schedules/{sch['id']}/run", headers=H).json()["id"]
    assert client.get(f"/api/tasks/{task_id}").json()["linux_script"] == "systemctl restart -- 'nginx'"
    with SessionLocal() as db:
        db.get(Schedule, sch["id"]).next_run_at = utcnow()
        db.commit()
        assert run_due_schedules(db) == 1


def test_starter_and_import(client):
    r = client.post("/api/scripts/starter", headers=H).json()
    assert len(r["added"]) == len(STARTER_SCRIPTS) and not r["skipped"]
    assert client.post("/api/scripts/starter", headers=H).json()["added"] == []  # idempotent
    names = {s["name"]: s for s in client.get("/api/scripts").json()}
    assert names["Restart a service"]["variables"] == ["SERVICE"]

    export = [{k: s[k] for k in ("name", "description", "linux_script", "windows_script", "timeout_seconds")}
              for s in names.values()]
    export[0]["description"] = "changed"
    r = client.post("/api/scripts/import", headers=H, json={"scripts": export}).json()
    assert r["added"] == [] and len(r["skipped"]) == len(export)
    r = client.post("/api/scripts/import", headers=H, json={"scripts": export, "replace": True}).json()
    assert len(r["updated"]) == len(export)
    assert {s["name"]: s for s in client.get("/api/scripts").json()}[export[0]["name"]]["description"] == "changed"
    r = client.post("/api/scripts/import", headers=H, json={"scripts": [{"name": "new one", "linux_script": "uptime"}]})
    assert r.json()["added"] == ["new one"]


def test_results_csv(client):
    task = client.post("/api/tasks", headers=H, json={"name": "check", "targets": {"vm_ids": [vm_id("web01"), vm_id("db01")]},
                                                      "linux_script": "uname -a"}).json()
    with SessionLocal() as db:
        run = db.query(TaskRun).filter_by(task_id=task["id"], vm_name="web01").one()
        run.status, run.exit_code, run.output = "succeeded", 0, "=HYPERLINK(\"http://evil\")\nline 2"
        run.started_at = run.finished_at = utcnow()
        db.commit()
    r = client.get(f"/api/tasks/{task['id']}/results.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = {row["vm"]: row for row in csv.DictReader(io.StringIO(r.text.lstrip("﻿")))}
    assert rows["web01"]["status"] == "succeeded" and rows["web01"]["output"].startswith("'=HYPERLINK")  # not a formula
    assert rows["db01"]["status"] == "skipped"
