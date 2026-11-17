import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import app.guestops as guestops
import app.onboarding as onboarding
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.guestops import PushError, platform_for
from app.main import app, bootstrap_admin
from app.models import VM, Agent, EnrollmentToken, PushJob
from app.onboarding import run_digest, save_policy, update_unmanaged
from app.sync import run_sync

from .test_api import H, ready_admin
from .test_sync import FakeClient, make_vm


class FakeGuest:
    """Stands in for VMware Tools guest operations."""
    calls: list = []
    reject_login = False
    exit_code = 0

    def __init__(self, client, vm, username, password):
        FakeGuest.calls.append(("open", vm.name, username, password))

    def validate(self):
        if FakeGuest.reject_login:
            raise PushError("The VM rejected the username or password")

    def upload(self, path, data, mode=None):
        FakeGuest.calls.append(("upload", path, len(data), mode))

    def run(self, program, args, timeout=240):
        FakeGuest.calls.append(("run", program, args))
        return FakeGuest.exit_code

    output = b"vCentral agent 0.3.0 installed and running.\n"

    def download(self, path):
        return FakeGuest.output

    def make_temp_dir(self):
        FakeGuest.calls.append(("mkdtemp",))
        return "/tmp/vcentral-x1"

    def delete_dir(self, path):
        FakeGuest.calls.append(("rmdir", path))

    def delete(self, path):
        FakeGuest.calls.append(("delete", path))


@pytest.fixture
def client(monkeypatch):
    bootstrap_admin()
    fake = FakeClient()
    vms = [make_vm("a", "web01", ip="10.0.0.5"), make_vm("b", "db01", ip="10.0.0.6"), make_vm("c", "orphan01")]
    vms[0]["custom_attributes"] = {"Owner": "VSPHERE.LOCAL\\ravi"}
    vms[1]["custom_attributes"] = {"Owner": "VSPHERE.LOCAL\\ravi"}
    vms[0]["guest_id"] = vms[1]["guest_id"] = "rhel9_64Guest"
    fake.vms = vms
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
        update_unmanaged(db)
    FakeGuest.calls, FakeGuest.reject_login, FakeGuest.exit_code = [], False, 0
    FakeGuest.output = b"vCentral agent 0.3.0 installed and running.\n"
    monkeypatch.setattr(guestops, "SESSION_FACTORY", FakeGuest)
    monkeypatch.setattr(guestops, "ENROLL_WAIT_SECONDS", 0)
    with TestClient(app) as c:
        ready_admin(c)
        yield c


