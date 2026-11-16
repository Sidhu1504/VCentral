"""Find addresses that are in use on the network but are not VMs (gateways, printers, firewalls, laptops...).

From the vCentral server: one ICMP echo per address (unprivileged "ping" socket, no root needed), falling back to
TCP connection attempts when ICMP sockets are not allowed; then the kernel's ARP table, which also lists devices on
the same subnet that block ping. Results are merged with earlier scans so a device that was briefly off is still
shown, with when it was last seen.
"""
import asyncio
import ipaddress
import logging
import socket
import struct
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from .db import iso, utcnow
from .state import get_state, set_state

log = logging.getLogger("vcentral.netscan")
MAX_ADDRESSES = 4096  # per scan, across all subnets
CONCURRENCY = 128
TIMEOUT = 1.0
TCP_PORTS = (22, 80, 443, 445, 3389, 135, 53, 8080)
FORGET_AFTER = timedelta(days=7)
ARP_FILE = "/proc/net/arp"


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def icmp_allowed() -> bool:
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
        return True
    except OSError:
        return False


async def _ping(ip: str, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    sock.setblocking(False)
    try:
        body = struct.pack("!d", time.time()) + b"vcentral"
        header = struct.pack("!BBHHH", 8, 0, 0, 0, 1)  # echo request; the kernel sets the identifier
        packet = struct.pack("!BBHHH", 8, 0, _checksum(header + body), 0, 1) + body
        await loop.sock_sendto(sock, packet, (ip, 0))
        deadline = loop.time() + timeout
        while True:
            left = deadline - loop.time()
            if left <= 0:
                return False
            data = await asyncio.wait_for(loop.sock_recv(sock, 1024), left)
            if data and data[0] == 0:  # echo reply (the socket only receives replies to its own identifier)
                return True
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        sock.close()


async def _tcp(ip: str, timeout: float) -> bool:
    async def one(port):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
            w.close()
            return True
        except ConnectionRefusedError:
            return True  # something answered "closed": the address is in use
        except (OSError, asyncio.TimeoutError):
            return False
    results = await asyncio.gather(*(one(p) for p in TCP_PORTS))
    return any(results)


def read_arp(path: str = ARP_FILE) -> dict[str, str]:
    """IP -> MAC for complete entries in the kernel's neighbour table."""
    out = {}
    try:
        with open(path) as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[2] != "0x0" and parts[3] != "00:00:00:00:00:00":
                    out[parts[0]] = parts[3].lower()
    except OSError:
        pass
    return out


async def sweep(addresses: list[str], timeout: float = TIMEOUT) -> dict[str, dict]:
    use_icmp = icmp_allowed()
    probe = _ping if use_icmp else _tcp
    sem = asyncio.Semaphore(CONCURRENCY)

    async def check(ip):
        async with sem:
            return ip, await probe(ip, timeout)

    found = {ip: {"via": "ping" if use_icmp else "tcp"} for ip, alive in await asyncio.gather(*(check(a) for a in addresses)) if alive}
    wanted = set(addresses)
    for ip, mac in read_arp().items():
        if ip in wanted:
            found.setdefault(ip, {"via": "arp"})["mac"] = mac
    return found


def scan_targets(nets) -> list[str]:
    out = []
    for net in nets:
        if net.version != 4:
            continue
        for a in net.hosts():
            out.append(str(a))
            if len(out) >= MAX_ADDRESSES:
                return out
    return out


def run_scan(db: Session, nets) -> dict:
    """Scan the configured subnets and merge with what earlier scans saw."""
    addresses = scan_targets(nets)
    started = time.monotonic()
    found = asyncio.run(sweep(addresses)) if addresses else {}
    now = utcnow()
    previous = get_state(db, "ip_scan").get("devices", {})
    devices = {}
    for ip, info in previous.items():
        try:
            last = info.get("last_seen")
            if last and now - _parse(last) < FORGET_AFTER:
                devices[ip] = info
        except ValueError:
            pass
    for ip, info in found.items():
        devices[ip] = {"last_seen": iso(now), "mac": info.get("mac") or devices.get(ip, {}).get("mac"), "via": info["via"]}
    state = {"finished_at": iso(now), "duration_seconds": round(time.monotonic() - started, 1),
             "scanned": len(addresses), "answered": len(found), "method": "ping" if icmp_allowed() else "tcp",
             "devices": devices}
    set_state(db, "ip_scan", state)
    db.commit()
    log.info("IP scan: %s of %s addresses answered in %ss", len(found), len(addresses), state["duration_seconds"])
    return state


def _parse(value: str):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

