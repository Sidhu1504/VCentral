"""vCenter access: one batched PropertyCollector query per object type, plus creation-event lookup.

Only read operations are used, so a read-only vCenter role is sufficient.
"""
from __future__ import annotations

import logging
import ipaddress
from dataclasses import dataclass, field
from datetime import datetime

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl

from .config import Settings
from .db import to_utc_naive

log = logging.getLogger(__name__)

VM_PROPS = [
    "name",
    "config.instanceUuid",
    "config.uuid",
    "config.guestFullName",
    "config.guestId",
    "config.hardware.numCPU",
    "config.hardware.memoryMB",
    "config.createDate",
    "config.annotation",
    "config.template",
    "config.hardware.device",
    "runtime.powerState",
    "runtime.host",
    "runtime.bootTime",
    "guest.guestFullName",
    "guest.guestId",
    "guest.guestFamily",
    "guest.hostName",
    "guest.ipAddress",
    "guest.net",
    "guest.disk",
    "guest.toolsRunningStatus",
    "guest.toolsVersionStatus2",
    "summary.quickStats.overallCpuUsage",
    "summary.quickStats.guestMemoryUsage",
    "summary.quickStats.uptimeSeconds",
    "summary.storage.committed",
    "summary.storage.uncommitted",
    "parent",
    "parentVApp",
    "customValue",
    "datastore",
]

CREATION_EVENTS = ["VmCreatedEvent", "VmClonedEvent", "VmDeployedEvent", "VmRegisteredEvent"]

# Things people do to their own VMs; used to suggest an owner when the creation event is gone.
ACTIVITY_EVENTS = [
    "VmAcquiredTicketEvent", "VmAcquiredMksTicketEvent",  # opened the console
    "VmPoweredOnEvent", "VmPoweredOffEvent", "VmResettingEvent", "VmSuspendedEvent",
    "VmGuestRebootEvent", "VmGuestShutdownEvent", "VmReconfiguredEvent", "VmRenamedEvent",
]

LINUX_HINTS = (
    "linux", "rhel", "centos", "ubuntu", "debian", "sles", "suse", "oracle", "alma", "rocky",
    "fedora", "amazon", "photon", "coreos", "asianux", "mandriva", "turbolinux", "redhat",
)

GB = 1024 ** 3


@dataclass
class Inventory:
    vms: list[dict] = field(default_factory=list)
    vcenter_version: str | None = None
    templates_skipped: int = 0


def classify_os(guest_family: str | None, guest_id: str | None, full_name: str | None) -> str:
    if guest_family == "windowsGuest":
        return "windows"
    if guest_family == "linuxGuest":
        return "linux"
    text = f"{guest_id or ''} {full_name or ''}".lower()
    if "win" in text:
        return "windows"
    if any(h in text for h in LINUX_HINTS):
        return "linux"
    return "other"


def _usable_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_link_local or addr.is_loopback or addr.is_unspecified)


def _round(v: float | None, n: int = 1) -> float | None:
    return round(v, n) if v is not None else None


