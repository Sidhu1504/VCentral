import ipaddress

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import VM, User
from ..security import require_viewer

router = APIRouter(prefix="/api/network", tags=["network"])


def _addr(ip: str):
    try:
        return ipaddress.ip_address(ip)
    except ValueError:
        return None


def subnet_usage(vms: list[VM], nets) -> list[dict]:
    """Per-subnet IP usage. Powered-on VMs hold their current IPs ("used"/"conflict");
    powered-off VMs show their last known IP as "stale" (DHCP may hand it to someone else)."""
    result = []
    for net in nets:
        holders: dict[str, list[dict]] = {}
        stale: dict[str, list[dict]] = {}
        for vm in vms:
            if vm.is_deleted:
                continue
            ref = {"id": vm.id, "name": vm.name, "power_state": vm.power_state, "owner": vm.owner}
            current = vm.ip_addresses if vm.power_state == "poweredOn" else []
            for ip in current or []:
                a = _addr(ip)
                if a is not None and a in net:
                    holders.setdefault(ip, []).append(ref)
            if vm.power_state != "poweredOn":
                for ip in vm.last_known_ips or []:
                    a = _addr(ip)
                    if a is not None and a in net:
                        stale.setdefault(ip, []).append(ref)

        usable = net.num_addresses - 2 if net.version == 4 and net.prefixlen < 31 else net.num_addresses
        ips = []
        for ip, refs in holders.items():
            ips.append({"ip": ip, "state": "conflict" if len(refs) > 1 else "used", "vms": refs,
                        "stale_vms": stale.get(ip, [])})
        for ip, refs in stale.items():
            if ip not in holders:
                ips.append({"ip": ip, "state": "stale", "vms": [], "stale_vms": refs})
        ips.sort(key=lambda r: ipaddress.ip_address(r["ip"]))
        used = len(holders)
        result.append({
            "cidr": str(net),
            "first": str(net.network_address),
            "prefixlen": net.prefixlen,
            "version": net.version,
            "usable": usable,
            "used": used,
            "free": max(0, usable - used),
            "stale": sum(1 for r in ips if r["state"] == "stale"),
            "conflicts": sum(1 for r in ips if r["state"] == "conflict"),
            "used_pct": round(used / usable * 100, 1) if usable else 0,
            "ips": ips,
        })
    return result


@router.get("/subnets")
def subnets(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    vms = list(db.scalars(select(VM).where(VM.is_deleted.is_(False))))
    nets = get_settings().subnet_list
    # VMs with an IP outside every configured subnet, so nothing goes unnoticed.
    outside = []
    for vm in vms:
        if vm.power_state != "poweredOn":
            continue
        for ip in vm.ip_addresses or []:
            a = _addr(ip)
            if a is not None and a.version == 4 and not any(a in n for n in nets):
                outside.append({"ip": ip, "vm": {"id": vm.id, "name": vm.name}})
    return {"subnets": subnet_usage(vms, nets), "outside": outside}
