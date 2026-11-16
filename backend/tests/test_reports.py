import csv
import io
from datetime import timedelta

from app.db import SessionLocal, utcnow
from app.models import VM, VMHistory, VMMetricHourly
from app.reports import REPORTS

from .test_api import H
from .test_tasks import client, vm_id  # noqa: F401 - fixture: web01 (linux agent), win01 (windows agent), db01 (no agent)


def seed():
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        web, db01 = db.query(VM).filter_by(name="web01").one(), db.query(VM).filter_by(name="db01").one()
        web.owner = "neha"
        for h in range(48):  # web01 idles, db01 is busy
            db.add(VMMetricHourly(vm_id=web.id, ts=now - timedelta(hours=h), cpu_pct_avg=2, cpu_pct_max=10,
                                  mem_pct_avg=30, mem_pct_max=40, disk_used_pct_max=50, samples=120))
            db.add(VMMetricHourly(vm_id=db01.id, ts=now - timedelta(hours=h), cpu_pct_avg=60, cpu_pct_max=97,
                                  mem_pct_avg=80, mem_pct_max=95, disk_used_pct_max=88, samples=120))
        db01.power_state = "poweredOff"
        db.add(VMHistory(vm_id=db01.id, ts=utcnow() - timedelta(days=40), kind="powered_off", message="db01 powered off"))
        db.add(VMHistory(vm_id=web.id, ts=utcnow() - timedelta(days=3), kind="created", message="web01 appeared"))
        db.commit()


def test_every_report_runs_and_exports(client):
    seed()
    listing = client.get("/api/reports").json()
    assert [r["key"] for r in listing["reports"]] == list(REPORTS)
    for key in REPORTS:
        r = client.get(f"/api/reports/{key}", params={"days": 30})
        assert r.status_code == 200, (key, r.text)
        data = r.json()
        assert data["columns"] and isinstance(data["rows"], list) and "summary" in data
        csv_r = client.get(f"/api/reports/{key}/export.csv", params={"days": 30})
        assert csv_r.status_code == 200 and "attachment" in csv_r.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(csv_r.text.lstrip("﻿"))))
        assert rows[0] == [c["title"] for c in data["columns"]] and len(rows) == len(data["rows"]) + 1
    assert client.get("/api/reports/nope").status_code == 404


def test_report_contents(client):
    seed()
    usage = {r["name"]: r for r in client.get("/api/reports/usage?days=7").json()["rows"]}
    assert usage["web01"]["flags"] == ["idle"] and usage["web01"]["cpu_avg"] == 2.0
    assert set(usage["db01"]["flags"]) == {"CPU busy", "memory high", "disk nearly full"}
    off = client.get("/api/reports/powered-off?days=30").json()
    assert off["rows"][0]["name"] == "db01" and off["rows"][0]["days_off"] == 40
    owners = {r["owner"]: r for r in client.get("/api/reports/owners").json()["rows"]}
    assert owners["neha"]["vm_names"] == ["web01"] and owners["(unknown)"]["vms"] == 2
    agents = client.get("/api/reports/agents").json()
    assert {r["name"]: r["agent"] for r in agents["rows"]}["db01"] == "none"
    changes = client.get("/api/reports/changes?days=7").json()["rows"]
    assert [(c["change"], c["name"]) for c in changes] == [("created", "web01")]
    assert client.get("/api/reports/changes?days=1").json()["rows"] == []


def test_viewers_can_read_reports(client):
    client.post("/api/users", headers=H, json={"username": "viewer1", "password": "Viewer-Pass-2026", "role": "viewer"})
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as v:
        v.post("/api/auth/login", headers=H, json={"username": "viewer1", "password": "Viewer-Pass-2026"})
        v.post("/api/auth/change-password", headers=H, json={"current_password": "Viewer-Pass-2026", "new_password": "Viewer-Pass-2027"})
        assert v.get("/api/reports/inventory").status_code == 200
        assert v.get("/api/reports/tasks/export.csv").status_code == 200
