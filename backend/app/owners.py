"""Owner suggestions for VMs whose creator is no longer in vCenter's event history (kept ~30 days by default).

Two signals, both from vCenter (nothing is guessed from outside data):
  * who has been using the VM: console opens, power on/off, reboots, reconfigurations in the retained events;
  * a known vCenter user's name in the VM name, folder or notes (e.g. "Nachiket-Win11" and user "nachiket").
Suggestions are only proposals: an admin or operator accepts them on the Owners page.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import utcnow
from .models import VM

KIND_LABEL = {
    "VmAcquiredTicketEvent": "opened the console", "VmAcquiredMksTicketEvent": "opened the console",
    "VmPoweredOnEvent": "powered it on", "VmPoweredOffEvent": "powered it off", "VmResettingEvent": "reset it",
    "VmSuspendedEvent": "suspended it", "VmGuestRebootEvent": "rebooted it", "VmGuestShutdownEvent": "shut it down",
    "VmReconfiguredEvent": "changed its settings", "VmRenamedEvent": "renamed it",
}
# Words too common to identify a person when found in a VM name.
COMMON = {"admin", "administrator", "test", "testing", "user", "root", "demo", "temp", "server", "client", "linux",
          "windows", "ubuntu", "centos", "rocky", "alma", "redhat", "debian", "build", "runner", "gitlab", "jenkins",
          "docker", "master", "worker", "node", "prod", "staging", "backup", "patch", "vcenter", "local", "vsphere"}
SERVICE_HINTS = ("vpxd", "com.vmware", "solution", "machine-", "vsphere-webclient", "vmware-vsm", "svc-vcentral")


def account_key(user: str) -> str:
    """'VSPHERE.LOCAL\\Ravi' and 'ravi@vsphere.local' -> 'ravi@vsphere.local'."""
    user = user.strip()
    if "\\" in user:
        domain, _, name = user.partition("\\")
        user = f"{name}@{domain}"
    return user.lower()


def name_tokens(user: str) -> list[str]:
    """Words of the account name that could appear in a VM name: 'nachiket.patil' -> ['nachiket', 'patil']."""
    local = account_key(user).split("@")[0]
    return [t for t in re.split(r"[^a-z0-9]+", local) if len(t) >= 4 and t not in COMMON and not t.isdigit()]


def _ignored(user: str, settings: Settings) -> bool:
    key = account_key(user)
    if key == account_key(settings.vcenter_user or "-"):
        return True
    if key in {account_key(u) for u in settings.owner_ignore_users.split(",") if u.strip()}:
        return True
    return any(h in key for h in SERVICE_HINTS)


def _vm_words(vm: VM) -> list[str]:
    text = " ".join(filter(None, [vm.name, (vm.folder or "").split("/")[-1], vm.annotation]))
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def _activity_reason(stats: dict) -> str:
    kinds = sorted({KIND_LABEL.get(k, k) for k in stats["kinds"]})
    days = (utcnow() - stats["last"]).days if stats["last"] else None
    when = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago" if days is not None else "recently"
    return f"{', '.join(kinds)} ({stats['count']}×, last {when})"


def suggest_owners(db: Session, activity: dict[str, dict[str, dict]], settings: Settings) -> dict:
    """Recompute suggestions for every VM without an owner. `activity` comes from VCenterClient.user_activity()."""
    # Everyone vCenter shows as a person: activity users plus creators/owners already known.
    people: dict[str, str] = {}  # account key -> display form as vCenter writes it
    for per_user in activity.values():
        for user in per_user:
            if not _ignored(user, settings):
                people.setdefault(account_key(user), user)
    for (owner,) in db.execute(select(VM.owner).where(VM.owner.is_not(None))).all():
        if owner and not _ignored(owner, settings) and ("\\" in owner or "@" in owner):
            people.setdefault(account_key(owner), owner)
    token_index: dict[str, set[str]] = defaultdict(set)
    for key, display in people.items():
        for t in name_tokens(display):
            token_index[t].add(key)

    suggested = 0
    for vm in db.scalars(select(VM).where(VM.is_deleted.is_(False))):
        if vm.owner:
            vm.owner_suggestion = vm.owner_suggestion_reason = vm.owner_suggestion_confidence = None
            continue
        # 1. A known person's name inside the VM name / folder / notes.
        words = _vm_words(vm)
        matches: dict[str, str] = {}
        for token, keys in token_index.items():
            if any(w == token or (len(token) >= 5 and w.startswith(token)) for w in words):
                for k in keys:
                    matches[k] = token
        # 2. Who has been using it.
        usage = {account_key(u): (u, s) for u, s in (activity.get(vm.moid) or {}).items() if not _ignored(u, settings)}
        top = max(usage.values(), key=lambda us: (us[1]["count"], us[1]["last"] or utcnow()), default=None)

        suggestion = reason = confidence = None
        if len(matches) == 1:
            key, token = next(iter(matches.items()))
            suggestion = people[key]
            reason = f"“{token}” appears in the VM name/folder/notes"
            if top and account_key(top[0]) == key:
                reason += f", and this user {_activity_reason(top[1])}"
                confidence = "high"
            else:
                confidence = "medium"
        elif top:
            suggestion = top[0]
            reason = f"Most active user on this VM: {_activity_reason(top[1])}"
            confidence = "medium" if len(usage) == 1 else "low"
        vm.owner_suggestion, vm.owner_suggestion_reason, vm.owner_suggestion_confidence = suggestion, reason, confidence
        suggested += suggestion is not None
    db.commit()
    return {"suggested": suggested, "people_seen": len(people)}
