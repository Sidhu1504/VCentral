"""Reports page: each report returns columns, rows and a summary, shown in the UI, downloadable as CSV and printable.

Column types (for the UI): vm (link, row has vm_id), number, pct, date, text, tags (list of strings).
"""
import csv
import io
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import iso, utcnow
from .models import VM, Agent, Task, TaskRun, VMHistory, VMMetricHourly

PERIODS = [7, 30, 90]


def col(key, title, type="text"):
    return {"key": key, "title": title, "type": type}


def _gb(mb):
    return round(mb / 1024, 1) if mb else None


def _active_vms(db: Session) -> list[VM]:
    return list(db.scalars(select(VM).where(VM.is_deleted.is_(False)).order_by(VM.name)))


def _agents_by_vm(db: Session) -> dict[int, Agent]:
    from .agents import agent_state
    best: dict[int, Agent] = {}
    for a in db.scalars(select(Agent).where(Agent.status == "active", Agent.vm_id.is_not(None))):
        cur = best.get(a.vm_id)
        if cur is None or (a.last_seen_at or a.enrolled_at) > (cur.last_seen_at or cur.enrolled_at):
            best[a.vm_id] = a
    for a in best.values():
        a._state = agent_state(a)
    return best


# -- reports ---------------------------------------------------------------------------------------------

def inventory(db: Session, days: int) -> dict:
    agents = _agents_by_vm(db)
    rows = []
    for v in _active_vms(db):
        a = agents.get(v.id)
        rows.append({"vm_id": v.id, "name": v.name, "power": v.power_state, "health": v.health,
                     "critical": "yes" if v.critical else "", "os": v.guest_os, "ip": v.primary_ip or "",
                     "owner": v.owner or "", "vcpu": v.num_cpu, "memory_gb": _gb(v.memory_mb),
                     "storage_gb": v.storage_committed_gb, "host": v.esxi_host, "cluster": v.cluster, "folder": v.folder,
                     "agent": a._state if a else "none", "created": iso(v.vcenter_created_at or v.first_seen_at)})
    on = [r for r in rows if r["power"] == "poweredOn"]
    return {
        "columns": [col("name", "VM", "vm"), col("power", "Power"), col("health", "Health"), col("critical", "Critical"),
                    col("os", "OS"), col("ip", "IP"), col("owner", "Owner"), col("vcpu", "vCPU", "number"),
                    col("memory_gb", "RAM (GB)", "number"), col("storage_gb", "Storage (GB)", "number"),
                    col("host", "ESXi host"), col("cluster", "Cluster"), col("folder", "Folder"), col("agent", "Agent"),
                    col("created", "Created", "date")],
        "rows": rows,
        "summary": [{"label": "VMs", "value": len(rows)}, {"label": "Powered on", "value": len(on)},
                    {"label": "vCPU (powered on)", "value": sum(r["vcpu"] or 0 for r in on)},
                    {"label": "RAM GB (powered on)", "value": round(sum(r["memory_gb"] or 0 for r in on))},
                    {"label": "Storage GB (all)", "value": round(sum(r["storage_gb"] or 0 for r in rows))}],
    }


def owners(db: Session, days: int) -> dict:
    groups: dict[str, list[VM]] = defaultdict(list)
    for v in _active_vms(db):
        groups[v.owner or "(unknown)"].append(v)
    rows = []
    for owner, vms in groups.items():
        on = [v for v in vms if v.power_state == "poweredOn"]
        rows.append({"owner": owner, "vms": len(vms), "powered_on": len(on),
                     "vcpu": sum(v.num_cpu or 0 for v in on), "memory_gb": round(sum(v.memory_mb or 0 for v in on) / 1024, 1),
                     "storage_gb": round(sum(v.storage_committed_gb or 0 for v in vms), 1),
                     "vm_names": [v.name for v in vms]})
    rows.sort(key=lambda r: (r["owner"] == "(unknown)", -r["vms"], r["owner"].lower()))
    unknown = next((r["vms"] for r in rows if r["owner"] == "(unknown)"), 0)
    return {
        "columns": [col("owner", "Owner"), col("vms", "VMs", "number"), col("powered_on", "Powered on", "number"),
                    col("vcpu", "vCPU (on)", "number"), col("memory_gb", "RAM GB (on)", "number"),
                    col("storage_gb", "Storage GB", "number"), col("vm_names", "VMs", "tags")],
        "rows": rows,
        "summary": [{"label": "Owners", "value": sum(1 for r in rows if r["owner"] != "(unknown)")},
                    {"label": "VMs with unknown owner", "value": unknown}],
    }


