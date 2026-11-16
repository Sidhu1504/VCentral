from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app, bootstrap_admin
from app.models import VM
from app.owners import account_key, name_tokens, suggest_owners
from app.sync import run_sync

from .test_api import H, ready_admin
from .test_sync import FakeClient, make_vm


def act(count, kinds=("VmAcquiredMksTicketEvent",), days_ago=1):
    return {"count": count, "last": utcnow() - timedelta(days=days_ago), "kinds": set(kinds)}


@pytest.fixture
def client():
    bootstrap_admin()
    fake = FakeClient()
    fake.vms = [make_vm("a", "Nachiket-Win11-24H2", moid="vm-1"), make_vm("b", "Roshan_linux", moid="vm-2"),
                make_vm("c", "build-runner-07", moid="vm-3"), make_vm("d", "test-box", moid="vm-4"),
                make_vm("e", "owned01", moid="vm-5", owner_attr="QA team")]
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
    with TestClient(app) as c:
        ready_admin(c)
        yield c


ACTIVITY = {
    # Nachiket's VM: he opens its console; the shared admin account is ignored.
    "vm-1": {"VSPHERE.LOCAL\\nachiket": act(5), "VSPHERE.LOCAL\\Administrator": act(40)},
    # Roshan's name is in the VM name; someone else used it once.
    "vm-2": {"VSPHERE.LOCAL\\priya": act(1, ("VmPoweredOnEvent",))},
    # No name match: the only user who touches it is the suggestion.
    "vm-3": {"VSPHERE.LOCAL\\arjun.k": act(3, ("VmPoweredOnEvent", "VmReconfiguredEvent"))},
    # Roshan shows up somewhere, which makes him a known person.
    "vm-9": {"roshan@vsphere.local": act(1)},
}


def test_helpers():
    assert account_key("VSPHERE.LOCAL\\Ravi") == account_key("ravi@vsphere.local") == "ravi@vsphere.local"
    assert name_tokens("CORP\\nachiket.patil") == ["nachiket", "patil"]
    assert name_tokens("VSPHERE.LOCAL\\admin") == []  # too common to identify anyone


def test_suggestions(client):
    with SessionLocal() as db:
        result = suggest_owners(db, ACTIVITY, get_settings())
    assert result["suggested"] == 3
    items = {i["name"]: i for i in client.get("/api/owners").json()["items"]}
    n = items["Nachiket-Win11-24H2"]
    assert n["suggestion"] == "VSPHERE.LOCAL\\nachiket" and n["confidence"] == "high"  # name + activity agree
    assert "nachiket" in n["reason"] and "opened the console" in n["reason"]
    r = items["Roshan_linux"]
    assert r["suggestion"] == "roshan@vsphere.local" and r["confidence"] == "medium"  # name wins over a passer-by
    b = items["build-runner-07"]
    assert b["suggestion"] == "VSPHERE.LOCAL\\arjun.k" and b["confidence"] == "medium"
    assert items["test-box"]["suggestion"] is None
    assert "owned01" not in items  # already has an owner (vCenter attribute)
    stats = client.get("/api/owners").json()["stats"]
    assert (stats["unknown"], stats["suggested"], stats["confidence"]["high"]) == (4, 3, 1)


def test_accept_and_bulk_owner(client):
    with SessionLocal() as db:
        suggest_owners(db, ACTIVITY, get_settings())
        ids = {v.name: v.id for v in db.query(VM)}
    r = client.post("/api/owners/accept", headers=H, json={"vm_ids": [ids["Nachiket-Win11-24H2"], ids["test-box"]]})
    assert r.json()["accepted"] == 1  # test-box had no suggestion
    vm = client.get(f"/api/vms/{ids['Nachiket-Win11-24H2']}").json()
    assert (vm["owner"], vm["owner_source"]) == ("VSPHERE.LOCAL\\nachiket", "suggested")
    assert client.get(f"/api/vms/{ids['Nachiket-Win11-24H2']}/history").json()[0]["kind"] == "owner_changed"

    r = client.post("/api/vms/bulk-owner", headers=H, json={"vm_ids": [ids["test-box"], ids["build-runner-07"]], "owner": "QA team"})
    assert r.json()["updated"] == 2
    assert client.get("/api/vms", params={"owner": "QA team"}).json()["total"] == 3
    assert client.get(f"/api/vms/{ids['build-runner-07']}").json()["owner_source"] == "manual"

    assert client.post("/api/owners/scan", headers=H).json() == {"ok": True}
    assert client.get("/api/owners").json()["scan_pending"] is True


def test_dashboard_lists_new_and_deleted_vms(client):
    fake = FakeClient()
    fake.vms = [make_vm("a", "Nachiket-Win11-24H2", moid="vm-1"), make_vm("b", "Roshan_linux", moid="vm-2"),
                make_vm("c", "build-runner-07", moid="vm-3"), make_vm("e", "owned01", moid="vm-5", owner_attr="QA team"),
                make_vm("f", "brand-new-vm", moid="vm-6")]
    fake.events = {"vm-6": {"user": "VSPHERE.LOCAL\\neha"}}
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())  # test-box deleted, brand-new-vm created
    changes = {c["vm_name"]: c for c in client.get("/api/dashboard").json()["vm_changes_7d"]}
    assert changes["brand-new-vm"]["kind"] == "created" and changes["brand-new-vm"]["owner"] == "VSPHERE.LOCAL\\neha"
    assert changes["test-box"]["kind"] == "deleted" and changes["test-box"]["deleted"] is True


def test_template_token_check(client):
    one = client.post("/api/agent-tokens", headers=H, json={"name": "one", "max_uses": 1}).json()["token"]
    many = client.post("/api/agent-tokens", headers=H, json={"name": "tpl", "expires_in_hours": None}).json()["token"]
    assert client.post("/api/agent/token-check", json={"token": many}).json()["reusable"] is True
    r = client.post("/api/agent/token-check", json={"token": one}).json()
    assert r["valid"] is True and r["reusable"] is False
    assert client.post("/api/agent/token-check", json={"token": "vct_nope"}).json() == {"valid": False, "reusable": False, "reason": "unknown token"}
    # Checking does not use the token up.
    assert client.post("/api/agent/token-check", json={"token": one}).json()["valid"] is True
