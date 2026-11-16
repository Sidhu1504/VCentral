"""IP & subnets: an address is free only when nothing claims it."""
import ipaddress

import pytest
from fastapi.testclient import TestClient

from app import netscan
from app.config import get_settings
from app.db import SessionLocal
from app.main import app, bootstrap_admin
from app.models import VMHistory
from app.state import get_state, set_state
from app.sync import run_sync
from app.vcenter import Inventory

from .test_api import H, ready_admin
from .test_sync import make_vm


class NetFake:
    def __init__(self):
        self.vms, self.templates, self.infra = [], [], []

    def collect(self):
        return Inventory(vms=[dict(v) for v in self.vms], templates=[dict(t) for t in self.templates],
                         infrastructure=list(self.infra), vcenter_version="fake")

    def creation_events(self):
        return {}


def vm(uuid, name, ip=None, power="poweredOn", macs=()):
    v = make_vm(uuid, name, ip=ip, power=power)
    v["mac_addresses"] = list(macs)
    return v


@pytest.fixture
def client():
    bootstrap_admin()
    fake = NetFake()
    fake.vms = [vm("a", "web", "10.0.0.5"), vm("b", "dup1", "10.0.0.6"), vm("c", "dup2", "10.0.0.6"),
                vm("d", "off", "10.0.0.9"), vm("e", "golden", "10.0.0.41"), vm("f", "no-tools", macs=["00:50:56:aa:bb:cc"])]
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
        # "off" is powered off now (keeps 10.0.0.9); "golden" is converted to a template.
        fake.vms[3] = vm("d", "off", power="poweredOff")
        golden = fake.vms.pop(4)
        fake.templates = [{"uuid": "e", "moid": "vm-e", "name": "golden", "folder": "Templates", "guest_os": "Ubuntu",
                           "ip_addresses": []},
                          {"uuid": "t2", "moid": "vm-t2", "name": "win-tpl", "folder": None, "guest_os": "Windows",
                           "ip_addresses": ["10.0.0.40"]}]
        fake.infra = [{"ip": "10.0.0.1", "kind": "gateway", "label": "Default gateway (used by 5 VMs)"},
                      {"ip": "10.0.0.254", "kind": "esxi", "label": "ESXi host esx01 (vmk0)"}]
        run_sync(db, fake, get_settings())
        assert golden["name"] == "golden"
        set_state(db, "ip_scan", {"finished_at": "2026-11-17T00:00:00Z", "scanned": 254, "answered": 4, "method": "ping",
                                  "devices": {"10.0.0.1": {"mac": "aa:aa:aa:aa:aa:01", "last_seen": "2026-11-17T00:00:00Z"},
                                              "10.0.0.9": {"mac": "aa:aa:aa:aa:aa:09", "last_seen": "2026-11-17T00:00:00Z"},
                                              "10.0.0.50": {"mac": "aa:aa:aa:aa:aa:50", "last_seen": "2026-11-17T00:00:00Z"},
                                              "10.0.0.30": {"mac": "00:50:56:AA:BB:CC", "last_seen": "2026-11-17T00:00:00Z"}}})
        db.commit()
    with TestClient(app) as c:
        ready_admin(c)
        yield c


def test_every_claim_counts(client):
    r = client.post("/api/network/reservations", headers=H, json={"start_ip": "10.0.0.100", "end_ip": "10.0.0.102", "label": "DHCP pool"})
    assert r.status_code == 201
    data = client.get("/api/network/subnets").json()
    (net,) = data["subnets"]
    by_ip = {r["ip"]: r for r in net["ips"]}
    assert by_ip["10.0.0.1"]["state"] == "infra" and "gateway" in by_ip["10.0.0.1"]["infra"][0]
    assert by_ip["10.0.0.254"]["state"] == "infra"
    assert by_ip["10.0.0.5"]["state"] == "used"
    assert by_ip["10.0.0.6"]["state"] == "conflict"
    assert by_ip["10.0.0.40"]["state"] == "template" and by_ip["10.0.0.40"]["templates"][0]["name"] == "win-tpl"
    # A VM converted to a template keeps its IP held, by the template.
    assert by_ip["10.0.0.41"]["state"] == "template" and by_ip["10.0.0.41"]["templates"][0]["name"] == "golden"
    assert [by_ip[f"10.0.0.{n}"]["state"] for n in (100, 101, 102)] == ["reserved"] * 3
    assert by_ip["10.0.0.50"]["state"] == "device" and by_ip["10.0.0.50"]["device"]["mac"] == "aa:aa:aa:aa:aa:50"
    # A VM without VMware Tools, found by its MAC in the scan.
    assert by_ip["10.0.0.30"]["state"] == "used" and by_ip["10.0.0.30"]["vms"][0]["by_mac"] is True
    # A powered-off VM's address that something answers on is flagged.
    assert by_ip["10.0.0.9"]["state"] == "stale" and "another device" in by_ip["10.0.0.9"]["warnings"][0]
    taken = {"10.0.0.1", "10.0.0.254", "10.0.0.5", "10.0.0.6", "10.0.0.9", "10.0.0.30", "10.0.0.40", "10.0.0.41",
             "10.0.0.50", "10.0.0.100", "10.0.0.101", "10.0.0.102"}
    assert net["taken"] == len(taken) and net["free"] == 254 - len(taken)
    assert net["next_free"][:4] == ["10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.7"]
    assert not taken & set(net["next_free"])
    with SessionLocal() as db:
        assert any("converted to a template" in h.message for h in db.query(VMHistory))


