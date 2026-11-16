import base64
import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from app.agents import sha256_hex
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app, bootstrap_admin
from app.models import VM, Agent, Schedule, Task, TaskRun
from app.sync import run_sync
from app.tasks import expire_runs, run_due_schedules

from .test_api import H, ready_admin
from .test_sync import FakeClient, make_vm

SECRETS = {"web01": "vca_web", "win01": "vca_win"}


@pytest.fixture
def client():
    bootstrap_admin()
    fake = FakeClient()
    vms = [make_vm("a", "web01"), make_vm("b", "win01"), make_vm("c", "db01")]
    vms[1].update(os_family="windows", guest_os="Microsoft Windows Server 2019", guest_id="windows2019srv_64Guest")
    fake.vms = vms
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
        for name, family, platform, version in (("web01", "linux", "linux-amd64", "0.1.0"),
                                                ("win01", "windows", None, "0.3.0")):
            vm = db.query(VM).filter_by(name=name).one()
            db.add(Agent(agent_uuid=f"uuid-{name}", secret_hash=sha256_hex(SECRETS[name]), vm_id=vm.id, status="active",
                         hostname=name, os_family=family, platform=platform, agent_version=version, ips=["10.0.0.5"],
                         macs=[], metrics={}, top_processes={}, inventory={}, last_seen_at=utcnow()))
            vm.managed = True
        db.commit()
    with TestClient(app) as c:
        ready_admin(c)
        yield c


def report(c, name):
    return c.post("/api/agent/report", headers={"Authorization": f"Bearer {SECRETS[name]}"},
                  json={"metrics": {"cpu_pct": 1}, "agent_version": "0.4.0"}).json()


def verify(resp):
    """What the agent does: check every task against the public key it was given."""
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(resp["task_public_key"]))
    out = []
    for t in resp["tasks"]:
        key.verify(base64.b64decode(t["signature"]), t["payload"].encode())  # raises if tampered
        out.append(json.loads(t["payload"]))
    return out


def new_task(c, **kw):
    body = {"name": "check", "targets": {"vm_ids": [vm_id("web01"), vm_id("win01"), vm_id("db01")]},
            "linux_script": "uname -a", "windows_script": "Get-Date", **kw}
    return c.post("/api/tasks", headers=H, json=body)


def vm_id(name):
    with SessionLocal() as db:
        return db.query(VM).filter_by(name=name).one().id


def runs_by_vm(c, task_id):
    return {r["vm_name"]: r for r in c.get(f"/api/tasks/{task_id}").json()["runs"]}


def test_task_lifecycle_and_signatures(client):
    preview = client.post("/api/tasks/preview", headers=H, json={"targets": {"vm_ids": [vm_id("web01"), vm_id("win01"), vm_id("db01")]},
                                                                  "linux_script": "uname -a"}).json()
    assert (preview["will_run"], preview["no_agent"], preview["no_script"]) == (1, 1, 1)

    r = new_task(client)
    assert r.status_code == 201
    task = r.json()
    runs = runs_by_vm(client, task["id"])
    assert runs["db01"]["status"] == "skipped" and "No vCentral agent" in runs["db01"]["error"]
    assert runs["web01"]["status"] == runs["win01"]["status"] == "pending"

    linux = verify(report(client, "web01"))
    assert len(linux) == 1 and linux[0]["shell"] == "bash" and linux[0]["script"] == "uname -a"
    assert linux[0]["agent_id"] == "uuid-web01" and linux[0]["run_id"] == runs["web01"]["id"]
    assert verify(report(client, "web01")) == []  # never delivered twice
    (win,) = verify(report(client, "win01"))
    assert win["shell"] == "powershell" and win["script"] == "Get-Date"

    # A tampered payload fails verification.
    resp = report(client, "web01")
    assert resp["tasks"] == []
    with pytest.raises(Exception):
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(resp["task_public_key"]))
        key.verify(base64.b64decode("A" * 88), json.dumps({**linux[0], "script": "rm -rf /"}).encode())

    auth = {"Authorization": f"Bearer {SECRETS['web01']}"}
    run_id = linux[0]["run_id"]
    client.post(f"/api/agent/tasks/{run_id}/progress", headers=auth, json={"output": "Linux web01 "})
    assert runs_by_vm(client, task["id"])["web01"]["status"] == "running"
    client.post(f"/api/agent/tasks/{run_id}/result", headers=auth, json={"exit_code": 0, "output": "6.8.0\n"})
    assert client.get(f"/api/task-runs/{run_id}").json()["output"] == "Linux web01 6.8.0\n"
    # Another agent cannot report on this run.
    assert client.post(f"/api/agent/tasks/{run_id}/result", headers={"Authorization": f"Bearer {SECRETS['win01']}"},
                       json={"exit_code": 1}).status_code == 404

    client.post(f"/api/agent/tasks/{win['run_id']}/result", headers={"Authorization": f"Bearer {SECRETS['win01']}"},
                json={"exit_code": 3, "output": "boom"})
    t = client.get(f"/api/tasks/{task['id']}").json()
    assert t["state"] == "partial" and t["counts"]["succeeded"] == 1 and t["counts"]["failed"] == 1

    rerun = client.post(f"/api/tasks/{task['id']}/rerun", headers=H, json={"only_failed": True}).json()
    assert list(runs_by_vm(client, rerun["id"])) == ["win01"]