def wait_job(c, job_id):
    for _ in range(50):
        job = c.get(f"/api/push-jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("push job did not finish")


def vm_id(name):
    with SessionLocal() as db:
        return db.query(VM).filter_by(name=name).one().id


def test_platform_selection():
    def vm(family, gid):
        return VM(os_family=family, guest_id=gid)
    assert platform_for(vm("windows", "windows2019srv_64Guest")) == "windows-amd64"
    assert platform_for(vm("windows", "windows9Server64Guest")) == "windows-amd64"       # Server 2016
    assert platform_for(vm("windows", "windows8Server64Guest")) == "windows-legacy-amd64"  # 2012 / 2012 R2
    assert platform_for(vm("windows", "windows7Server64Guest")) == "windows-legacy-amd64"  # 2008 R2
    assert platform_for(vm("windows", "windows7Guest")) == "windows-legacy-386"
    assert platform_for(vm("linux", "centos7_64Guest")) == "linux-amd64"
    with pytest.raises(PushError):
        platform_for(vm("other", "otherGuest"))


def test_no_end_user_access(client):
    """vCentral is for the admin team: no vCenter sign-in, no owner pages."""
    with TestClient(app) as o:
        r = o.post("/api/auth/login", headers=H, json={"username": "ravi@vsphere.local", "password": "anything"})
        assert r.status_code == 401
    assert client.get("/api/my/vms").status_code == 404


def test_push_flow_and_secrets(client):
    r = client.post(f"/api/vms/{vm_id('web01')}/push", headers=H, json={"username": "root", "password": "guest-secret"})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded" and job["guest_user"] == "root" and "installed and running" in job["output"]
    assert [c[0] for c in FakeGuest.calls] == ["open", "upload", "run", "delete", "delete"]
    upload, run = FakeGuest.calls[1], FakeGuest.calls[2]
    assert upload[1] == "/usr/local/bin/vcentral-agent.download" and upload[3] == 0o755
    assert run[1] == "/bin/sh" and "--server https://vcentral.test --token vct_" in run[2]
    with SessionLocal() as db:
        t = db.query(EnrollmentToken).one()
        assert t.max_uses == 1 and t.name.startswith("push to web01")
        j = db.query(PushJob).one()
        assert "guest-secret" not in repr((j.steps, j.error, j.output, j.guest_user))  # never stored
        db.query(PushJob).update({"status": "running"})
        db.commit()
    assert client.post(f"/api/vms/{vm_id('web01')}/push", headers=H, json={"username": "root", "password": "x"}).status_code == 409
    assert client.get(f"/api/vms/{vm_id('web01')}/push-jobs").json()[0]["id"] == job["id"]

    cmd = client.post(f"/api/vms/{vm_id('db01')}/install-command", headers=H).json()
    assert cmd["token"].startswith("vct_") and cmd["server"] == "https://vcentral.test" and cmd["platform"] == "linux-amd64"


def test_push_failures(client):
    FakeGuest.reject_login = True
    job = wait_job(client, client.post(f"/api/vms/{vm_id('web01')}/push", headers=H, json={"username": "root", "password": "bad"}).json()["id"])
    assert job["status"] == "failed" and "rejected the username" in job["error"]

    FakeGuest.reject_login, FakeGuest.exit_code = False, 1
    job = wait_job(client, client.post(f"/api/vms/{vm_id('db01')}/push", headers=H, json={"username": "admin", "password": "x"}).json()["id"])
    assert job["status"] == "failed" and "exited with code 1" in job["error"]

    with SessionLocal() as db:
        db.query(VM).filter_by(name="web01").update({"tools_status": "guestToolsNotRunning"})
        db.commit()
    r = client.post(f"/api/vms/{vm_id('web01')}/push", headers=H, json={"username": "root", "password": "x"})
    assert r.status_code == 400 and "VMware Tools" in r.json()["detail"]


def test_admin_digest(client, monkeypatch):
    sent = []
    monkeypatch.setattr(onboarding, "send_email", lambda db, to, subject, text, html=None, kind="", vm_ids=None: sent.append(("email", to, subject, text)) or True)
    monkeypatch.setattr(onboarding, "send_chat", lambda db, text, subject, kind="", vm_ids=None: sent.append(("chat", text)) or True)
    with SessionLocal() as db:
        assert run_digest(db)["sent"] is False  # off by default
        save_policy(db, {"enabled": True, "include_after_days": 1, "every_days": 1, "overdue_after_days": 14})
        db.query(VM).update({"unmanaged_since": utcnow() - timedelta(days=2)})
        db.query(VM).filter_by(name="orphan01").update({"unmanaged_since": utcnow() - timedelta(days=20)})
        db.commit()
        result = run_digest(db)
    assert result == {"sent": True, "vm_count": 3, "overdue": 1, "failed": 0}
    # Only the admin team receives it: the configured admin address, never VM owners.
    (email,) = [s for s in sent if s[0] == "email"]
    assert email[1] == ["ops@example.com"] and "1 overdue" in email[2]
    assert "VSPHERE.LOCAL\\ravi (2)" in email[3] and "Unknown owner (1)" in email[3] and "https://vcentral.test/onboarding" in email[3]
    assert email[3].index("Overdue") < email[3].index("orphan01")

    sent.clear()
    with SessionLocal() as db:
        assert run_digest(db)["sent"] is False  # already sent today
        web = db.query(VM).filter_by(name="web01").one()
        db.add(Agent(agent_uuid="u1", secret_hash="s1", vm_id=web.id, status="active", ips=[], macs=[], metrics={},
                     top_processes={}, inventory={}))
        web.managed = True
        db.commit()
        update_unmanaged(db)
        assert db.query(VM).filter_by(name="web01").one().unmanaged_since is None
    data = client.get("/api/onboarding").json()
    assert data["stats"]["unmanaged"] == 2 and data["stats"]["no_owner"] == 1 and data["stats"]["overdue"] == 1
    assert client.post("/api/onboarding/digest", headers=H).json()["sent"] is True  # "send now" ignores the schedule

    # A brand-new VM is held back by the schedule but included when sending now.
    with SessionLocal() as db:
        db.query(VM).filter_by(name="db01").update({"unmanaged_since": utcnow()})
        db.commit()
        save_policy(db, {"every_days": 1})
        sent.clear()
        assert run_digest(db, force=True)["vm_count"] == 2


def test_settings_and_test_message(client):
    r = client.post("/api/system/notifications/test", headers=H, json={"channel": "email", "to": "me@example.com"}).json()
    assert r["ok"] is False and "not configured" in r["error"]  # no SMTP in tests
    assert client.get("/api/notifications").json()[0]["kind"] == "test"
    s = client.put("/api/system/settings", headers=H, json={"onboarding": {"enabled": True, "overdue_after_days": 7}}).json()
    assert s["onboarding"]["enabled"] is True and s["onboarding"]["overdue_after_days"] == 7
    assert client.put("/api/system/settings", headers=H, json={"onboarding": {"every_days": 0}}).status_code == 400


def test_push_as_sudo_user(client):
    """A normal user (e.g. 'ubuntu' on cloud images) installs through sudo; the password is never on a command line."""
    r = client.post(f"/api/vms/{vm_id('web01')}/push", headers=H, json={"username": "ubuntu", "password": "pa'ss $x"})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    kinds = [c[0] for c in FakeGuest.calls]
    assert kinds == ["open", "mkdtemp", "upload", "upload", "run", "rmdir"]
    agent_up, pw_up, run = FakeGuest.calls[2], FakeGuest.calls[3], FakeGuest.calls[4]
    assert agent_up[1] == "/tmp/vcentral-x1/vcentral-agent" and agent_up[3] == 0o755
    assert pw_up[1] == "/tmp/vcentral-x1/.sudo" and pw_up[3] == 0o600
    assert "sudo -S" in run[2] and "pa'ss" not in run[2] and "< /tmp/vcentral-x1/.sudo" in run[2]
    assert FakeGuest.calls[-1] == ("rmdir", "/tmp/vcentral-x1")  # staging always removed
    assert any("with sudo as 'ubuntu'" in s["message"] for s in job["steps"])

    FakeGuest.calls, FakeGuest.exit_code = [], 1
    FakeGuest.output = b"ubuntu is not in the sudoers file.  This incident will be reported.\n"
    job = wait_job(client, client.post(f"/api/vms/{vm_id('db01')}/push", headers=H, json={"username": "ubuntu", "password": "x"}).json()["id"])
    assert job["status"] == "failed" and "may not use sudo" in job["error"]
    assert FakeGuest.calls[-1][0] == "rmdir"
