"""VM health: turns VM + agent state into a list of problems and records when problems start and end.

Severity is "critical" for VMs marked critical (they must always be up) and "warning" for the rest.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .agents import is_online, representative_agents
from .db import iso, utcnow
from .models import VM, Agent, VMHistory

SEVERITY_RANK = {"ok": 0, "warning": 1, "critical": 2}


def _problem(code: str, severity: str, message: str) -> dict:
    return {"code": code, "severity": severity, "message": message}


def compute_problems(vm: VM, agent: Agent | None) -> list[dict]:
    sev = "critical" if vm.critical else "warning"
    if vm.is_deleted:
        return [_problem("deleted", "critical", "Critical VM was deleted from vCenter")] if vm.critical else []
    if vm.power_state != "poweredOn":
        if vm.critical:
            state = "suspended" if vm.power_state == "suspended" else "powered off"
            return [_problem("powered_off", "critical", f"Critical VM is {state}")]
        return []

    problems = []
    online = agent is not None and is_online(agent)
    tools = vm.tools_status == "guestToolsRunning"
    # VMware Tools reports through the hypervisor, so it still works when the VM has lost its network.
    if tools and not vm.ip_addresses:
        problems.append(_problem("no_ip", sev, "No IP address (reported by VMware Tools)"))
    elif online and not (agent.ips or []):
        problems.append(_problem("no_ip", sev, "No IP address (reported by the agent)"))
    if agent is not None and not online:
        problems.append(_problem("agent_offline", sev, "Agent stopped reporting"))
    if vm.critical and not tools and agent is None:
        problems.append(_problem("not_monitored", "warning",
                                 "No VMware Tools or agent: vCentral cannot see whether this VM is healthy"))
    return problems


def evaluate_health(db: Session, vm_ids: list[int] | None = None) -> int:
    """Recompute problems; write a history entry whenever a problem starts or ends. Returns number of changes."""
    now = utcnow()
    agents = representative_agents(db)
    q = select(VM).where(or_(VM.is_deleted.is_(False), VM.critical.is_(True), VM.health != "ok"))
    if vm_ids is not None:
        q = select(VM).where(VM.id.in_(vm_ids))
    changes = 0
    for vm in db.scalars(q):
        old = {p["code"]: p for p in (vm.problems or [])}
        new = []
        for p in compute_problems(vm, agents.get(vm.id)):
            prev = old.get(p["code"])
            p["since"] = prev["since"] if prev and prev["severity"] == p["severity"] else iso(now)
            new.append(p)
            if not prev:
                db.add(VMHistory(vm_id=vm.id, ts=now, kind="problem", message=p["message"],
                                 data={"code": p["code"], "severity": p["severity"]}))
                changes += 1
        new_codes = {p["code"] for p in new}
        for code, p in old.items():
            if code not in new_codes:
                db.add(VMHistory(vm_id=vm.id, ts=now, kind="resolved", message=f"Resolved: {p['message']}",
                                 data={"code": code}))
                changes += 1
        health = max((p["severity"] for p in new), key=SEVERITY_RANK.get, default="ok")
        if new != (vm.problems or []) or health != vm.health:
            vm.problems, vm.health = new, health
    db.commit()
    return changes


def tools_state(vm) -> str:
    """running | not_installed | asleep (probably) | not_running | off | unknown, for the UI."""
    if vm.power_state != "poweredOn":
        return "off"
    if vm.tools_status == "guestToolsRunning":
        return "running"
    if vm.tools_version_status == "guestToolsNotInstalled":
        return "not_installed"
    if vm.tools_status is None and vm.tools_version_status is None:
        return "unknown"
    # Installed but stopped on a Windows VM that used to have an IP: almost always Windows idle sleep.
    if vm.os_family == "windows" and vm.last_known_ips:
        return "asleep"
    return "not_running"
