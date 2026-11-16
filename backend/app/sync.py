"""Inventory sync: compares vCenter with the database, records lifecycle history and metric samples."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import iso, utcnow
from .agents import relink_unlinked
from .models import VM, AgentMetric, AgentMetricHourly, VMHistory, VMMetric, VMMetricHourly
from .state import get_state, set_state
from .vcenter import VCenterClient

log = logging.getLogger(__name__)

# Fields copied as-is from the collector into the VM row.
COPY_FIELDS = (
    "bios_uuid", "moid", "name", "power_state", "guest_os", "guest_id", "os_family", "hostname",
    "primary_ip", "ip_addresses", "mac_addresses", "num_cpu", "memory_mb", "storage_committed_gb",
    "storage_provisioned_gb", "guest_disks", "tools_status", "tools_version_status", "esxi_host",
    "cluster", "folder", "datastores", "annotation", "custom_attributes", "cpu_usage_mhz", "cpu_pct",
    "mem_active_mb", "mem_pct", "disk_used_pct_max", "uptime_seconds", "vcenter_created_at", "boot_time",
)

POWER_EVENTS = {"poweredOn": "powered_on", "poweredOff": "powered_off", "suspended": "suspended"}

# How long to keep retrying the creation-event lookup for a new VM whose event has not appeared yet.
OWNER_RETRY_WINDOW = timedelta(minutes=30)


def _history(db: Session, vm: VM, kind: str, message: str, ts, **data) -> None:
    db.add(VMHistory(vm_id=vm.id, ts=ts, kind=kind, message=message, data=data))


def run_sync(db: Session, client: VCenterClient, settings: Settings) -> dict:
    started = time.monotonic()
    now = utcnow()
    inventory = client.collect()

    existing = {vm.uuid: vm for vm in db.scalars(select(VM))}
    active_before = sum(1 for vm in existing.values() if not vm.is_deleted)
    if not inventory.vms and active_before:
        # Most likely a permissions problem or vCenter hiccup; never mass-delete on an empty answer.
        raise RuntimeError("vCenter returned no VMs; skipping this sync to avoid marking everything deleted")

    initial_sync = not existing
    seen: set[str] = set()
    stats = defaultdict(int)

    for item in inventory.vms:
        uuid = item["uuid"]
        if uuid in seen:  # duplicate instance UUID (rare, e.g. copied VMX); keep the first one
            continue
        seen.add(uuid)
        vm = existing.get(uuid)

        if vm is None:
            vm = VM(uuid=uuid, first_seen_at=now, ip_addresses=[], last_known_ips=[], mac_addresses=[],
                    guest_disks=[], datastores=[], custom_attributes={})
            for f in COPY_FIELDS:
                setattr(vm, f, item[f])
            db.add(vm)
            db.flush()
            if initial_sync:
                _history(db, vm, "discovered", f"{vm.name} discovered in vCenter", now)
            else:
                _history(db, vm, "created", f"{vm.name} appeared in vCenter", now)
                stats["created"] += 1
        else:
            if vm.is_deleted:
                vm.is_deleted = False
                vm.deleted_at = None
                _history(db, vm, "reappeared", f"{item['name']} is back in vCenter", now)
            _detect_changes(db, vm, item, now, stats)
            for f in COPY_FIELDS:
                setattr(vm, f, item[f])

        if item["ip_addresses"]:
            vm.last_known_ips = item["ip_addresses"]
        vm.last_seen_at = now
        vm.updated_at = now
        _apply_attribute_owner(vm, settings.owner_attribute)

        if vm.power_state == "poweredOn" and (vm.cpu_pct is not None or vm.mem_pct is not None):
            db.add(VMMetric(vm_id=vm.id, ts=now, cpu_pct=vm.cpu_pct, cpu_mhz=vm.cpu_usage_mhz,
                            mem_pct=vm.mem_pct, mem_mb=vm.mem_active_mb,
                            disk_used_pct_max=vm.disk_used_pct_max))

    for uuid, vm in existing.items():
        if uuid not in seen and not vm.is_deleted:
            vm.is_deleted = True
            vm.deleted_at = now
            vm.power_state = "deleted"
            vm.updated_at = now
            _history(db, vm, "deleted", f"{vm.name} no longer exists in vCenter", now)
            stats["deleted"] += 1

    db.flush()
    stats["owners_found"] = _resolve_owners_from_events(db, client, now)
    stats["agents_linked"] = relink_unlinked(db)

    summary = {
        "status": "ok",
        "last_finished": iso(utcnow()),
        "duration_seconds": round(time.monotonic() - started, 2),
        "vm_count": len(seen),
        "templates_skipped": inventory.templates_skipped,
        "vcenter_version": inventory.vcenter_version,
        **stats,
    }
    set_state(db, "sync", summary)
    db.commit()
    return summary


def _detect_changes(db: Session, vm: VM, item: dict, now, stats) -> None:
    if item["name"] != vm.name:
        _history(db, vm, "renamed", f"Renamed from {vm.name} to {item['name']}", now,
                 old=vm.name, new=item["name"])
    if item["power_state"] != vm.power_state and item["power_state"] in POWER_EVENTS:
        kind = POWER_EVENTS[item["power_state"]]
        _history(db, vm, kind, f"{item['name']} {kind.replace('_', ' ')}", now, previous=vm.power_state)
        stats[kind] += 1
    new_ip = item["primary_ip"]
    old_ip = vm.primary_ip or (vm.last_known_ips[0] if vm.last_known_ips else None)
    if new_ip and old_ip and new_ip != old_ip:
        _history(db, vm, "ip_changed", f"IP changed from {old_ip} to {new_ip}", now, old=old_ip, new=new_ip)
        stats["ip_changed"] += 1
    if (vm.num_cpu, vm.memory_mb) != (item["num_cpu"], item["memory_mb"]) and vm.num_cpu is not None:
        _history(db, vm, "reconfigured",
                 f"Resized from {vm.num_cpu} vCPU / {vm.memory_mb} MB to {item['num_cpu']} vCPU / {item['memory_mb']} MB",
                 now, old_cpu=vm.num_cpu, new_cpu=item["num_cpu"], old_mem=vm.memory_mb, new_mem=item["memory_mb"])


def _apply_attribute_owner(vm: VM, attribute: str) -> None:
    if vm.owner_source == "manual" or not attribute:
        return
    value = (vm.custom_attributes or {}).get(attribute)
    value = value.strip() if isinstance(value, str) else None
    if value:
        vm.owner, vm.owner_source = value, "attribute"
    elif vm.owner_source == "attribute":  # attribute was cleared in vCenter
        vm.owner, vm.owner_source, vm.owner_checked = None, None, False


def _resolve_owners_from_events(db: Session, client: VCenterClient, now) -> int:
    pending = list(db.scalars(select(VM).where(
        VM.is_deleted.is_(False), VM.owner.is_(None), VM.owner_checked.is_(False))))
    if not pending:
        return 0
    try:
        events = client.creation_events()
    except Exception:
        log.exception("Could not read creation events; will retry on the next sync")
        return 0
    found = 0
    for vm in pending:
        ev = events.get(vm.moid)
        if ev:
            vm.owner, vm.owner_source, vm.owner_checked = ev["user"], "event", True
            found += 1
        elif now - vm.first_seen_at > OWNER_RETRY_WINDOW:
            vm.owner_checked = True  # event expired from vCenter; needs a manual owner
        # otherwise: new VM whose creation event may not be posted yet, retry next sync
    return found


# -- metrics housekeeping -------------------------------------------------------------------

def _values(samples, attr):
    return [v for v in (getattr(s, attr) for s in samples) if v is not None]


def _avg(values):
    return round(sum(values) / len(values), 1) if values else None


def _vm_hour(owner_id, start, samples):
    cpu, mem = _values(samples, "cpu_pct"), _values(samples, "mem_pct")
    return VMMetricHourly(vm_id=owner_id, ts=start, samples=len(samples), cpu_pct_avg=_avg(cpu),
                          cpu_pct_max=max(cpu, default=None), mem_pct_avg=_avg(mem), mem_pct_max=max(mem, default=None),
                          disk_used_pct_max=max(_values(samples, "disk_used_pct_max"), default=None))


def _agent_hour(owner_id, start, samples):
    cpu, mem = _values(samples, "cpu_pct"), _values(samples, "mem_pct")
    return AgentMetricHourly(
        agent_id=owner_id, ts=start, samples=len(samples), cpu_pct_avg=_avg(cpu), cpu_pct_max=max(cpu, default=None),
        mem_pct_avg=_avg(mem), mem_pct_max=max(mem, default=None),
        disk_used_pct_max=max(_values(samples, "disk_used_pct_max"), default=None),
        **{c: _avg(_values(samples, c)) for c in ("disk_read_bps", "disk_write_bps", "net_rx_bps", "net_tx_bps")})


def _rollup(db: Session, raw, owner_attr: str, build, state_key: str) -> int:
    """Aggregate each complete hour of raw samples into one hourly row per owner. Returns hours processed."""
    current_hour = utcnow().replace(minute=0, second=0, microsecond=0)
    done_until = get_state(db, state_key).get("done_until")
    if done_until:
        start = datetime.fromisoformat(done_until)
    else:
        first_raw = db.scalar(select(func.min(raw.ts)))
        if first_raw is None:
            return 0
        start = first_raw.replace(minute=0, second=0, microsecond=0)

    hours = 0
    while start < current_hour:
        end = start + timedelta(hours=1)
        groups: dict[int, list] = defaultdict(list)
        for r in db.scalars(select(raw).where(raw.ts >= start, raw.ts < end)):
            groups[getattr(r, owner_attr)].append(r)
        for owner_id, samples in groups.items():
            db.add(build(owner_id, start, samples))
        set_state(db, state_key, {"done_until": end.isoformat()})
        db.commit()
        start = end
        hours += 1
    return hours


def rollup_hourly(db: Session) -> int:
    return (_rollup(db, VMMetric, "vm_id", _vm_hour, "rollup")
            + _rollup(db, AgentMetric, "agent_id", _agent_hour, "rollup_agents"))


def prune(db: Session, settings: Settings) -> None:
    now = utcnow()
    raw_cutoff = now - timedelta(days=settings.metrics_raw_retention_days)
    hourly_cutoff = now - timedelta(days=settings.metrics_hourly_retention_days)
    db.execute(delete(VMMetric).where(VMMetric.ts < raw_cutoff))
    db.execute(delete(VMMetricHourly).where(VMMetricHourly.ts < hourly_cutoff))
    db.execute(delete(AgentMetric).where(AgentMetric.ts < raw_cutoff))
    db.execute(delete(AgentMetricHourly).where(AgentMetricHourly.ts < hourly_cutoff))
    db.execute(delete(VMHistory).where(VMHistory.ts < now - timedelta(days=settings.history_retention_days)))
    db.commit()


def record_sync_failure(db: Session, error: str) -> None:
    db.rollback()
    previous = get_state(db, "sync")
    previous.update({"status": "error", "last_error": error, "last_error_at": iso(utcnow())})
    set_state(db, "sync", previous)
    db.commit()
