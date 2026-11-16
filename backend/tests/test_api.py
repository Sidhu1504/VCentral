import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.main import app, bootstrap_admin
from app.sync import run_sync

from .test_sync import FakeClient, make_vm

H = {"X-Requested-With": "vcentral"}


@pytest.fixture
def client():
    bootstrap_admin()
    with TestClient(app) as c:
        yield c


def login(c, username="admin", password="InitialPass123"):
    return c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)


def ready_admin(c):
    assert login(c).status_code == 200
    r = c.post("/api/auth/change-password", headers=H,
               json={"current_password": "InitialPass123", "new_password": "NewAdminPass456"})
    assert r.status_code == 200


def test_requires_login_and_csrf_header(client):
    assert client.get("/api/vms").status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": "InitialPass123"})
    assert r.status_code == 403  # missing X-Requested-With


def test_forced_password_change(client):
    assert login(client).json()["must_change_password"] is True
    assert client.get("/api/vms").status_code == 403
    ready_admin(client)
    assert client.get("/api/vms").status_code == 200


def test_login_throttle(client):
    for _ in range(5):
        assert login(client, password="wrong-password").status_code == 401
    assert login(client).status_code == 429


def test_roles_and_last_admin(client):
    ready_admin(client)
    r = client.post("/api/users", headers=H, json={"username": "viewer1", "password": "ViewerPass123", "role": "viewer"})
    assert r.status_code == 201
    admin_id = client.get("/api/auth/me").json()["id"]
    assert client.patch(f"/api/users/{admin_id}", headers=H, json={"role": "viewer"}).status_code == 400

    with TestClient(app) as v:
        login(v, "viewer1", "ViewerPass123")
        v.post("/api/auth/change-password", headers=H,
               json={"current_password": "ViewerPass123", "new_password": "ViewerPass456"})
        assert v.get("/api/dashboard").status_code == 200
        assert v.post("/api/system/sync", headers=H).status_code == 403
        assert v.get("/api/users").status_code == 403
        assert v.get("/api/audit").status_code == 403


def test_vm_endpoints(client):
    fake = FakeClient()
    fake.vms = [make_vm("a", "web01", ip="10.0.0.5"), make_vm("b", "=cmd|calc", ip="10.0.0.6")]
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
    ready_admin(client)

    data = client.get("/api/vms", params={"search": "web"}).json()
    assert data["total"] == 1
    vm_id = data["items"][0]["id"]

    r = client.patch(f"/api/vms/{vm_id}", headers=H, json={"owner": "dave", "notes": "payments team"})
    assert r.json()["owner"] == "dave" and r.json()["owner_source"] == "manual"
    assert client.get(f"/api/vms/{vm_id}/history").json()[0]["kind"] == "owner_changed"
    assert len(client.get(f"/api/vms/{vm_id}/metrics", params={"range": "1h"}).json()["points"]) == 1

    csv_text = client.get("/api/vms/export.csv").text
    assert "'=cmd|calc" in csv_text  # formula injection neutralised

    dash = client.get("/api/dashboard").json()
    assert dash["totals"]["total"] == 2 and dash["subnets"][0]["used"] == 2
    assert client.get("/api/network/subnets").json()["subnets"][0]["free"] == 252

    assert client.post("/api/system/sync", headers=H).json() == {"ok": True}
    actions = {a["action"] for a in client.get("/api/audit").json()["items"]}
    assert {"login", "change_password", "update_vm", "export_vms", "request_sync"} <= actions
