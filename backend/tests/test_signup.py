"""Self sign-up: always a Viewer, optional admin approval, admin switches, abuse limits."""
import pytest
from fastapi.testclient import TestClient

from app.api.auth import signup_throttle
from app.db import SessionLocal
from app.main import app, bootstrap_admin
from app.models import AuditLog, User

from .test_api import H, ready_admin

PW = "Viewer-Pass-2026"


@pytest.fixture
def clients():
    bootstrap_admin()
    signup_throttle._failures.clear()
    with TestClient(app) as admin, TestClient(app) as guest:
        ready_admin(admin)
        yield admin, guest


def signup(c, username="neha", **kw):
    return c.post("/api/auth/signup", headers=H, json={"username": username, "full_name": "Neha K", "password": PW, **kw})


def test_signup_needs_approval_by_default(clients):
    admin, guest = clients
    assert guest.get("/api/auth/signup").json() == {"enabled": True, "requires_approval": True}
    r = signup(guest, role="admin")  # a role in the request is ignored
    assert r.status_code == 201 and r.json() == {"pending_approval": True}
    assert guest.get("/api/vms").status_code == 401  # not signed in

    r = guest.post("/api/auth/login", headers=H, json={"username": "neha", "password": PW})
    assert r.status_code == 403 and "waiting for an admin" in r.json()["detail"]
    r = guest.post("/api/auth/login", headers=H, json={"username": "neha", "password": "wrong-password-1"})
    assert r.status_code == 401  # wrong password does not reveal the pending account

    users = {u["username"]: u for u in admin.get("/api/users").json()}
    assert users["neha"]["pending_approval"] is True and users["neha"]["role"] == "viewer"
    assert admin.post(f"/api/users/{users['neha']['id']}/approve", headers=H).status_code == 200

    me = guest.post("/api/auth/login", headers=H, json={"username": "neha", "password": PW}).json()
    assert me["role"] == "viewer" and me["pending_approval"] is False
    assert guest.get("/api/vms").status_code == 200
    # Viewers cannot change anything.
    assert guest.put("/api/system/settings", headers=H, json={"signup": {"requires_approval": False}}).status_code == 403
    assert guest.get("/api/users").status_code == 403
    with SessionLocal() as db:
        assert [a.action for a in db.query(AuditLog).filter(AuditLog.action.in_(["signup", "approve_signup"])).order_by(AuditLog.id)] == ["signup", "approve_signup"]


def test_signup_without_approval_signs_in(clients):
    admin, guest = clients
    r = admin.put("/api/system/settings", headers=H, json={"signup": {"requires_approval": False}})
    assert r.json()["signup"] == {"enabled": True, "requires_approval": False}
    r = signup(guest, "arjun")
    assert r.status_code == 201 and r.json()["user"]["role"] == "viewer"
    assert guest.get("/api/auth/me").json()["username"] == "arjun"


def test_signup_rules(clients):
    admin, guest = clients
    assert signup(guest, "Admin").status_code == 409  # taken, case-insensitive
    assert signup(guest, "x").status_code == 422
    assert signup(guest, "weak", password="short").status_code == 400
    assert guest.post("/api/auth/signup", json={"username": "nocsrf", "password": PW}).status_code == 403
    admin.put("/api/system/settings", headers=H, json={"signup": {"enabled": False}})
    assert guest.get("/api/auth/signup").json()["enabled"] is False
    assert signup(guest, "late").status_code == 403
    with SessionLocal() as db:
        assert {u.username for u in db.query(User)} == {"admin"}


def test_signup_rate_limit(clients):
    _, guest = clients
    codes = [signup(guest, f"user{i}").status_code for i in range(7)]
    assert codes[:5] == [201] * 5 and codes[5:] == [429, 429]
