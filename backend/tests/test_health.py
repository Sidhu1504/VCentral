from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.api.series import downsample
from app.config import get_settings
from app.db import SessionLocal, engine, init_db, utcnow
from app.health import evaluate_health
from app.main import app, bootstrap_admin
from app.models import VM, Agent, VMHistory
from app.sync import run_sync

from .test_api import H, ready_admin
from .test_sync import FakeClient, make_vm


@pytest.fixture
def setup():
    bootstrap_admin()
    fake = FakeClient()
    fake.vms = [make_vm("a", "web01", ip="10.0.0.5"), make_vm("b", "db01", ip="10.0.0.6"),
                make_vm("c", "app01", power="poweredOff")]
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
        evaluate_health(db)
    with TestClient(app) as c:
        ready_admin(c)
        yield c, fake


def vm(name):
    with SessionLocal() as db:
        return db.query(VM).filter_by(name=name).one()


def resync(fake):
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
        evaluate_health(db)


def kinds(name):
    with SessionLocal() as db:
        v = db.query(VM).filter_by(name=name).one()
        return [h.kind for h in db.query(VMHistory).filter_by(vm_id=v.id).order_by(VMHistory.id)]


def test_no_ip_warning_then_critical_then_resolved(setup):
    c, fake = setup
    assert vm("web01").health == "ok" and vm("app01").health == "ok"  # powered-off normal VM is fine

    fake.vms[0] = make_vm("a", "web01", ip=None)  # Tools running, IP gone
    resync(fake)
    v = vm("web01")
    assert v.health == "warning" and v.problems[0]["code"] == "no_ip"

    r = c.patch(f"/api/vms/{v.id}", headers=H, json={"critical": True})
    assert r.json()["critical"] is True and r.json()["health"] == "critical"
    assert c.get("/api/vms", params={"health": "critical"}).json()["total"] == 1
    assert c.get("/api/vms", params={"critical": "true"}).json()["items"][0]["name"] == "web01"
    dash = c.get("/api/dashboard").json()
    assert dash["totals"]["health_critical"] == 1 and dash["totals"]["no_ip"] == 1
    assert dash["attention"][0]["problems"][0]["severity"] == "critical"

    fake.vms[0] = make_vm("a", "web01", ip="10.0.0.9")
    resync(fake)
    assert vm("web01").health == "ok"
    assert kinds("web01")[-4:] == ["problem", "critical_changed", "ip_changed", "resolved"]


def test_critical_powered_off_and_deleted(setup):
    c, fake = setup
    c.patch(f"/api/vms/{vm('app01').id}", headers=H, json={"critical": True})
    v = vm("app01")
    assert v.health == "critical" and v.problems[0]["message"] == "Critical VM is powered off"

    c.patch(f"/api/vms/{vm('db01').id}", headers=H, json={"critical": True})
    fake.vms = [fake.vms[0], fake.vms[2]]  # db01 deleted from vCenter
    resync(fake)
    assert vm("db01").problems[0]["code"] == "deleted"
    assert any(a["name"] == "db01" for a in c.get("/api/dashboard").json()["attention"])


def test_agent_offline_and_agent_reported_no_ip(setup):
    c, fake = setup
    fake.vms[0] = make_vm("a", "web01", ip="10.0.0.5")
    fake.vms[0]["tools_status"] = "guestToolsNotRunning"
    resync(fake)
    with SessionLocal() as db:
        v = db.query(VM).filter_by(name="web01").one()
        db.add(Agent(agent_uuid="x", secret_hash="h", vm_id=v.id, status="active", ips=[], macs=[],
                     metrics={}, top_processes={}, inventory={}, last_seen_at=utcnow()))
        db.commit()
        evaluate_health(db)
    assert [p["code"] for p in vm("web01").problems] == ["no_ip"]  # agent online, reports no IP

    with SessionLocal() as db:
        a = db.query(Agent).one()
        a.ips, a.last_seen_at = ["10.0.0.5"], utcnow() - timedelta(minutes=10)
        db.commit()
        evaluate_health(db)
    assert [p["code"] for p in vm("web01").problems] == ["agent_offline"]


def test_unmonitored_critical_vm(setup):
    c, fake = setup
    fake.vms[1]["tools_status"] = "guestToolsNotRunning"
    resync(fake)
    c.patch(f"/api/vms/{vm('db01').id}", headers=H, json={"critical": True})
    assert [p["code"] for p in vm("db01").problems] == ["not_monitored"]


def test_runtime_settings(setup):
    c, _ = setup
    s = c.get("/api/system/settings").json()
    assert s["values"]["agent_report_interval_seconds"] == 20 and s["values"]["agent_inventory_interval_seconds"] == 3600
    assert c.put("/api/system/settings", headers=H, json={"agent_report_interval_seconds": 7}).status_code == 400
    r = c.put("/api/system/settings", headers=H, json={"agent_report_interval_seconds": 10, "sync_interval_seconds": 20})
    assert r.json()["values"]["agent_report_interval_seconds"] == 10 and r.json()["agent_offline_after_seconds"] == 40

    token = c.post("/api/agent-tokens", headers=H, json={"name": "t"}).json()["token"]
    secret = c.post("/api/agent/enroll", json={"token": token, "facts": {"hostname": "h"}}).json()["secret"]
    rep = c.post("/api/agent/report", headers={"Authorization": f"Bearer {secret}"},
                 json={"metrics": {"cpu_pct": 1}, "ips": ["10.0.0.5"]}).json()
    assert rep["report_interval"] == 10 and rep["inventory_interval"] == 3600
    with SessionLocal() as db:
        assert db.query(Agent).one().ips == ["10.0.0.5"]


def test_settings_require_admin(setup):
    c, _ = setup
    c.post("/api/users", headers=H, json={"username": "op1", "password": "OperatorPass1", "role": "operator"})
    with TestClient(app) as o:
        o.post("/api/auth/login", headers=H, json={"username": "op1", "password": "OperatorPass1"})
        o.post("/api/auth/change-password", headers=H, json={"current_password": "OperatorPass1", "new_password": "OperatorPass2"})
        assert o.get("/api/system/settings").status_code == 200
        assert o.put("/api/system/settings", headers=H, json={"sync_interval_seconds": 60}).status_code == 403
        assert o.patch(f"/api/vms/{vm('web01').id}", headers=H, json={"critical": True}).status_code == 200


def test_downsample():
    start = utcnow() - timedelta(hours=24)
    pts = [{"_ts": start + timedelta(seconds=10 * i), "cpu_pct": float(i % 2) * 100} for i in range(8640)]
    out = downsample(pts, start, start + timedelta(hours=24))
    assert len(out) <= 720 and out[0]["cpu_pct"] == 50.0 and "_ts" not in out[0]
    small = downsample(pts[:5], start, start + timedelta(minutes=1))
    assert len(small) == 5 and small[0]["ts"].endswith("Z")


def test_schema_upgrade_adds_missing_columns():
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE vms DROP COLUMN critical"))
    assert "critical" not in {c["name"] for c in inspect(engine).get_columns("vms")}
    init_db()
    assert "critical" in {c["name"] for c in inspect(engine).get_columns("vms")}
