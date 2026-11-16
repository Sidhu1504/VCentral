"""Agent helpers shared by the API and the worker: VM matching, managed flag, status, install info."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import ssl
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import utcnow
from .models import VM, Agent
from .runtime import offline_after_seconds

log = logging.getLogger(__name__)

# Platforms the server can hand out, and the file names agent/build.sh produces.
PLATFORMS = {
    "linux-amd64": "vcentral-agent-linux-amd64",
    "linux-386": "vcentral-agent-linux-386",
    "linux-arm64": "vcentral-agent-linux-arm64",
    "windows-amd64": "vcentral-agent-windows-amd64.exe",
    "windows-386": "vcentral-agent-windows-386.exe",
    "windows-legacy-amd64": "vcentral-agent-windows-legacy-amd64.exe",
    "windows-legacy-386": "vcentral-agent-windows-legacy-386.exe",
}


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


# -- identity matching -----------------------------------------------------------------------------

def _swap_bytes(group: str) -> str:
    return "".join(reversed([group[i:i + 2] for i in range(0, len(group), 2)]))


def uuid_variants(value: str | None) -> set[str]:
    """A BIOS UUID as vCenter reports it and as some guests report it.

    SMBIOS stores the first three groups little-endian, and depending on the hardware version and OS the
    guest may show them byte-swapped compared with vCenter's config.uuid. Both forms are accepted.
    """
    if not value:
        return set()
    u = value.strip().strip("{}").lower()
    parts = u.split("-")
    if len(parts) != 5 or [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return {u}
    swapped = "-".join([_swap_bytes(parts[0]), _swap_bytes(parts[1]), _swap_bytes(parts[2]), parts[3], parts[4]])
    return {u, swapped}


def find_vm(db: Session, agent: Agent) -> tuple[VM | None, str | None]:
    vms = list(db.scalars(select(VM).where(VM.is_deleted.is_(False))))
    variants = uuid_variants(agent.bios_uuid)
    if variants:
        hits = [vm for vm in vms if vm.bios_uuid and vm.bios_uuid.lower() in variants]
        if len(hits) == 1:
            return hits[0], "bios_uuid"
    macs = {m.lower() for m in (agent.macs or []) if m}
    if macs:
        hits = [vm for vm in vms if macs & {m.lower() for m in (vm.mac_addresses or [])}]
        if len(hits) == 1:
            return hits[0], "mac"
    return None, None


def refresh_managed(db: Session, vm_ids) -> None:
    """VM.managed is true while at least one active agent is linked to the VM."""
    db.flush()  # the session does not autoflush; pending agent changes must be visible to the query
    for vm_id in {v for v in vm_ids if v}:
        vm = db.get(VM, vm_id)
        if vm is not None:
            vm.managed = db.scalar(select(Agent.id).where(Agent.vm_id == vm_id, Agent.status == "active")
                                   .limit(1)) is not None


def link_agent(db: Session, agent: Agent) -> None:
    """Attach an agent to its VM automatically, unless an operator linked it by hand."""
    if agent.link_source == "manual":
        return
    old = agent.vm_id
    vm, source = find_vm(db, agent)
    agent.vm_id, agent.link_source = (vm.id, source) if vm else (None, None)
    db.flush()
    refresh_managed(db, [old, agent.vm_id])


def relink_unlinked(db: Session) -> int:
    """Called after each vCenter sync: new VMs may now match agents that enrolled earlier."""
    linked = 0
    for agent in db.scalars(select(Agent).where(Agent.status == "active", Agent.vm_id.is_(None),
                                                Agent.link_source.is_(None))):
        link_agent(db, agent)
        linked += agent.vm_id is not None
    return linked


# -- status ----------------------------------------------------------------------------------------

def is_online(agent: Agent) -> bool:
    if agent.status != "active" or agent.last_seen_at is None:
        return False
    return agent.last_seen_at >= utcnow() - timedelta(seconds=offline_after_seconds())


def agent_state(agent: Agent) -> str:
    if agent.status != "active":
        return "revoked"
    return "online" if is_online(agent) else "offline"


def representative_agents(db: Session) -> dict[int, Agent]:
    """The agent that stands for each VM: an online one if there is one, otherwise the most recent."""
    best: dict[int, Agent] = {}
    for a in db.scalars(select(Agent).where(Agent.status == "active", Agent.vm_id.is_not(None))):
        cur = best.get(a.vm_id)
        if cur is None or (is_online(a), a.last_seen_at or a.enrolled_at) > (is_online(cur), cur.last_seen_at or cur.enrolled_at):
            best[a.vm_id] = a
    return best


# -- install information ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _cert_fingerprint(path: str, mtime: float) -> str | None:
    try:
        der = ssl.PEM_cert_to_DER_cert(Path(path).read_text())
    except (OSError, ValueError) as exc:
        log.warning("Cannot read TLS certificate %s: %s", path, exc)
        return None
    return hashlib.sha256(der).hexdigest()


def certificate_fingerprint() -> str | None:
    s = get_settings()
    if not s.agent_pin_certificate or not s.tls_cert_file:
        return None
    p = Path(s.tls_cert_file)
    if not p.is_file():
        return None
    return _cert_fingerprint(str(p), p.stat().st_mtime)


def manifest() -> dict:
    s = get_settings()
    if not s.agent_dir:
        return {}
    try:
        return json.loads((Path(s.agent_dir) / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def latest_agent_version() -> str | None:
    return manifest().get("version")