def problems(db: Session, days: int) -> dict:
    rows = [{"vm_id": v.id, "name": v.name, "health": v.health, "critical": "yes" if v.critical else "",
             "problems": [p["message"] for p in v.problems or []], "power": v.power_state, "ip": v.primary_ip or "",
             "owner": v.owner or ""}
            for v in _active_vms(db) if v.health != "ok"]
    rows.sort(key=lambda r: (r["health"] != "critical", r["critical"] != "yes", r["name"].lower()))
    return {
        "columns": [col("name", "VM", "vm"), col("health", "Health"), col("critical", "Critical VM"),
                    col("problems", "Problems", "tags"), col("power", "Power"), col("ip", "IP"), col("owner", "Owner")],
        "rows": rows,
        "summary": [{"label": "Critical", "value": sum(1 for r in rows if r["health"] == "critical")},
                    {"label": "Warning", "value": sum(1 for r in rows if r["health"] == "warning")}],
    }


def agents(db: Session, days: int) -> dict:
    by_vm = _agents_by_vm(db)
    rows, versions = [], defaultdict(int)
    for v in _active_vms(db):
        a = by_vm.get(v.id)
        if a:
            versions[a.agent_version or "unknown"] += 1
        rows.append({"vm_id": v.id, "name": v.name, "os": v.os_family, "power": v.power_state,
                     "agent": a._state if a else "none", "version": a.agent_version if a else "",
                     "last_seen": iso(a.last_seen_at) if a else None, "owner": v.owner or ""})
    on = [r for r in rows if r["power"] == "poweredOn"]
    covered = sum(1 for r in on if r["agent"] == "online")
    rows.sort(key=lambda r: ({"none": 0, "offline": 1}.get(r["agent"], 2), r["name"].lower()))
    return {
        "columns": [col("name", "VM", "vm"), col("os", "OS"), col("power", "Power"), col("agent", "Agent"),
                    col("version", "Version"), col("last_seen", "Last report", "date"), col("owner", "Owner")],
        "rows": rows,
        "summary": [{"label": "Powered-on VMs with an online agent", "value": f"{covered} of {len(on)}"},
                    {"label": "Coverage", "value": f"{round(covered / len(on) * 100) if on else 0}%"},
                    {"label": "No agent", "value": sum(1 for r in rows if r["agent"] == "none")},
                    *[{"label": f"Version {k}", "value": n} for k, n in sorted(versions.items())]],
    }


def changes(db: Session, days: int) -> dict:
    since = utcnow() - timedelta(days=days)
    vms = {v.id: v for v in db.scalars(select(VM))}
    rows = []
    q = select(VMHistory).where(VMHistory.ts >= since, VMHistory.kind.in_(["created", "deleted", "reappeared"]))
    for h in db.scalars(q.order_by(VMHistory.ts.desc())):
        v = vms.get(h.vm_id)
        change = "converted to template" if (h.data or {}).get("template") else h.kind
        rows.append({"when": iso(h.ts), "change": change, "vm_id": h.vm_id, "name": v.name if v else "?",
                     "owner": (v.owner if v else "") or "", "os": (v.guest_os if v else "") or "", "detail": h.message})
    return {
        "columns": [col("when", "When", "date"), col("change", "Change"), col("name", "VM", "vm"), col("owner", "Owner / creator"),
                    col("os", "OS"), col("detail", "Detail")],
        "rows": rows,
        "summary": [{"label": "Created", "value": sum(1 for r in rows if r["change"] == "created")},
                    {"label": "Deleted", "value": sum(1 for r in rows if r["change"] == "deleted")},
                    {"label": "Converted to template", "value": sum(1 for r in rows if r["change"] == "converted to template")},
                    {"label": "Came back", "value": sum(1 for r in rows if r["change"] == "reappeared")}],
    }


