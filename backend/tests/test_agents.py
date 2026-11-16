from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.agents import uuid_variants
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app, bootstrap_admin
from app.models import VM, Agent, AgentMetric, AgentMetricHourly
from app.sync import rollup_hourly, run_sync

from .test_api import H, ready_admin
from .test_sync import FakeClient, make_vm

VC_UUID = "4213a1b2-c3d4-e5f6-0718-293a4b5c6d7e"   # vCenter config.uuid
GUEST_UUID = "b2a11342-d4c3-f6e5-0718-293a4b5c6d7e"  # same, as some guests report it (byte-swapped)


@pytest.fixture
def client():
    bootstrap_admin()
    fake = FakeClient()
    web = make_vm("a", "web01", ip="10.0.0.5")
    web["bios_uuid"] = VC_UUID
    db_vm = make_vm("b", "db01", ip="10.0.0.6")
    db_vm["mac_addresses"] = ["00:50:56:aa:bb:cc"]
    fake.vms = [web, db_vm, make_vm("c", "app01")]
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
    with TestClient(app) as c:
        ready_admin(c)
        yield c


def new_token(c, **kw):
    r = c.post("/api/agent-tokens", headers=H, json={"name": "test", **kw})
    assert r.status_code == 201
    return r.json()["token"]


def facts(**kw):
    return {"hostname": "web01", "os_family": "linux", "os_name": "Ubuntu 22.04", "arch": "x86_64",
            "bios_uuid": GUEST_UUID, "macs": ["00:50:56:00:00:01"], "ips": ["10.0.0.5"], "agent_version": "0.2.0", **kw}


def enroll(c, token, **kw):
    # No X-Requested-With: agents are exempt from the browser CSRF check.
    return c.post("/api/agent/enroll", json={"token": token, "facts": facts(**kw)})


def report(c, secret, inventory=False, bios=GUEST_UUID, macs=("00:50:56:00:00:01",)):
    body = {"agent_version": "0.2.0", "bios_uuid": bios,
            "metrics": {"cpu_pct": 42.5, "mem_pct": 61.0, "disks": [{"mount": "/", "used_pct": 70.1}, {"mount": "/data", "used_pct": 91.0}],
                        "net_rx_bps": 1000, "net_tx_bps": 500},
            "top_processes": {"by_cpu": [{"pid": 1, "name": "java", "cpu_pct": 30}], "by_mem": []}}
    if inventory:
        body["inventory"] = {"facts": facts(bios_uuid=bios, macs=list(macs)), "cpu_model": "Xeon", "cpu_cores": 4, "mem_total_mb": 8192,
                             "software": [{"name": "nginx", "version": "1.24"}], "services": [{"name": "sshd", "state": "running"}],
                             "listening_ports": [{"proto": "tcp", "address": "0.0.0.0", "port": 22}]}
    return c.post("/api/agent/report", json=body, headers={"Authorization": f"Bearer {secret}"})


def vm_id(name):
    with SessionLocal() as db:
        return db.query(VM).filter_by(name=name).one().id


def test_uuid_variants():
    assert uuid_variants(VC_UUID) == uuid_variants(GUEST_UUID) == {VC_UUID, GUEST_UUID}


def test_enroll_report_and_link(client):
    token = new_token(client, max_uses=1)
    assert enroll(client, "vct_wrong").status_code == 401
    r = enroll(client, token)
    assert r.status_code == 200
    data = r.json()
    assert data["secret"].startswith("vca_") and data["reusable_token"] is False
    assert enroll(client, token, bios_uuid="").status_code == 401  # single-use token is spent

    agent = client.get("/api/agents").json()[0]
    assert agent["vm_name"] == "web01" and agent["link_source"] == "bios_uuid" and agent["state"] == "offline"

    assert report(client, "vca_bogus").status_code == 401
    r = report(client, data["secret"], inventory=True)
    assert r.status_code == 200 and r.json()["send_inventory"] is False and r.json()["latest_version"] == "0.2.0"

    detail = client.get(f"/api/vms/{vm_id('web01')}").json()
    assert detail["managed"] and detail["agent"] == "online"
    a = detail["agent_detail"]
    assert a["metrics"]["cpu_pct"] == 42.5 and a["counts"]["software"] == 1 and a["system"]["cpu_model"] == "Xeon"
    assert client.get(f"/api/agents/{a['id']}/inventory/software").json() == [{"name": "nginx", "version": "1.24"}]
    with SessionLocal() as db:
        assert db.query(AgentMetric).one().disk_used_pct_max == 91.0

    # Inventory refresh request is delivered with the next report
    client.post(f"/api/agents/{a['id']}/refresh-inventory", headers=H)
    assert report(client, data["secret"]).json()["send_inventory"] is True

    # VM list filter and dashboard counts
    assert [v["name"] for v in client.get("/api/vms", params={"agent": "online"}).json()["items"]] == ["web01"]
    assert client.get("/api/vms", params={"agent": "none"}).json()["total"] == 2
    totals = client.get("/api/dashboard").json()["totals"]
    assert (totals["agents_online"], totals["managed"]) == (1, 1)