def test_reservation_rules(client):
    assert client.post("/api/network/reservations", headers=H, json={"start_ip": "10.0.0.9", "end_ip": "10.0.0.2", "label": "x"}).status_code == 400
    assert client.post("/api/network/reservations", headers=H, json={"start_ip": "nope", "label": "x"}).status_code == 400
    assert client.post("/api/network/reservations", headers=H, json={"start_ip": "10.0.0.0", "end_ip": "10.0.255.255", "label": "x"}).status_code == 400
    rid = client.post("/api/network/reservations", headers=H, json={"start_ip": "10.0.0.77", "label": "Printer"}).json()["id"]
    assert client.delete(f"/api/network/reservations/{rid}", headers=H).status_code == 204


def test_scan_merges_and_forgets(monkeypatch):
    calls = iter([{"10.9.0.5": {"via": "ping", "mac": "aa:bb:cc:dd:ee:ff"}, "10.9.0.6": {"via": "arp", "mac": "11:22:33:44:55:66"}},
                  {"10.9.0.6": {"via": "ping"}}])

    async def fake_sweep(addresses, timeout=1.0):
        return next(calls)
    monkeypatch.setattr(netscan, "sweep", fake_sweep)
    net = [ipaddress.ip_network("10.9.0.0/29")]
    with SessionLocal() as db:
        first = netscan.run_scan(db, net)
        assert first["scanned"] == 6 and set(first["devices"]) == {"10.9.0.5", "10.9.0.6"}
        second = netscan.run_scan(db, net)
        # 10.9.0.5 did not answer this time but is still remembered (with when it was last seen);
        # 10.9.0.6 keeps the MAC learned earlier.
        assert set(second["devices"]) == {"10.9.0.5", "10.9.0.6"} and second["devices"]["10.9.0.6"]["mac"] == "11:22:33:44:55:66"
        assert get_state(db, "ip_scan")["answered"] == 1


def test_read_arp(tmp_path):
    f = tmp_path / "arp"
    f.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                 "192.168.14.1     0x1         0x2         AA:BB:CC:00:11:22     *        ens160\n"
                 "192.168.14.77    0x1         0x0         00:00:00:00:00:00     *        ens160\n")
    assert netscan.read_arp(str(f)) == {"192.168.14.1": "aa:bb:cc:00:11:22"}


def test_gateways_from_guest_routes():
    from types import SimpleNamespace as NS

    from app.vcenter import _guest_dns, _guest_gateways
    route = lambda net, plen, gw: NS(network=net, prefixLength=plen, gateway=NS(ipAddress=gw))  # noqa: E731
    stack = NS(ipRouteConfig=NS(ipRoute=[route("0.0.0.0", 0, "192.168.14.1"), route("192.168.14.0", 24, None),
                                         route("10.0.0.0", 8, "192.168.14.9"), route("::", 0, "fe80::1")]),
               dnsConfig=NS(ipAddress=["192.168.14.10", "127.0.0.53"]))
    raw = {"guest.ipStack": [stack]}
    assert _guest_gateways(raw) == ["192.168.14.1"]  # only the default route; link-local skipped
    assert _guest_dns(raw) == ["192.168.14.10"]  # the local resolver stub is not a network device
    assert _guest_gateways({}) == [] and _guest_dns({"guest.ipStack": None}) == []
