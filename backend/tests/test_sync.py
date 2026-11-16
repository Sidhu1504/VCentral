from datetime import timedelta

import pytest

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import VM, VMHistory, VMMetric, VMMetricHourly
from app.sync import rollup_hourly, run_sync
from app.vcenter import Inventory, classify_os


def make_vm(uuid, name, power="poweredOn", ip=None, owner_attr=None, cpu=2, cpu_pct=10.0, moid=None):
    return {
        "uuid": uuid, "bios_uuid": None, "moid": moid or f"vm-{uuid}", "name": name, "power_state": power,
        "guest_os": "Ubuntu Linux (64-bit)", "guest_id": "ubuntu64Guest", "os_family": "linux",
        "hostname": name, "primary_ip": ip, "ip_addresses": [ip] if ip else [], "mac_addresses": [],
        "num_cpu": cpu, "memory_mb": 4096, "storage_committed_gb": 20.0, "storage_provisioned_gb": 40.0,
        "guest_disks": [], "tools_status": "guestToolsRunning", "tools_version_status": None,
        "esxi_host": "esx1", "cluster": "C1", "folder": "DC/Prod", "datastores": ["ds1"], "annotation": None,
        "custom_attributes": {"Owner": owner_attr} if owner_attr else {}, "cpu_usage_mhz": 100,
        "cpu_pct": cpu_pct if power == "poweredOn" else None, "mem_active_mb": 512,
        "mem_pct": 12.5 if power == "poweredOn" else None, "disk_used_pct_max": None, "uptime_seconds": 10,
        "vcenter_created_at": None, "boot_time": None,
    }


class FakeClient:
    def __init__(self):
        self.vms, self.events, self.event_calls = [], {}, 0

    def collect(self):
        return Inventory(vms=[dict(v) for v in self.vms], vcenter_version="fake")

    def creation_events(self):
        self.event_calls += 1
        return self.events


def kinds(db):
    return sorted(h.kind for h in db.query(VMHistory) if h.kind != "discovered")


def test_lifecycle_detection():
    s, c = get_settings(), FakeClient()
    c.vms = [make_vm("a", "web01", ip="10.0.0.5"), make_vm("b", "db01", ip="10.0.0.6")]
    with SessionLocal() as db:
        run_sync(db, c, s)
        assert db.query(VM).count() == 2 and kinds(db) == []

        c.vms = [make_vm("a", "web01-new", ip="10.0.0.9", cpu=4), make_vm("c", "app01", power="poweredOff")]
        summary = run_sync(db, c, s)
        assert summary["created"] == 1 and summary["deleted"] == 1
        assert kinds(db) == ["created", "deleted", "ip_changed", "reconfigured", "renamed"]
        b = db.query(VM).filter_by(uuid="b").one()
        assert b.is_deleted and b.power_state == "deleted"

        # Deleted VM comes back (e.g. re-registered)
        c.vms.append(make_vm("b", "db01", ip="10.0.0.6"))
        run_sync(db, c, s)
        assert not db.query(VM).filter_by(uuid="b").one().is_deleted
        assert "reappeared" in kinds(db)


def test_ip_change_detected_across_power_off():
    s, c = get_settings(), FakeClient()
    c.vms = [make_vm("a", "web01", ip="10.0.0.5")]
    with SessionLocal() as db:
        run_sync(db, c, s)
        c.vms = [make_vm("a", "web01", power="poweredOff")]
        run_sync(db, c, s)
        vm = db.query(VM).one()
        assert vm.primary_ip is None and vm.last_known_ips == ["10.0.0.5"]
        c.vms = [make_vm("a", "web01", ip="10.0.0.77")]
        run_sync(db, c, s)
        assert kinds(db) == ["ip_changed", "powered_off", "powered_on"]


def test_empty_inventory_does_not_mass_delete():
    s, c = get_settings(), FakeClient()
    c.vms = [make_vm("a", "web01")]
    with SessionLocal() as db:
        run_sync(db, c, s)
        c.vms = []
        with pytest.raises(RuntimeError):
            run_sync(db, c, s)
        db.rollback()
        assert not db.query(VM).one().is_deleted


def test_owner_priority_and_retry():
    s, c = get_settings(), FakeClient()
    c.vms = [make_vm("a", "web01"), make_vm("b", "db01", owner_attr="bob"), make_vm("c", "old01")]
    c.events = {"vm-a": {"user": "VSPHERE.LOCAL\\alice"}}
    with SessionLocal() as db:
        run_sync(db, c, s)
        a, b, old = (db.query(VM).filter_by(uuid=u).one() for u in "abc")
        assert (a.owner, a.owner_source) == ("VSPHERE.LOCAL\\alice", "event")
        assert (b.owner, b.owner_source) == ("bob", "attribute")
        assert old.owner is None and not old.owner_checked  # still inside retry window

        old.first_seen_at = utcnow() - timedelta(hours=1)
        db.commit()
        run_sync(db, c, s)
        assert db.query(VM).filter_by(uuid="c").one().owner_checked
        calls = c.event_calls
        run_sync(db, c, s)
        assert c.event_calls == calls  # nothing pending, no more event queries

        # Manual owner beats the attribute; clearing the attribute resets attribute-based owners
        b.owner, b.owner_source = "carol", "manual"
        db.commit()
        run_sync(db, c, s)
        assert db.query(VM).filter_by(uuid="b").one().owner == "carol"


def test_metrics_and_rollup():
    s, c = get_settings(), FakeClient()
    c.vms = [make_vm("a", "web01", cpu_pct=20.0), make_vm("b", "db01", power="poweredOff")]
    with SessionLocal() as db:
        run_sync(db, c, s)
        assert db.query(VMMetric).count() == 1  # powered-off VMs have no samples
        vm_id = db.query(VM).filter_by(uuid="a").one().id
        hour = (utcnow() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
        for i, v in enumerate((10.0, 30.0, 50.0)):
            db.add(VMMetric(vm_id=vm_id, ts=hour + timedelta(minutes=i * 10), cpu_pct=v, mem_pct=40.0))
        db.commit()
        assert rollup_hourly(db) >= 1
        row = db.query(VMMetricHourly).filter_by(vm_id=vm_id, ts=hour).one()
        assert (row.cpu_pct_avg, row.cpu_pct_max, row.samples) == (30.0, 50.0, 3)
        assert rollup_hourly(db) == 0  # idempotent


@pytest.mark.parametrize("family,gid,name,expected", [
    ("windowsGuest", None, None, "windows"),
    (None, "windows2019srv_64Guest", None, "windows"),
    (None, "rhel9_64Guest", None, "linux"),
    (None, "otherGuest", "AlmaLinux (64-bit)", "linux"),
    (None, "otherGuest", "Other (64-bit)", "other"),
])
def test_classify_os(family, gid, name, expected):
    assert classify_os(family, gid, name) == expected