def usage(db: Session, days: int) -> dict:
    since = utcnow() - timedelta(days=days)
    q = (select(VMMetricHourly.vm_id,
                func.sum(VMMetricHourly.cpu_pct_avg * VMMetricHourly.samples), func.max(VMMetricHourly.cpu_pct_max),
                func.sum(VMMetricHourly.mem_pct_avg * VMMetricHourly.samples), func.max(VMMetricHourly.mem_pct_max),
                func.max(VMMetricHourly.disk_used_pct_max), func.sum(VMMetricHourly.samples), func.count())
         .where(VMMetricHourly.ts >= since).group_by(VMMetricHourly.vm_id))
    stats = {r[0]: r for r in db.execute(q)}
    rows = []
    for v in _active_vms(db):
        s = stats.get(v.id)
        if not s or not s[6]:
            continue
        cpu_avg = round(s[1] / s[6], 1) if s[1] is not None else None
        mem_avg = round(s[3] / s[6], 1) if s[3] is not None else None
        flags = []
        if s[7] >= 24 and cpu_avg is not None and cpu_avg < 5 and (s[2] or 0) < 25:
            flags.append("idle")
        if (s[2] or 0) >= 90 or (cpu_avg or 0) >= 70:
            flags.append("CPU busy")
        if (s[4] or 0) >= 90:
            flags.append("memory high")
        if (s[5] or 0) >= 85:
            flags.append("disk nearly full")
        rows.append({"vm_id": v.id, "name": v.name, "vcpu": v.num_cpu, "memory_gb": _gb(v.memory_mb),
                     "cpu_avg": cpu_avg, "cpu_peak": s[2], "mem_avg": mem_avg, "mem_peak": s[4], "disk_peak": s[5],
                     "hours": s[7], "flags": flags, "owner": v.owner or ""})
    rows.sort(key=lambda r: (not r["flags"], -(r["cpu_peak"] or 0)))
    return {
        "columns": [col("name", "VM", "vm"), col("flags", "Flags", "tags"), col("vcpu", "vCPU", "number"),
                    col("memory_gb", "RAM GB", "number"), col("cpu_avg", "CPU avg", "pct"), col("cpu_peak", "CPU peak", "pct"),
                    col("mem_avg", "Active mem avg", "pct"), col("mem_peak", "Active mem peak", "pct"),
                    col("disk_peak", "Fullest disk", "pct"), col("hours", "Hours of data", "number"), col("owner", "Owner")],
        "rows": rows,
        "summary": [{"label": "Idle (avg CPU < 5%, peak < 25%)", "value": sum(1 for r in rows if "idle" in r["flags"])},
                    {"label": "CPU busy (peak ≥ 90% or avg ≥ 70%)", "value": sum(1 for r in rows if "CPU busy" in r["flags"])},
                    {"label": "Disk ≥ 85%", "value": sum(1 for r in rows if "disk nearly full" in r["flags"])}],
        "note": "From vCenter's measurements, averaged per hour. Memory is vCenter's active memory. Idle VMs are candidates for shrinking or switching off; busy ones for more resources.",
    }


TOOLS_VERSION = {
    "guestToolsNotInstalled": "not installed", "guestToolsCurrent": "current", "guestToolsUnmanaged": "open-vm-tools (OS-managed)",
    "guestToolsNeedUpgrade": "upgrade available", "guestToolsSupportedOld": "old but supported",
    "guestToolsTooOld": "too old (unsupported)", "guestToolsBlacklisted": "known-bad version",
    "guestToolsSupportedNew": "newer than host", "guestToolsTooNew": "too new for host",
}
OUTDATED = {"guestToolsNeedUpgrade", "guestToolsSupportedOld", "guestToolsTooOld", "guestToolsBlacklisted"}
FIX = {
    "linux": "Install open-vm-tools (apt install open-vm-tools / dnf install open-vm-tools), or run the 'Install or start VMware Tools' script if the VM has the agent",
    "windows": "vSphere Client: right-click the VM > Guest OS > Install/Upgrade VMware Tools, then run setup in the VM",
    "other": "Install VMware Tools or the OS's open-vm-tools package",
}


