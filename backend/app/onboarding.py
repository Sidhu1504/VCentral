"""Onboarding: track powered-on VMs without an agent and send the admin team a digest about them.

vCentral is for the admin team only: nothing is sent to VM owners. The digest groups VMs by owner so the team
knows whom to follow up with, and lists overdue VMs first.
"""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import iso, utcnow
from .models import VM
from .notify import send_chat, send_email
from .state import get_state, set_state

POLICY_DEFAULTS = {"enabled": False, "include_after_days": 1, "every_days": 1, "overdue_after_days": 14}
POLICY_LIMITS = {"include_after_days": (0, 30), "every_days": (1, 30), "overdue_after_days": (1, 180)}


def get_policy(db: Session) -> dict:
    return {**POLICY_DEFAULTS, **{k: v for k, v in get_state(db, "onboarding_policy").items() if k in POLICY_DEFAULTS}}


def save_policy(db: Session, changes: dict) -> dict:
    policy = get_policy(db)
    for key, value in changes.items():
        if key == "enabled":
            policy[key] = bool(value)
        elif key in POLICY_LIMITS:
            lo, hi = POLICY_LIMITS[key]
            if not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a whole number between {lo} and {hi}")
            policy[key] = value
    set_state(db, "onboarding_policy", policy)
    return policy


def is_unmanaged(vm: VM) -> bool:
    return not vm.is_deleted and vm.power_state == "poweredOn" and not vm.managed


def update_unmanaged(db: Session) -> None:
    """Start the clock when a VM is first seen running without an agent; clear it once an agent is linked."""
    now = utcnow()
    for vm in db.scalars(select(VM)):
        if is_unmanaged(vm):
            if vm.unmanaged_since is None:
                vm.unmanaged_since = now
        elif (vm.managed or vm.is_deleted) and vm.unmanaged_since is not None:
            vm.unmanaged_since = None
    db.commit()


def _days(vm: VM, now: datetime) -> int:
    return (now - vm.unmanaged_since).days if vm.unmanaged_since else 0


def _line(vm: VM, now: datetime) -> str:
    d = _days(vm, now)
    age = "since today" if d == 0 else f"for {d} day{'s' if d != 1 else ''}"
    how = "can install through VMware Tools" if vm.tools_status == "guestToolsRunning" else "VMware Tools not running: use the install command"
    return f"{vm.name} ({vm.primary_ip or 'no IP'}, {vm.guest_os or vm.os_family}) — without agent {age}; {how}"


def build_digest(vms: list[VM], now: datetime, overdue_days: int) -> tuple[str, str, str, str]:
    """Returns (subject, text, html, chat_text)."""
    overdue = [vm for vm in vms if _days(vm, now) >= overdue_days]
    by_owner: dict[str, list[VM]] = defaultdict(list)
    for vm in vms:
        by_owner[vm.owner or "Unknown owner"].append(vm)
    owners = sorted(by_owner, key=lambda o: (o == "Unknown owner", -len(by_owner[o]), o.lower()))
    link = get_settings().public_url.rstrip("/")
    link = f"{link}/onboarding" if link else ""

    subject = f"vCentral: {len(vms)} VM(s) without the agent" + (f", {len(overdue)} overdue" if overdue else "")
    parts = [f"{len(vms)} powered-on VM(s) do not have the vCentral agent yet."]
    if overdue:
        parts.append(f"Overdue (more than {overdue_days} days):\n" + "\n".join(f"- {_line(vm, now)}" for vm in overdue[:100]))
    parts.append("By owner:\n" + "\n".join(
        f"\n{owner} ({len(by_owner[owner])}):\n" + "\n".join(f"- {_line(vm, now)}" for vm in by_owner[owner][:50])
        for owner in owners))
    if link:
        parts.append(f"Install from the Onboarding page: {link}")
    text = "\n\n".join(parts)

    def ul(items):
        return "<ul>" + "".join(f"<li>{html.escape(_line(vm, now))}</li>" for vm in items) + "</ul>"
    body = f"<p><b>{len(vms)}</b> powered-on VM(s) do not have the vCentral agent yet.</p>"
    if overdue:
        body += f"<h3>Overdue (more than {overdue_days} days)</h3>{ul(overdue[:100])}"
    body += "<h3>By owner</h3>" + "".join(
        f"<p><b>{html.escape(o)}</b> ({len(by_owner[o])})</p>{ul(by_owner[o][:50])}" for o in owners)
    if link:
        body += f'<p><a href="{html.escape(link)}">Open the Onboarding page</a></p>'

    top = ", ".join(f"{o} ({len(by_owner[o])})" for o in owners[:8])
    chat = f"*{subject}*\nBy owner: {top}" + (f"\nOverdue: {', '.join(vm.name for vm in overdue[:15])}" if overdue else "")
    if link:
        chat += f"\n{link}"
    return subject, text, body, chat


def run_digest(db: Session, force: bool = False) -> dict:
    """Send the admin-team digest when it is due (or now, if forced)."""
    s = get_settings()
    policy = get_policy(db)
    now = utcnow()
    result = {"sent": False, "vm_count": 0, "overdue": 0, "failed": 0}
    if not policy["enabled"] and not force:
        return result
    # "Send now" lists every VM currently without an agent; the schedule waits include_after_days for new VMs.
    include = timedelta(0) if force else timedelta(days=policy["include_after_days"])
    vms = [vm for vm in db.scalars(select(VM).where(VM.unmanaged_since.is_not(None)).order_by(VM.name))
           if is_unmanaged(vm) and now - vm.unmanaged_since >= include]
    result["vm_count"] = len(vms)
    result["overdue"] = sum(1 for vm in vms if _days(vm, now) >= policy["overdue_after_days"])
    if not vms:
        return result
    last = get_state(db, "onboarding_digest").get("sent_at")
    if not force and last and now - datetime.fromisoformat(last.rstrip("Z")) < timedelta(days=policy["every_days"]):
        return result

    subject, text, body, chat = build_digest(vms, now, policy["overdue_after_days"])
    ids = [vm.id for vm in vms]
    attempts = []
    if s.admin_email_list:
        attempts.append(send_email(db, s.admin_email_list, subject, text, body, kind="digest", vm_ids=ids))
    if s.google_chat_webhook:
        attempts.append(send_chat(db, chat, subject, kind="digest", vm_ids=ids))
    result["failed"] = attempts.count(False)
    if any(attempts):
        set_state(db, "onboarding_digest", {"sent_at": iso(now)})
        result["sent"] = True
    db.commit()
    return result