def test_clone_detection_and_reinstall(client):
    token = new_token(client)  # unlimited uses
    first = enroll(client, token).json()
    assert first["reusable_token"] is True
    # Same credentials reported from a machine with a different BIOS UUID (cloned disk)
    assert report(client, first["secret"], bios="11111111-2222-3333-4444-555555555555").status_code == 409

    # Reinstall on the same machine replaces the old agent
    second = enroll(client, token).json()
    agents = {a["agent_id"]: a for a in client.get("/api/agents").json()}
    assert agents[first["agent_id"]]["state"] == "revoked" and agents[second["agent_id"]]["state"] == "offline"
    assert report(client, first["secret"]).status_code == 401


def test_revoke_link_and_delete(client):
    secret = enroll(client, new_token(client), bios_uuid="", macs=["00:50:56:AA:BB:CC"]).json()["secret"]
    agent = client.get("/api/agents").json()[0]
    assert agent["vm_name"] == "db01" and agent["link_source"] == "mac"

    # Manual link wins and is not undone by automatic matching
    r = client.post(f"/api/agents/{agent['id']}/link", headers=H, json={"vm_id": vm_id("app01")})
    assert r.json()["vm_name"] == "app01" and r.json()["link_source"] == "manual"
    report(client, secret, inventory=True, bios="", macs=["00:50:56:aa:bb:cc"])
    assert client.get(f"/api/agents/{agent['id']}").json()["vm_name"] == "app01"
    assert client.post(f"/api/agents/{agent['id']}/link/auto", headers=H).json()["vm_name"] == "db01"

    assert client.delete(f"/api/agents/{agent['id']}", headers=H).status_code == 400  # must revoke first
    assert client.post(f"/api/agents/{agent['id']}/revoke", headers=H).json()["state"] == "revoked"
    assert client.get(f"/api/vms/{vm_id('db01')}").json()["managed"] is False
    assert report(client, secret, bios="").status_code == 401
    assert client.delete(f"/api/agents/{agent['id']}", headers=H).status_code == 204


def test_token_states_and_permissions(client):
    r = client.post("/api/agent-tokens", headers=H, json={"name": "temp", "expires_in_hours": 1})
    token, token_id = r.json()["token"], r.json()["id"]
    assert "token" not in client.get("/api/agent-tokens").json()[0]  # shown only once
    client.post(f"/api/agent-tokens/{token_id}/revoke", headers=H)
    assert client.get("/api/agent-tokens").json()[0]["state"] == "revoked"
    assert enroll(client, token).status_code == 401
    assert client.post("/api/agent-tokens", json={"name": "x"}).status_code == 403  # browser call without CSRF header

    client.post("/api/users", headers=H, json={"username": "viewer1", "password": "ViewerPass123", "role": "viewer"})
    with TestClient(app) as v:
        v.post("/api/auth/login", headers=H, json={"username": "viewer1", "password": "ViewerPass123"})
        v.post("/api/auth/change-password", headers=H, json={"current_password": "ViewerPass123", "new_password": "ViewerPass456"})
        assert v.get("/api/agents").status_code == 200
        assert v.get("/api/agent-tokens").status_code == 403
        assert v.post("/api/agent-tokens", headers=H, json={"name": "x"}).status_code == 403


def test_enroll_throttle(client):
    for _ in range(10):
        assert enroll(client, "vct_wrong").status_code == 401
    assert enroll(client, new_token(client)).status_code == 429


def test_installers_and_downloads(client):
    assert "Install-VCentralAgent" in client.get("/api/agent/install.ps1").text
    assert client.get("/api/agent/install.sh").text.startswith("#!/bin/sh")
    assert client.get("/api/agent/download/linux-amd64").content.startswith(b"\x7fELF")
    assert client.get("/api/agent/download/windows-amd64").status_code == 404  # not built in this test
    assert client.get("/api/agent/download/../../etc/passwd").status_code == 404
    info = client.get("/api/agents/install-info").json()
    assert info["platforms"] == ["linux-amd64"] and info["version"] == "0.2.0"


def test_agent_metrics_rollup(client):
    enroll(client, new_token(client))
    with SessionLocal() as db:
        agent = db.query(Agent).one()
        hour = (utcnow() - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        for i, (cpu, rx) in enumerate(((10.0, 100.0), (50.0, 300.0))):
            db.add(AgentMetric(agent_id=agent.id, ts=hour + timedelta(minutes=i), cpu_pct=cpu, net_rx_bps=rx))
        db.commit()
        rollup_hourly(db)
        row = db.query(AgentMetricHourly).filter_by(agent_id=agent.id, ts=hour).one()
        assert (row.cpu_pct_avg, row.cpu_pct_max, row.net_rx_bps, row.samples) == (30.0, 50.0, 200.0, 2)
    pts = client.get(f"/api/agents/{agent.id}/metrics", params={"range": "7d"}).json()["points"]
    assert pts[0]["cpu_pct"] == 30.0
