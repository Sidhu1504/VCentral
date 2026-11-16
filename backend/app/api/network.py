"""IP & subnets: which addresses are taken, by what, and which are really free.

An address is taken when a VM uses it, when it belongs to the network (gateways, DNS servers, ESXi hosts, vCenter),
when a template holds it, when an admin reserved it, when a powered-off VM will take it back, or when a device
answered the vCentral server's network scan.
"""
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db, iso
from ..models import VM, IPReservation, User
from ..runtime import runtime
from ..security import require_operator, require_viewer
from ..state import get_state

router = APIRouter(prefix="/api/network", tags=["network"])

MAX_RANGE = 4096
# Display order: the first state that applies is the address's state.
STATES = ["conflict", "used", "infra", "template", "reserved", "stale", "device"]


def _addr(ip: str):
    try:
        return ipaddress.ip_address(ip)
    except ValueError:
        return None


def _reserved_ips(reservations: list[IPReservation]) -> dict[str, IPReservation]:
    out = {}
    for r in reservations:
        start, end = _addr(r.start_ip), _addr(r.end_ip or r.start_ip)
        if start is None or end is None or start.version != end.version:
            continue
        for n in range(int(start), min(int(end), int(start) + MAX_RANGE - 1) + 1):
            out.setdefault(str(ipaddress.ip_address(n)), r)
    return out


def network_facts(db: Session) -> dict:
    """Everything that can claim an address, keyed by IP."""
    vms = list(db.scalars(select(VM).where(VM.is_deleted.is_(False))))
    templates = get_state(db, "templates").get("items", [])
    infra = get_state(db, "network_infra").get("items", [])
    scan = get_state(db, "ip_scan")
    reservations = list(db.scalars(select(IPReservation)))
    mac_to_vm = {m.lower(): vm for vm in vms for m in (vm.mac_addresses or [])}
    return {"vms": vms, "templates": templates, "infra": infra, "scan": scan, "devices": scan.get("devices", {}),
            "reservations": reservations, "reserved": _reserved_ips(reservations), "mac_to_vm": mac_to_vm}


def _ref(vm: VM) -> dict:
    return {"id": vm.id, "name": vm.name, "power_state": vm.power_state, "owner": vm.owner}


def classify(facts: dict, nets) -> tuple[list[dict], list[dict]]:
    """Per-subnet usage, plus VMs whose IPs are outside every configured subnet."""
    claims: dict[str, dict] = {}

    def entry(ip):
        return claims.setdefault(ip, {"ip": ip, "vms": [], "stale_vms": [], "templates": [], "infra": [],
                                      "reservation": None, "device": None, "warnings": []})

    for vm in facts["vms"]:
        if vm.power_state == "poweredOn":
            for ip in vm.ip_addresses or []:
                entry(ip)["vms"].append(_ref(vm))
        else:
            for ip in vm.last_known_ips or []:
                entry(ip)["stale_vms"].append(_ref(vm))
    for t in facts["templates"]:
        for ip in t.get("ips") or []:
            entry(ip)["templates"].append({"name": t["name"], "folder": t.get("folder"), "was_vm_id": t.get("was_vm_id")})
    for item in facts["infra"]:
        labels = entry(item["ip"])["infra"]
        if item["label"] not in labels:
            labels.append(item["label"])
    for ip, r in facts["reserved"].items():
        entry(ip)["reservation"] = {"id": r.id, "label": r.label, "note": r.note}
    for ip, d in facts["devices"].items():
        e = entry(ip)
        e["device"] = {"mac": d.get("mac"), "last_seen": d.get("last_seen")}
        vm = facts["mac_to_vm"].get((d.get("mac") or "").lower())
        if vm is not None and not any(v["id"] == vm.id for v in e["vms"]) and vm.power_state == "poweredOn":
            e["vms"].append({**_ref(vm), "by_mac": True})  # VMware Tools did not report it; the MAC did

    for e in claims.values():
        live = e["device"] is not None
        if len(e["vms"]) > 1:
            e["state"] = "conflict"
        elif e["vms"]:
            e["state"] = "used"
        elif e["infra"]:
            e["state"] = "infra"
        elif e["templates"]:
            e["state"] = "template"
        elif e["reservation"]:
            e["state"] = "reserved"
        elif e["stale_vms"]:
            e["state"] = "stale"
        else:
            e["state"] = "device"
        if e["state"] == "stale" and live:
            e["warnings"].append("Something answers on this address while its VM is powered off: another device may "
                                 "have taken it, and the VM will clash when it starts")
        if e["vms"] and e["templates"]:
            e["warnings"].append(f"Also the IP of template {e['templates'][0]['name']}: VMs deployed from it may clash")
        if e["vms"] and e["reservation"]:
            e["warnings"].append(f"Reserved for {e['reservation']['label']}, but a VM uses it")
        if e["vms"] and e["infra"]:
            e["warnings"].append(f"A VM uses an infrastructure address ({e['infra'][0]})")

    subnets = []
    for net in nets:
        rows = sorted((e for ip, e in claims.items() if (a := _addr(ip)) is not None and a in net),
                      key=lambda e: ipaddress.ip_address(e["ip"]))
        usable = net.num_addresses - 2 if net.version == 4 and net.prefixlen < 31 else net.num_addresses
        counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
        taken = len(rows)
        next_free = []
        if net.version == 4 and net.num_addresses <= 65536:
            claimed = {r["ip"] for r in rows}
            for a in net.hosts():
                if str(a) not in claimed:
                    next_free.append(str(a))
                    if len(next_free) >= 10:
                        break
        subnets.append({
            "cidr": str(net), "first": str(net.network_address), "prefixlen": net.prefixlen, "version": net.version,
            "usable": usable, "taken": taken, "free": max(0, usable - taken),
            "used": counts["used"] + counts["conflict"], "conflicts": counts["conflict"], "counts": counts,
            "warnings": sum(1 for r in rows if r["warnings"]),
            "used_pct": round(taken / usable * 100, 1) if usable else 0,
            "next_free": next_free, "ips": rows,
        })

    outside = []
    for vm in facts["vms"]:
        if vm.power_state != "poweredOn":
            continue
        for ip in vm.ip_addresses or []:
            a = _addr(ip)
            if a is not None and a.version == 4 and not any(a in n for n in nets):
                outside.append({"ip": ip, "vm": {"id": vm.id, "name": vm.name}})
    return subnets, outside