def tools(db: Session, days: int) -> dict:
    """VMs where VMware Tools is missing, not running or outdated (from vCenter, so it covers every VM)."""
    agents = _agents_by_vm(db)
    counts = defaultdict(int)
    rows = []
    for v in _active_vms(db):
        on = v.power_state == "poweredOn"
        version = v.tools_version_status
        if version == "guestToolsNotInstalled":
            problem = "not installed"
        elif on and v.tools_status != "guestToolsRunning":
            problem = "installed, not running"
        elif version in OUTDATED:
            problem = "outdated"
        else:
            counts["ok" if version else "unknown"] += 1
            continue
        counts[problem] += 1
        a = agents.get(v.id)
        impact = "vCenter cannot see its IP, disks or guest OS; no-IP alerts and agent install through VMware Tools do not work" \
            if problem != "outdated" else "May miss fixes; vCenter shows it as needing an upgrade"
        rows.append({"vm_id": v.id, "name": v.name, "problem": problem, "power": v.power_state,
                     "running": "yes" if v.tools_status == "guestToolsRunning" else "no",
                     "version": TOOLS_VERSION.get(version, version or "unknown"), "os": v.guest_os or "",
                     "ip": v.primary_ip or (v.last_known_ips or [""])[0], "agent": a._state if a else "none",
                     "owner": v.owner or "", "impact": impact, "fix": FIX.get(v.os_family or "other", FIX["other"])})
    order = {"not installed": 0, "installed, not running": 1, "outdated": 2}
    rows.sort(key=lambda r: (order[r["problem"]], r["power"] != "poweredOn", r["name"].lower()))
    return {
        "columns": [col("name", "VM", "vm"), col("problem", "Problem"), col("power", "Power"), col("running", "Tools running"),
                    col("version", "Tools version"), col("os", "OS"), col("ip", "IP (last known)"), col("agent", "Agent"),
                    col("owner", "Owner"), col("impact", "What it affects"), col("fix", "How to fix")],
        "rows": rows,
        "summary": [{"label": "Not installed", "value": counts["not installed"]},
                    {"label": "Installed, not running", "value": counts["installed, not running"]},
                    {"label": "Outdated", "value": counts["outdated"]},
                    {"label": "OK", "value": counts["ok"]}],
        "note": "Fix VMs from here with the Fix button: vCenter upgrades outdated Tools, agents install or start Tools, and Windows VMs without an agent get the installer mounted. "
                "Powered-off VMs are only listed when Tools is not installed or outdated.",
    }


def powered_off(db: Session, days: int) -> dict:
    now = utcnow()
    off = [v for v in _active_vms(db) if v.power_state in ("poweredOff", "suspended")]
    last_off = dict(db.execute(select(VMHistory.vm_id, func.max(VMHistory.ts))
                               .where(VMHistory.kind.in_(["powered_off", "suspended"]), VMHistory.vm_id.in_([v.id for v in off] or [0]))
                               .group_by(VMHistory.vm_id)).all())
    rows = []
    for v in off:
        since = last_off.get(v.id)
        rows.append({"vm_id": v.id, "name": v.name, "power": v.power_state,
                     "off_since": iso(since) if since else None,
                     "days_off": (now - since).days if since else None,
                     "known_since": iso(v.first_seen_at) if not since else None,
                     "storage_gb": v.storage_committed_gb, "last_ip": (v.last_known_ips or [""])[0], "owner": v.owner or ""})
    rows.sort(key=lambda r: (r["days_off"] is not None, -(r["days_off"] or 0)))
    return {
        "columns": [col("name", "VM", "vm"), col("power", "State"), col("off_since", "Off since", "date"),
                    col("days_off", "Days off", "number"), col("known_since", "Already off when vCentral first saw it", "date"),
                    col("storage_gb", "Storage GB", "number"), col("last_ip", "Last IP"), col("owner", "Owner")],
        "rows": rows,
        "summary": [{"label": "Powered off / suspended", "value": len(rows)},
                    {"label": f"Off more than {days} days", "value": sum(1 for r in rows if r["days_off"] is None or r["days_off"] > days)},
                    {"label": "Storage they use (GB)", "value": round(sum(r["storage_gb"] or 0 for r in rows))}],
        "note": "VMs off for a long time still use storage and hold their IP. Check with the owner whether they can be deleted.",
    }


def ip_usage(db: Session, days: int) -> dict:
    from .api.network import classify, network_facts
    subnets, _ = classify(network_facts(db), get_settings().subnet_list)
    labels = {"conflict": "Conflict", "used": "VM", "infra": "Infrastructure", "template": "Template",
              "reserved": "Reserved", "stale": "Powered-off VM", "device": "Other device"}
    rows = []
    for s in subnets:
        for r in s["ips"]:
            holder = ", ".join(v["name"] for v in r["vms"]) or "; ".join(r["infra"]) or \
                ", ".join(t["name"] for t in r["templates"]) or (r["reservation"] or {}).get("label") or \
                ", ".join(v["name"] for v in r["stale_vms"]) or (f"MAC {r['device']['mac']}" if r["device"] and r["device"]["mac"] else "")
            rows.append({"subnet": s["cidr"], "ip": r["ip"], "state": labels[r["state"]], "held_by": holder,
                         "responds": "yes" if r["device"] else "", "warnings": r["warnings"]})
    return {
        "columns": [col("subnet", "Subnet"), col("ip", "IP"), col("state", "Taken by"), col("held_by", "Who / what"),
                    col("responds", "Answers on network"), col("warnings", "Warnings", "tags")],
        "rows": rows,
        "summary": [x for s in subnets for x in ({"label": f"{s['cidr']} taken", "value": f"{s['taken']} of {s['usable']}"},
                                                 {"label": f"{s['cidr']} free", "value": s["free"]})],
    }


