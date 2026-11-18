"""Install, start or upgrade VMware Tools on VMs from vCentral.

Without VMware Tools, vCenter cannot run anything inside a VM, so how far this gets depends on what the VM has:

* Tools running but outdated (VMware-managed Tools)  -> vCenter's own Tools upgrade (no login needed).
* A vCentral agent online                           -> the 'Install or start VMware Tools' script runs through the
  agent (Linux: installs/starts open-vm-tools; Windows: the installer ISO is mounted first, then setup runs silently).
* No agent, Windows                                 -> the installer ISO is mounted; someone runs setup from the console.
* No agent, Linux                                   -> nothing can be automated; open-vm-tools must be installed
  from the package manager (over SSH / console).
"""
from __future__ import annotations

from pyVmomi import vim

from .agents import is_online, representative_agents
from .db import utcnow
from .models import VM, VMHistory
from .starter_scripts import STARTER_SCRIPTS
from .tasks import TaskError, create_task

SCRIPT_NAME = "Install or start VMware Tools"
OUTDATED = {"guestToolsNeedUpgrade", "guestToolsSupportedOld", "guestToolsTooOld", "guestToolsBlacklisted"}
PRIVILEGE = "Virtual machine > Interaction > Install VMware Tools"
LINUX_MANUAL = ("No vCentral agent online on this VM, so it must be done inside the VM: "
                "sudo apt install -y open-vm-tools (Ubuntu/Debian) or sudo dnf install -y open-vm-tools (RHEL family).")


def _script() -> dict:
    return next(s for s in STARTER_SCRIPTS if s["name"] == SCRIPT_NAME)


def _vc_error(exc: Exception) -> str:
    if isinstance(exc, vim.fault.NoPermission):
        return f"vCentral's vCenter account needs the privilege {PRIVILEGE}"
    msg = getattr(exc, "msg", None) or str(exc)
    if "cdrom" in msg.lower() or "cd-rom" in msg.lower():
        return "The VM has no CD/DVD drive to mount the installer on; add one in the VM settings"
    return f"vCenter refused: {msg}"


def fix_tools(db, client, vms: list[VM], username: str, signing_ok: bool) -> dict:
    """Do the best possible thing per VM. Returns {"results": [...], "task_id": id or None}."""
    agents = representative_agents(db)
    results, via_agent = [], []

    def result(vm, outcome, message, history=None):
        results.append({"vm_id": vm.id, "name": vm.name, "outcome": outcome, "message": message})
        if history:
            db.add(VMHistory(vm_id=vm.id, ts=utcnow(), kind="tools", message=f"{history} by {username}"))

    for vm in vms:
        if vm.is_deleted or vm.power_state != "poweredOn":
            result(vm, "skipped", "The VM is not powered on")
            continue
        running = vm.tools_status == "guestToolsRunning"
        if running and vm.tools_version_status == "guestToolsUnmanaged":
            result(vm, "ok", "VMware Tools (open-vm-tools) is running; it is updated by the guest's package manager")
            continue
        if running and vm.tools_version_status in OUTDATED:
            try:
                client.upgrade_tools(vm.uuid)
                result(vm, "started", "vCenter is upgrading VMware Tools (see Recent Tasks in the vSphere Client). "
                                      "Windows may ask for a reboot to finish.", "VMware Tools upgrade started")
            except LookupError as exc:
                result(vm, "failed", str(exc))
            except Exception as exc:  # noqa: BLE001
                result(vm, "failed", _vc_error(exc))
            continue
        if running:
            result(vm, "ok", "VMware Tools is already running and up to date")
            continue

        agent = agents.get(vm.id)
        online = agent is not None and is_online(agent)
        family = (agent.os_family if agent and agent.os_family in ("linux", "windows") else None) or vm.os_family
        windows = family == "windows"
        not_installed = vm.tools_version_status == "guestToolsNotInstalled"

        mounted_note = ""
        if windows and not_installed:
            try:
                fresh = client.mount_tools_installer(vm.uuid)
                mounted_note = "Installer mounted as a CD drive" if fresh else "Installer was already mounted"
            except LookupError as exc:
                result(vm, "failed", str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                result(vm, "failed", _vc_error(exc))
                continue

        if online and signing_ok:
            via_agent.append((vm, mounted_note))
            continue
        why = "task signing is not set up on this server" if online else "no vCentral agent is online on this VM"
        if windows and not_installed:
            result(vm, "manual", f"{mounted_note}. Because {why}, finish in the VM console: open the 'VMware Tools' "
                                 "drive and run setup64.exe (or install the vCentral agent by command and click again).",
                   "VMware Tools installer mounted")
        elif windows:
            result(vm, "manual", "VMware Tools is installed but not running. The VM is probably asleep: use Wake up. "
                                 f"If it stays stopped, start the 'VMware Tools' service in the VM ({why}).")
        elif family == "linux":
            result(vm, "manual", LINUX_MANUAL if not online else f"Cannot run the script: {why}")
        else:
            result(vm, "manual", "Unknown guest OS; install VMware Tools from inside the VM")

    task_id = None
    if via_agent:
        s = _script()
        try:
            task = create_task(db, name="Install or start VMware Tools", created_by=username,
                               targets={"vm_ids": [vm.id for vm, _ in via_agent]}, linux_script=s["linux_script"],
                               windows_script=s["windows_script"], timeout=s["timeout_seconds"])
            task_id = task.id
            for vm, note in via_agent:
                result(vm, "started", (f"{note}; the" if note else "The") +
                       " agent installs / starts VMware Tools now (see the task for its output)",
                       "VMware Tools install sent to the agent")
        except TaskError as exc:
            for vm, _ in via_agent:
                result(vm, "failed", str(exc))
    order = {v.id: i for i, v in enumerate(vms)}
    results.sort(key=lambda r: order.get(r["vm_id"], 0))
    return {"results": results, "task_id": task_id}