@router.get("/subnets")
def subnets(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    facts = network_facts(db)
    nets = get_settings().subnet_list
    result, outside = classify(facts, nets)
    scan = facts["scan"]
    return {
        "subnets": result, "outside": outside,
        "scan": {"interval_minutes": runtime()["ip_scan_interval_minutes"], "finished_at": scan.get("finished_at"),
                 "scanned": scan.get("scanned"), "answered": scan.get("answered"), "method": scan.get("method")},
        "templates": len(facts["templates"]),
        "reservations": [reservation_out(r) for r in facts["reservations"]],
    }


# -- reservations --------------------------------------------------------------------------------------

class ReservationIn(BaseModel):
    start_ip: str = Field(max_length=45)
    end_ip: str | None = Field(None, max_length=45)
    label: str = Field(min_length=1, max_length=128)
    note: str | None = Field(None, max_length=1000)


def reservation_out(r: IPReservation) -> dict:
    return {"id": r.id, "start_ip": r.start_ip, "end_ip": r.end_ip, "label": r.label, "note": r.note,
            "created_by": r.created_by, "created_at": iso(r.created_at)}


@router.post("/reservations", status_code=201)
def add_reservation(body: ReservationIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_operator)):
    start, end = _addr(body.start_ip.strip()), _addr((body.end_ip or body.start_ip).strip())
    if start is None or end is None:
        raise HTTPException(400, "Enter valid IP addresses")
    if start.version != end.version or int(end) < int(start):
        raise HTTPException(400, "The range must end at or after its first address")
    if int(end) - int(start) + 1 > MAX_RANGE:
        raise HTTPException(400, f"A range can have at most {MAX_RANGE} addresses")
    r = IPReservation(start_ip=str(start), end_ip=str(end) if end != start else None, label=body.label.strip(),
                      note=body.note, created_by=user.username)
    db.add(r)
    audit(db, request, user.username, "ip_reserve", target=f"{r.start_ip}{'-' + r.end_ip if r.end_ip else ''}", label=r.label)
    db.commit()
    return reservation_out(r)


@router.delete("/reservations/{reservation_id}", status_code=204)
def delete_reservation(reservation_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_operator)):
    r = db.get(IPReservation, reservation_id)
    if r is None:
        raise HTTPException(404, "Reservation not found")
    audit(db, request, user.username, "ip_unreserve", target=f"{r.start_ip}{'-' + r.end_ip if r.end_ip else ''}", label=r.label)
    db.delete(r)
    db.commit()