def tasks(db: Session, days: int) -> dict:
    from .tasks import summarize
    since = utcnow() - timedelta(days=days)
    task_list = list(db.scalars(select(Task).where(Task.created_at >= since).order_by(Task.created_at.desc())))
    runs = defaultdict(list)
    if task_list:
        for r in db.scalars(select(TaskRun).where(TaskRun.task_id.in_([t.id for t in task_list]))):
            runs[r.task_id].append(r)
    rows = []
    for t in task_list:
        s = summarize(runs[t.id])
        c = s["counts"]
        rows.append({"when": iso(t.created_at), "name": t.name, "by": t.created_by, "kind": t.kind, "result": s["state"],
                     "vms": s["total"], "succeeded": c["succeeded"],
                     "failed": c["failed"] + c["timeout"] + c["lost"] + c["expired"], "skipped": c["skipped"], "task_id": t.id})
    done = sum(r["succeeded"] + r["failed"] for r in rows)
    return {
        "columns": [col("when", "When", "date"), col("name", "Task"), col("by", "Started by"), col("kind", "Kind"),
                    col("result", "Result"), col("vms", "VMs", "number"), col("succeeded", "Succeeded", "number"),
                    col("failed", "Failed", "number"), col("skipped", "Skipped", "number")],
        "rows": rows,
        "summary": [{"label": "Tasks", "value": len(rows)},
                    {"label": "VM runs succeeded", "value": f"{round(sum(r['succeeded'] for r in rows) / done * 100) if done else 0}%"}],
    }


REPORTS = {
    "inventory": {"title": "VM inventory", "fn": inventory, "periodic": False, "icon": "inventory",
                  "description": "Every VM with power, health, OS, IP, owner, size, host, folder and agent."},
    "owners": {"title": "VMs by owner", "fn": owners, "periodic": False, "icon": "owners",
               "description": "How many VMs, vCPU, RAM and storage each owner has, and which VMs have no known owner."},
    "problems": {"title": "Current problems", "fn": problems, "periodic": False, "icon": "problems",
                 "description": "VMs with a health problem right now: no IP, agent offline, critical VM down."},
    "agents": {"title": "Agent coverage", "fn": agents, "periodic": False, "icon": "agents",
               "description": "Which VMs have an agent, its state and version, and which still need one."},
    "changes": {"title": "New and deleted VMs", "fn": changes, "periodic": True, "icon": "changes",
                "description": "VMs created, deleted, converted to templates or brought back, with owner."},
    "usage": {"title": "Resource usage", "fn": usage, "periodic": True, "icon": "usage",
              "description": "Average and peak CPU, memory and disk per VM, flagging idle and overloaded VMs."},
    "tools": {"title": "VMware Tools", "fn": tools, "periodic": False, "icon": "tools",
              "description": "VMs where VMware Tools is not installed, not running or outdated, what that breaks and how to fix it."},
    "powered-off": {"title": "Powered-off VMs", "fn": powered_off, "periodic": True, "icon": "off",
                    "description": "VMs that are off, since when, and the storage and IPs they still hold."},
    "ip": {"title": "IP address usage", "fn": ip_usage, "periodic": False, "icon": "ip",
           "description": "Every taken address in each subnet, what holds it, and conflicts."},
    "tasks": {"title": "Task history", "fn": tasks, "periodic": True, "icon": "tasks",
              "description": "Tasks run in the period, who started them, and how many VMs succeeded or failed."},
}


def run_report(db: Session, key: str, days: int) -> dict:
    r = REPORTS[key]
    result = r["fn"](db, days)
    return {"key": key, "title": r["title"], "description": r["description"], "periodic": r["periodic"],
            "days": days if r["periodic"] else None, "generated_at": iso(utcnow()), **result}


def _cell(value):
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value  # never a formula when opened in Excel
    return value


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["title"] for c in report["columns"]])
    for row in report["rows"]:
        w.writerow([_cell(row.get(c["key"])) for c in report["columns"]])
    return buf.getvalue()
