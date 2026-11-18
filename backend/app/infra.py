"""vCenter Server and ESXi host details for the Infrastructure page.

* ESXi hosts: read through the vSphere API (pyVmomi), monitoring only.
* vCenter: product details from the vSphere API, plus appliance health and its services (start / stop / restart)
  through the vCenter REST API (/api on vSphere 7+, /rest on 6.5-6.7). Managing services needs an SSO account that
  may administer the appliance (e.g. a member of SystemConfiguration.Administrators); set VCENTRAL_APPLIANCE_USER /
  VCENTRAL_APPLIANCE_PASSWORD when vCentral's normal vCenter account is not allowed to.
"""
from __future__ import annotations

import base64
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from pyVmomi import vim

from .config import Settings

log = logging.getLogger(__name__)

# Friendly names for the vCenter services people look for most; anything else shows its service ID.
SERVICE_NAMES = {
    "vpxd": "vCenter Server", "vsphere-ui": "vSphere Client", "vmware-vpostgres": "PostgreSQL database",
    "rhttpproxy": "Reverse proxy", "envoy": "Envoy proxy", "vapi-endpoint": "vAPI endpoint (REST API)",
    "vmware-stsd": "Security Token Service", "vmdird": "Directory service", "lookupsvc": "Lookup service",
    "vpxd-svcs": "vCenter services (inventory, tagging)", "content-library": "Content library",
    "eam": "ESX Agent Manager", "sps": "Storage profiles (SPS)", "vlcm": "vSphere Lifecycle Manager",
    "updatemgr": "Update Manager", "vmware-updatemgr": "Update Manager", "vsan-health": "vSAN health",
    "perfcharts": "Performance charts", "vstats": "Stats service", "wcp": "Workload management (Kubernetes)",
    "trustmanagement": "Trust management", "certificatemanagement": "Certificate management",
    "certificateauthority": "Certificate authority", "analytics": "Analytics (CEIP)", "applmgmt": "Appliance management",
    "cis-license": "License service", "sca": "Service control agent", "statsmonitor": "Appliance stats monitor",
    "vmcam": "Authentication proxy", "rbd": "Auto Deploy", "imagebuilder": "Image Builder", "netdumper": "ESXi dump collector",
    "vmware-postgres-archiver": "PostgreSQL archiver", "infraprofile": "Infrastructure profiles", "pschealth": "PSC health",
    "hvc": "Hybrid vCenter", "topologysvc": "Topology service", "observability": "Observability", "vtsdb": "Time-series DB",
    "vmonapi": "Service lifecycle API", "vsm": "vService manager", "vcha": "vCenter HA", "vdtc": "Distributed tracing",
    "vpxd-svcs-proxy": "vpxd services proxy", "smartcardauth": "Smart card authentication", "lwavmomi": "VMOMI proxy",
    "lookup-svc": "Lookup service", "rhttpproxy-sidecar": "Reverse proxy sidecar",
}
# Stopping or restarting these interrupts vCenter itself (and vCentral's connection to it).
CORE_SERVICES = {"vpxd", "vmware-vpostgres", "rhttpproxy", "envoy", "vapi-endpoint", "vmware-stsd", "vmdird",
                 "lookupsvc", "vpxd-svcs", "vmonapi", "sca", "applmgmt"}
HEALTH_PARTS = ("system", "load", "mem", "storage", "swap", "database", "applmgmt", "software-packages")