def test_cancel_expire_and_timeout(client):
    task = new_task(client).json()
    verify(report(client, "web01"))  # web01 run is now "sent"
    assert client.post(f"/api/tasks/{task['id']}/cancel", headers=H).json()["cancelled"] == 2
    runs = runs_by_vm(client, task["id"])
    assert runs["win01"]["status"] == "cancelled" and runs["web01"]["cancel_requested"] is True
    assert report(client, "web01")["cancel"] == [runs["web01"]["id"]]

    task2 = new_task(client).json()
    with SessionLocal() as db:
        db.get(Task, task2["id"]).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        expire_runs(db)
    assert runs_by_vm(client, task2["id"])["web01"]["status"] == "expired"

    task3 = new_task(client, timeout_seconds=30).json()
    (p,) = verify(report(client, "web01"))
    assert p["timeout"] == 30
    client.post(f"/api/agent/tasks/{p['run_id']}/result", headers={"Authorization": f"Bearer {SECRETS['web01']}"},
                json={"exit_code": None, "timed_out": True, "error": "timed out after 30s"})
    assert runs_by_vm(client, task3["id"])["web01"]["status"] == "timeout"


def test_roles(client):
    script = client.post("/api/scripts", headers=H, json={"name": "Disk usage", "linux_script": "df -h",
                                                           "windows_script": "Get-PSDrive"}).json()
    for role in ("operator", "viewer"):
        client.post("/api/users", headers=H, json={"username": role, "password": "SomePass1234", "role": role})
    with TestClient(app) as op:
        op.post("/api/auth/login", headers=H, json={"username": "operator", "password": "SomePass1234"})
        op.post("/api/auth/change-password", headers=H, json={"current_password": "SomePass1234", "new_password": "SomePass5678"})
        assert new_task(op).status_code == 403  # no ad-hoc scripts
        r = op.post("/api/tasks", headers=H, json={"targets": {"vm_ids": [vm_id("web01")]}, "script_id": script["id"]})
        assert r.status_code == 201 and r.json()["name"] == "Disk usage"
        assert op.post("/api/scripts", headers=H, json={"name": "x", "linux_script": "id"}).status_code == 403
        run_id = runs_by_vm(op, r.json()["id"])["web01"]["id"]
        assert op.get(f"/api/task-runs/{run_id}").status_code == 200
    with TestClient(app) as v:
        v.post("/api/auth/login", headers=H, json={"username": "viewer", "password": "SomePass1234"})
        v.post("/api/auth/change-password", headers=H, json={"current_password": "SomePass1234", "new_password": "SomePass5678"})
        assert v.get("/api/tasks").status_code == 200
        assert "linux_script" not in v.get(f"/api/tasks/{r.json()['id']}").json()
        assert v.get(f"/api/task-runs/{run_id}").status_code == 403  # output can hold secrets
        assert v.post("/api/tasks", headers=H, json={"targets": {"vm_ids": [1]}, "script_id": script["id"]}).status_code == 403


def test_filters_schedules_and_library(client):
    script = client.post("/api/scripts", headers=H, json={"name": "Uptime", "linux_script": "uptime"}).json()
    assert client.post("/api/scripts", headers=H, json={"name": "uptime", "linux_script": "x"}).status_code == 409
    preview = client.post("/api/tasks/preview", headers=H, json={"targets": {"os_family": "linux"}, "script_id": script["id"]}).json()
    assert preview["will_run"] == 1 and preview["names"] == ["web01"]  # db01 has no agent, so filters skip it

    assert client.post("/api/schedules", headers=H, json={"name": "bad", "cron": "every day", "script_id": script["id"],
                                                          "targets": {"os_family": "linux"}}).status_code == 400
    s = client.post("/api/schedules", headers=H, json={"name": "Nightly uptime", "cron": "0 2 * * *",
                                                       "script_id": script["id"], "targets": {"os_family": "linux"}}).json()
    assert s["next_run_at"].endswith("02:00:00Z")
    with SessionLocal() as db:
        db.get(Schedule, s["id"]).next_run_at = utcnow() - timedelta(minutes=1)
        db.commit()
        assert run_due_schedules(db) == 1
        sch = db.get(Schedule, s["id"])
        assert sch.last_task_id and sch.next_run_at > utcnow()
        assert db.get(Task, sch.last_task_id).created_by.startswith("schedule:")
    assert client.post(f"/api/schedules/{s['id']}/run", headers=H).status_code == 201

    assert client.delete(f"/api/scripts/{script['id']}", headers=H).status_code == 204
    assert client.get("/api/schedules").json() == []


def test_agent_update_tasks(client):
    r = client.post("/api/agents/update", headers=H, json={"agent_ids": None})
    assert r.status_code == 201
    runs = runs_by_vm(client, r.json()["id"])
    assert runs["web01"]["status"] == "pending"
    assert runs["win01"]["status"] == "skipped" and "Reinstall it once" in runs["win01"]["error"]  # no self-update support
    (p,) = verify(report(client, "web01"))
    assert p["kind"] == "update" and p["path"] == "/api/agent/download/linux-amd64" and p["sha256"] == "x"
    assert p["version"] == "0.2.0"


def test_tasks_disabled_without_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "task_signing_key_file", "")
    assert client.get("/api/tasks/status").json()["enabled"] is False
    r = new_task(client)
    assert r.status_code == 503 and "VCENTRAL_TASK_SIGNING_KEY_FILE" in r.json()["detail"]
    assert report(client, "web01")["tasks"] == []