def describe_error(exc: Exception, settings: Settings) -> str:
    """Turn common vCenter connection problems into a message an admin can act on."""
    import socket
    import ssl as _ssl

    host = settings.vcenter_host
    if isinstance(exc, vim.fault.InvalidLogin):
        return f"vCenter rejected the username or password for {settings.vcenter_user} (check VCENTRAL_VCENTER_USER / _PASSWORD; passwords in vsphere.local expire after 90 days by default)"
    if isinstance(exc, vim.fault.NoPermission):
        missing = getattr(exc, "privilegeId", None) or "System.View"
        return (f"{settings.vcenter_user} logged in but has no permission ({missing}) on the vCenter inventory. "
                "In vCenter: Administration > Global Permissions > add this user with the vCentral role and "
                "tick 'Propagate to children'")
    if isinstance(exc, socket.gaierror):
        return f"Cannot find the address of '{host}' (DNS). Check VCENTRAL_VCENTER_HOST, or add it to /etc/hosts"
    if isinstance(exc, _ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return f"vCenter's certificate is not trusted by this server. Set VCENTRAL_VCENTER_VERIFY_SSL=false, or trust vCenter's root CA"
    if isinstance(exc, (ConnectionRefusedError, TimeoutError, socket.timeout)) or "timed out" in str(exc).lower():
        return f"Cannot connect to {host}:{settings.vcenter_port} (refused or timed out). Check the address and firewall"
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


class VCenterClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.si = None

    # -- connection -----------------------------------------------------------------------
    def connect(self):
        s = self.settings
        if not s.vcenter_host:
            raise RuntimeError("VCENTRAL_VCENTER_HOST is not configured")
        self.si = SmartConnect(
            host=s.vcenter_host,
            port=s.vcenter_port,
            user=s.vcenter_user,
            pwd=s.vcenter_password,
            disableSslCertValidation=not s.vcenter_verify_ssl,
        )
        log.info("Connected to vCenter %s", s.vcenter_host)

    def ensure_connected(self):
        if self.si is not None:
            try:
                if self.si.content.sessionManager.currentSession is not None:
                    return
            except Exception:  # session expired or network error
                pass
            self.disconnect()
        self.connect()

    def disconnect(self):
        if self.si is not None:
            try:
                Disconnect(self.si)
            except Exception:
                pass
            self.si = None

    # -- low level ------------------------------------------------------------------------
    def _retrieve(self, obj_type, props: list[str]) -> list[dict]:
        """Fetch `props` for every object of `obj_type`, dropping properties the server rejects."""
        content = self.si.content
        props = list(props)
        while True:
            try:
                return self._retrieve_once(content, obj_type, props)
            except vmodl.query.InvalidProperty as exc:
                bad = getattr(exc, "name", None)
                if bad in props:
                    log.warning("vCenter does not support property %s; skipping it", bad)
                    props.remove(bad)
                    continue
                raise

    @staticmethod
    def _retrieve_once(content, obj_type, props) -> list[dict]:
        view = content.viewManager.CreateContainerView(content.rootFolder, [obj_type], True)
        try:
            pc = vmodl.query.PropertyCollector
            spec = pc.FilterSpec(
                objectSet=[pc.ObjectSpec(
                    obj=view, skip=True,
                    selectSet=[pc.TraversalSpec(name="view", path="view", skip=False, type=vim.view.ContainerView)],
                )],
                propSet=[pc.PropertySpec(type=obj_type, pathSet=props, all=False)],
            )
            collector = content.propertyCollector
            result = collector.RetrievePropertiesEx([spec], pc.RetrieveOptions(maxObjects=500))
            out = []
            while result:
                for oc in result.objects:
                    row = {p.name: p.val for p in (oc.propSet or [])}
                    row["_moid"] = oc.obj._moId
                    out.append(row)
                if not result.token:
                    break
                result = collector.ContinueRetrievePropertiesEx(result.token)
            return out
        finally:
            view.Destroy()

    # -- inventory ------------------------------------------------------------------------
    def collect(self) -> Inventory:
        self.ensure_connected()
        content = self.si.content
        inv = Inventory(vcenter_version=f"{content.about.fullName}")

        hosts = {h["_moid"]: h for h in self._retrieve(vim.HostSystem, ["name", "parent", "summary.hardware.cpuMhz"])}
        compute = {c["_moid"]: c["name"] for c in self._retrieve(vim.ComputeResource, ["name"])}
        folders = {f["_moid"]: f for f in self._retrieve(vim.Folder, ["name", "parent"])}
        datacenters = {d["_moid"]: d["name"] for d in self._retrieve(vim.Datacenter, ["name"])}
        vapps = {a["_moid"]: a for a in self._retrieve(vim.VirtualApp, ["name", "parent", "parentFolder"])}
        datastores = {d["_moid"]: d["name"] for d in self._retrieve(vim.Datastore, ["name"])}

        field_names = {}
        cfm = getattr(content, "customFieldsManager", None)
        if cfm is not None:
            field_names = {f.key: f.name for f in (cfm.field or [])}

        def folder_path(parent) -> str | None:
            parts = []
            seen = set()
            obj = parent
            while obj is not None and obj._moId not in seen:
                seen.add(obj._moId)
                moid = obj._moId
                if moid in folders:
                    f = folders[moid]
                    parent_obj = f.get("parent")
                    if parent_obj is not None and parent_obj._moId in datacenters:
                        parts.append(datacenters[parent_obj._moId])  # skip hidden "vm" root folder
                        break
                    parts.append(f["name"])
                    obj = parent_obj
                elif moid in vapps:
                    parts.append(vapps[moid]["name"])
                    obj = vapps[moid].get("parentFolder") or vapps[moid].get("parent")
                else:
                    break
            return "/".join(reversed(parts)) or None

        for raw in self._retrieve(vim.VirtualMachine, VM_PROPS):
            if raw.get("config.template"):
                inv.templates_skipped += 1
                continue
            inv.vms.append(self._normalize(raw, hosts, compute, folder_path, datastores, field_names))
        return inv

    @staticmethod
    def _normalize(raw, hosts, compute, folder_path, datastores, field_names) -> dict:
        power = str(raw.get("runtime.powerState") or "unknown")
        on = power == "poweredOn"

        guest_os = raw.get("guest.guestFullName") or raw.get("config.guestFullName")
        guest_id = raw.get("guest.guestId") or raw.get("config.guestId")

        ips, macs = [], []
        # MACs from the virtual hardware are known even when VMware Tools is not running.
        for dev in raw.get("config.hardware.device") or []:
            if isinstance(dev, vim.vm.device.VirtualEthernetCard) and dev.macAddress:
                if dev.macAddress.lower() not in macs:
                    macs.append(dev.macAddress.lower())
        for nic in raw.get("guest.net") or []:
            if nic.macAddress and nic.macAddress.lower() not in macs:
                macs.append(nic.macAddress.lower())
            addrs = []
            if nic.ipConfig and nic.ipConfig.ipAddress:
                addrs = [a.ipAddress for a in nic.ipConfig.ipAddress]
            elif nic.ipAddress:
                addrs = list(nic.ipAddress)
            ips.extend(a for a in addrs if _usable_ip(a) and a not in ips)
        primary_ip = raw.get("guest.ipAddress")
        if primary_ip and not _usable_ip(primary_ip):
            primary_ip = None
        if primary_ip and primary_ip not in ips:
            ips.insert(0, primary_ip)
        if not primary_ip and ips:
            primary_ip = next((i for i in ips if ":" not in i), ips[0])

        disks = []
        for d in raw.get("guest.disk") or []:
            if not d.capacity:
                continue
            used_pct = (d.capacity - d.freeSpace) / d.capacity * 100
            disks.append({
                "path": d.diskPath,
                "capacity_gb": round(d.capacity / GB, 1),
                "free_gb": round(d.freeSpace / GB, 1),
                "used_pct": round(used_pct, 1),
            })

        host_ref = raw.get("runtime.host")
        host = hosts.get(host_ref._moId) if host_ref is not None else None
        cluster = None
        if host and host.get("parent") is not None:
            cluster = compute.get(host["parent"]._moId)

        num_cpu = raw.get("config.hardware.numCPU")
        memory_mb = raw.get("config.hardware.memoryMB")
        cpu_mhz = raw.get("summary.quickStats.overallCpuUsage") if on else None
        mem_mb = raw.get("summary.quickStats.guestMemoryUsage") if on else None
        cpu_pct = mem_pct = None
        host_mhz = host.get("summary.hardware.cpuMhz") if host else None
        if cpu_mhz is not None and num_cpu and host_mhz:
            cpu_pct = min(100.0, max(0.0, cpu_mhz / (num_cpu * host_mhz) * 100))
        if mem_mb is not None and memory_mb:
            mem_pct = min(100.0, max(0.0, mem_mb / memory_mb * 100))

        committed = raw.get("summary.storage.committed") or 0
        uncommitted = raw.get("summary.storage.uncommitted") or 0

        attrs = {}
        for cv in raw.get("customValue") or []:
            name = field_names.get(cv.key, str(cv.key))
            attrs[name] = getattr(cv, "value", None)

        parent = raw.get("parent") or raw.get("parentVApp")

        return {
            "uuid": raw.get("config.instanceUuid") or f"moid:{raw['_moid']}",
            "bios_uuid": raw.get("config.uuid"),
            "moid": raw["_moid"],
            "name": raw.get("name") or raw["_moid"],
            "power_state": power,
            "guest_os": guest_os,
            "guest_id": guest_id,
            "os_family": classify_os(raw.get("guest.guestFamily"), guest_id, guest_os),
            "hostname": raw.get("guest.hostName"),
            "primary_ip": primary_ip,
            "ip_addresses": ips,
            "mac_addresses": macs,
            "num_cpu": num_cpu,
            "memory_mb": memory_mb,
            "storage_committed_gb": round(committed / GB, 1),
            "storage_provisioned_gb": round((committed + uncommitted) / GB, 1),
            "guest_disks": disks,
            "tools_status": raw.get("guest.toolsRunningStatus"),
            "tools_version_status": raw.get("guest.toolsVersionStatus2"),
            "esxi_host": host.get("name") if host else None,
            "cluster": cluster,
            "folder": folder_path(parent),
            "datastores": [datastores.get(d._moId, d._moId) for d in raw.get("datastore") or []],
            "annotation": raw.get("config.annotation") or None,
            "custom_attributes": attrs,
            "cpu_usage_mhz": cpu_mhz,
            "cpu_pct": _round(cpu_pct),
            "mem_active_mb": mem_mb,
            "mem_pct": _round(mem_pct),
            "disk_used_pct_max": max((d["used_pct"] for d in disks), default=None) if on else None,
            "uptime_seconds": raw.get("summary.quickStats.uptimeSeconds") if on else None,
            "vcenter_created_at": to_utc_naive(raw.get("config.createDate")),
            "boot_time": to_utc_naive(raw.get("runtime.bootTime")) if on else None,
        }

    # -- owners ---------------------------------------------------------------------------
    def user_activity(self) -> dict[str, dict[str, dict]]:
        """VM moid -> {user: {"count": n, "last": ts, "kinds": set}} from the activity events vCenter still keeps."""
        self.ensure_connected()
        em = self.si.content.eventManager
        collector = em.CreateCollectorForEvents(vim.event.EventFilterSpec(eventTypeId=ACTIVITY_EVENTS))
        result: dict[str, dict[str, dict]] = {}
        try:
            collector.RewindCollector()
            while True:
                events = collector.ReadNextEvents(1000)
                if not events:
                    break
                for ev in events:
                    vm_ref = ev.vm.vm if ev.vm else None
                    user = (ev.userName or "").strip()
                    if vm_ref is None or not user:
                        continue
                    entry = result.setdefault(vm_ref._moId, {}).setdefault(user, {"count": 0, "last": None, "kinds": set()})
                    entry["count"] += 1
                    ts = to_utc_naive(ev.createdTime)
                    if entry["last"] is None or (ts and ts > entry["last"]):
                        entry["last"] = ts
                    entry["kinds"].add(type(ev).__name__.split(".")[-1])
        finally:
            try:
                collector.DestroyCollector()
            except Exception:
                pass
        return result

    def creation_events(self) -> dict[str, dict]:
        """Map VM moid -> {user, ts, event} from the creation events still retained by vCenter.

        Created/cloned/deployed events win over registration events; the earliest one wins otherwise.
        """
        self.ensure_connected()
        em = self.si.content.eventManager
        collector = em.CreateCollectorForEvents(vim.event.EventFilterSpec(eventTypeId=CREATION_EVENTS))
        result: dict[str, dict] = {}
        try:
            collector.RewindCollector()
            while True:
                events = collector.ReadNextEvents(1000)
                if not events:
                    break
                for ev in events:
                    vm_ref = ev.vm.vm if ev.vm else None
                    user = (ev.userName or "").strip()
                    if vm_ref is None or not user:
                        continue
                    kind = type(ev).__name__.split(".")[-1]
                    rank = 1 if kind == "VmRegisteredEvent" else 0
                    ts: datetime | None = to_utc_naive(ev.createdTime)
                    current = result.get(vm_ref._moId)
                    if current is None or (rank, ts) < (current["rank"], current["ts"]):
                        result[vm_ref._moId] = {"user": user, "ts": ts, "event": kind, "rank": rank}
        finally:
            try:
                collector.DestroyCollector()
            except Exception:
                pass
        return result