class ApplianceError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class ApplianceClient:
    """Minimal vCenter REST client (stdlib only). Tries the vSphere 7+ /api paths, falls back to /rest."""

    def __init__(self, settings: Settings, timeout: float = 20):
        self.base = f"https://{settings.vcenter_host}:{settings.vcenter_port}"
        self.user = settings.appliance_user or settings.vcenter_user
        self.password = settings.appliance_password or settings.vcenter_password
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        if not settings.vcenter_verify_ssl:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.token: str | None = None
        self.legacy = False  # True on vSphere 6.5 / 6.7 (/rest)

    def _call(self, method: str, path: str, headers: dict | None = None, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else None
            except ValueError:
                payload = None
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApplianceError(f"Cannot reach the vCenter REST API: {getattr(exc, 'reason', exc)}")

    def login(self) -> None:
        basic = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        status, payload = self._call("POST", "/api/session", {"Authorization": f"Basic {basic}"})
        if status in (200, 201) and isinstance(payload, str):
            self.token = payload
            return
        if status == 404:  # vSphere 6.x
            status, payload = self._call("POST", "/rest/com/vmware/cis/session", {"Authorization": f"Basic {basic}"})
            if status == 200 and isinstance(payload, dict):
                self.token, self.legacy = payload.get("value"), True
                return
        if status in (401, 403):
            raise ApplianceError("vCenter did not accept the login for its REST API "
                                 f"({self.user}). Check the account, or set VCENTRAL_APPLIANCE_USER / _PASSWORD.", 401)
        raise ApplianceError(f"vCenter REST API login failed (HTTP {status})")

    def logout(self) -> None:
        if self.token:
            try:
                self._call("DELETE", "/rest/com/vmware/cis/session" if self.legacy else "/api/session", self._auth())
            except ApplianceError:
                pass
            self.token = None

    def _auth(self) -> dict:
        return {"vmware-api-session-id": self.token or ""}

    def get(self, api_path: str, rest_path: str | None = None):
        if not self.token:
            self.login()
        path = (rest_path or api_path.replace("/api/", "/rest/", 1)) if self.legacy else api_path
        status, payload = self._call("GET", path, self._auth())
        if status == 200:
            return payload.get("value") if self.legacy and isinstance(payload, dict) and "value" in payload else payload
        raise _http_error(status, payload)

    def services(self) -> list[dict]:
        raw = self.get("/api/vcenter/services")
        items = raw.items() if isinstance(raw, dict) else ((x.get("key"), x.get("value")) for x in raw or [])
        out = []
        for sid, info in items:
            info = info or {}
            out.append({"id": sid, "name": SERVICE_NAMES.get(sid, sid), "state": str(info.get("state", "")).lower(),
                        "health": str(info.get("health", "")).lower() or None,
                        "startup": str(info.get("startup_type", "")).lower() or None,
                        "core": sid in CORE_SERVICES})
        out.sort(key=lambda s: (not s["core"], s["state"] == "started", s["name"].lower()))
        return out

    def service_action(self, service: str, action: str) -> None:
        if not self.token:
            self.login()
        sid = urllib.parse.quote(service, safe="")
        path = f"/rest/vcenter/services/{sid}/{action}" if self.legacy else f"/api/vcenter/services/{sid}?action={action}"
        try:
            status, payload = self._call("POST", path, self._auth())
        except ApplianceError:
            if service in CORE_SERVICES:  # restarting the API's own front end can cut the reply
                return
            raise
        if status not in (200, 204):
            raise _http_error(status, payload)


def _http_error(status: int, payload) -> ApplianceError:
    msg = ""
    if isinstance(payload, dict):
        val = payload.get("value", payload)
        msgs = (val or {}).get("messages") if isinstance(val, dict) else None
        if msgs:
            msg = "; ".join(m.get("default_message", "") for m in msgs if isinstance(m, dict))
    if status in (401, 403):
        return ApplianceError("The vCenter account is not allowed to do this. Managing vCenter services needs an SSO "
                              "account that may administer the appliance (e.g. a member of SystemConfiguration."
                              "Administrators); set VCENTRAL_APPLIANCE_USER / VCENTRAL_APPLIANCE_PASSWORD."
                              + (f" ({msg})" if msg else ""), 403)
    if status == 404:
        return ApplianceError("This vCenter does not offer that API" + (f": {msg}" if msg else ""), 404)
    return ApplianceError(f"vCenter REST API error (HTTP {status})" + (f": {msg}" if msg else ""))


def _number(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def vcenter_overview(client, settings: Settings) -> dict:
    """vCenter product details (vSphere API) and appliance version / uptime / health / services (REST API)."""
    out: dict = {"host": settings.vcenter_host, "about": None, "appliance": None, "health": {}, "services": None,
                 "errors": {}}
    try:
        client.ensure_connected()
        a = client.si.content.about
        out["about"] = {"name": a.fullName, "version": a.version, "build": a.build, "os_type": a.osType,
                        "api_version": a.apiVersion, "instance_uuid": getattr(a, "instanceUuid", None)}
        try:
            out["about"]["server_time"] = client.si.CurrentTime().astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        out["errors"]["about"] = f"vSphere API: {getattr(exc, 'msg', None) or exc}"

    app = ApplianceClient(settings)
    try:
        try:
            v = app.get("/api/appliance/system/version") or {}
            up = app.get("/api/appliance/system/uptime")
            out["appliance"] = {"product": v.get("product"), "version": v.get("version"), "build": v.get("build"),
                                "type": v.get("type"), "release_date": v.get("releasedate"),
                                "install_time": v.get("install_time"),
                                "uptime_seconds": _number(up)}
        except ApplianceError as exc:
            out["errors"]["appliance"] = str(exc)
            if exc.status == 401:
                raise
        for part in HEALTH_PARTS:
            try:
                out["health"][part] = str(app.get(f"/api/appliance/health/{part}")).lower()
            except ApplianceError:
                continue
        try:
            out["services"] = app.services()
        except ApplianceError as exc:
            out["errors"]["services"] = str(exc)
    except ApplianceError as exc:
        out["errors"]["appliance"] = str(exc)
    finally:
        app.logout()
    return out


# -- ESXi hosts ---------------------------------------------------------------------------------------

HOST_PROPS = ["name", "parent", "overallStatus", "summary.hardware", "summary.quickStats", "summary.config.product",
              "runtime.connectionState", "runtime.powerState", "runtime.inMaintenanceMode", "runtime.bootTime",
              "runtime.healthSystemRuntime.systemHealthInfo.numericSensorInfo", "datastore", "vm",
              "config.network.vnic", "triggeredAlarmState", "config.service.service"]
SENSOR_UNITS = {"degrees c": "°C", "rpm": "RPM", "watts": "W", "volts": "V", "amps": "A", "percent": "%"}
PHYSICAL_SENSORS = {"temperature", "fan", "power", "voltage", "current"}
_cache: dict = {}


def _sensor(s) -> dict:
    unit = (s.baseUnits or "").strip()
    value = s.currentReading * (10 ** (s.unitModifier or 0)) if s.currentReading is not None else None
    state = getattr(getattr(s, "healthState", None), "key", None) or "unknown"
    return {"name": s.name, "type": s.sensorType, "state": state.lower(),
            "reading": round(value, 1) if value is not None else None,
            "unit": SENSOR_UNITS.get(unit.lower(), unit)}


def host_details(client, max_age: float = 20) -> list[dict]:
    """Every ESXi host with hardware, versions, live usage, datastores, sensors, alarms and services (read only)."""
    hit = _cache.get("hosts")
    if hit and time.monotonic() - hit[0] < max_age:
        return hit[1]
    client.ensure_connected()
    rows = client._retrieve(vim.HostSystem, HOST_PROPS)
    ds_rows = {d["_moid"]: d for d in client._retrieve(vim.Datastore, ["summary.name", "summary.capacity",
                                                                       "summary.freeSpace", "summary.type",
                                                                       "summary.accessible"])}
    vm_power = {v["_moid"]: str(v.get("runtime.powerState")) for v in client._retrieve(vim.VirtualMachine,
                                                                                         ["runtime.powerState"])}
    now = datetime.now(timezone.utc)
    hosts = []
    for h in rows:
        hw, qs = h.get("summary.hardware"), h.get("summary.quickStats")
        product = h.get("summary.config.product")
        cpu_total = (hw.cpuMhz * hw.numCpuCores) if hw else None
        mem_total_mb = round(hw.memorySize / 1048576) if hw else None
        cpu_used = getattr(qs, "overallCpuUsage", None)
        mem_used = getattr(qs, "overallMemoryUsage", None)
        boot = h.get("runtime.bootTime")
        parent = h.get("parent")
        datastores = []
        for ref in h.get("datastore") or []:
            d = ds_rows.get(ref._moId)
            if not d:
                continue
            cap, free = d.get("summary.capacity") or 0, d.get("summary.freeSpace") or 0
            datastores.append({"name": d.get("summary.name"), "type": d.get("summary.type"),
                               "accessible": bool(d.get("summary.accessible")), "capacity_gb": round(cap / 1073741824, 1),
                               "free_gb": round(free / 1073741824, 1),
                               "used_pct": round(100 * (cap - free) / cap, 1) if cap else None})
        datastores.sort(key=lambda d: -(d["used_pct"] or 0))
        # Physical readings (temperature, fans, power...) plus anything not healthy; skip informational entries.
        sensors = [x for x in (_sensor(s) for s in h.get("runtime.healthSystemRuntime.systemHealthInfo.numericSensorInfo") or [])
                   if x["state"] in ("yellow", "red") or (x["type"] or "").lower() in PHYSICAL_SENSORS]
        alarms = []
        for st in h.get("triggeredAlarmState") or []:
            try:
                name = st.alarm.info.name
            except Exception:  # noqa: BLE001
                name = "Alarm"
            alarms.append({"name": name, "status": str(st.overallStatus), "time": st.time.isoformat() if st.time else None,
                           "acknowledged": bool(st.acknowledged)})
        vms = [vm_power.get(r._moId) for r in h.get("vm") or []]
        hosts.append({
            "moid": h["_moid"], "name": h.get("name"), "status": str(h.get("overallStatus") or "gray"),
            "cluster": parent.name if isinstance(parent, vim.ClusterComputeResource) else None,
            "connection": str(h.get("runtime.connectionState")), "power": str(h.get("runtime.powerState")),
            "maintenance": bool(h.get("runtime.inMaintenanceMode")),
            "product": product.fullName if product else None, "version": product.version if product else None,
            "build": product.build if product else None,
            "vendor": hw.vendor if hw else None, "model": hw.model if hw else None,
            "cpu_model": hw.cpuModel if hw else None, "cpu_sockets": hw.numCpuPkgs if hw else None,
            "cpu_cores": hw.numCpuCores if hw else None, "cpu_threads": hw.numCpuThreads if hw else None,
            "nics": hw.numNics if hw else None,
            "cpu_used_mhz": cpu_used, "cpu_total_mhz": cpu_total,
            "cpu_pct": round(100 * cpu_used / cpu_total, 1) if cpu_used is not None and cpu_total else None,
            "mem_used_mb": mem_used, "mem_total_mb": mem_total_mb,
            "mem_pct": round(100 * mem_used / mem_total_mb, 1) if mem_used is not None and mem_total_mb else None,
            "boot_time": boot.isoformat() if boot else None,
            "uptime_seconds": int((now - boot).total_seconds()) if boot else getattr(qs, "uptime", None),
            "vms_total": len(vms), "vms_on": sum(1 for p in vms if p == "poweredOn"),
            "management_ips": [v.spec.ip.ipAddress for v in h.get("config.network.vnic") or []
                               if getattr(getattr(v, "spec", None), "ip", None) and v.spec.ip.ipAddress],
            "datastores": datastores,
            "sensors": sorted(sensors, key=lambda s: (s["state"] == "green", s["type"] or "", s["name"])),
            "sensor_problems": sum(1 for s in sensors if s["state"] in ("yellow", "red")),
            "alarms": alarms,
            "services": sorted(({"id": s.key, "name": s.label, "running": bool(s.running), "policy": s.policy}
                                for s in h.get("config.service.service") or []), key=lambda s: s["name"].lower()),
        })
    hosts.sort(key=lambda h: (h["name"] or "").lower())
    _cache["hosts"] = (time.monotonic(), hosts)
    return hosts
