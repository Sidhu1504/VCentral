"""Install the agent inside a VM through VMware Tools (vSphere Guest Operations).

This works without any network path from vCentral to the VM: files and commands travel through the hypervisor.
The guest username/password is used in memory for this one job and is never written anywhere.

vCenter privileges needed by the service account on the VMs:
  Virtual machine > Guest operations > Guest operation modifications, Guest operation program execution,
  Guest operation queries.
"""
from __future__ import annotations

import logging
import ssl
import threading
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

from pyVmomi import vim
from sqlalchemy import select

from .agents import PLATFORMS, certificate_fingerprint, new_secret, sha256_hex
from .config import get_settings
from .db import SessionLocal, iso, utcnow
from .models import VM, Agent, EnrollmentToken, PushJob
from .vcenter import VCenterClient

log = logging.getLogger(__name__)

MAX_PARALLEL = 5
ENROLL_WAIT_SECONDS = 30  # after installing, how long to wait for the agent to show up
_slots = threading.BoundedSemaphore(MAX_PARALLEL)

NEW_WINDOWS = ("windows9", "windows10", "windows11", "windows12", "windows2019", "windows2022", "windows2025")


class PushError(Exception):
    pass


def platform_for(vm: VM) -> str:
    gid = (vm.guest_id or "").lower()
    if vm.os_family == "windows":
        arch = "amd64" if "64" in gid else "386"
        modern = gid.startswith(NEW_WINDOWS)  # Server 2016+ / Windows 10+ ; older gets the Go 1.20 build
        return f"windows-{arch}" if modern else f"windows-legacy-{arch}"
    if vm.os_family == "linux":
        if "arm" in gid:
            return "linux-arm64"
        return "linux-amd64" if "64" in gid or not gid else "linux-386"
    raise PushError("Unknown guest OS: only Windows and Linux VMs can receive the agent")


# -- vSphere guest operations ----------------------------------------------------------------------

class GuestSession:
    """Thin wrapper over the vSphere guest operations API for one VM and one guest account."""

    def __init__(self, client: VCenterClient, vm: VM, username: str, password: str):
        client.ensure_connected()
        content = client.si.content
        self.vm_ref = content.searchIndex.FindByUuid(None, vm.uuid, True, True)
        if self.vm_ref is None:
            raise PushError("VM not found in vCenter")
        self.esxi_host = vm.esxi_host
        self.gom = content.guestOperationsManager
        self.auth = vim.vm.guest.NamePasswordAuthentication(username=username, password=password,
                                                            interactiveSession=False)
        s = get_settings()
        self.ssl_ctx = ssl.create_default_context()
        if not s.vcenter_verify_ssl:
            self.ssl_ctx.check_hostname = False
            self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def validate(self) -> None:
        try:
            self.gom.authManager.ValidateCredentialsInGuest(self.vm_ref, self.auth)
        except vim.fault.InvalidGuestLogin:
            raise PushError("The VM rejected the username or password")
        except vim.fault.GuestOperationsUnavailable:
            raise PushError("VMware Tools is not running or not ready in this VM")
        except vim.fault.NoPermission:
            raise PushError("vCentral's vCenter account lacks the 'Guest operations' privileges on this VM")

    def _url(self, url: str) -> str:
        return url.replace("://*", f"://{self.esxi_host}", 1) if "://*" in url else url

    def upload(self, path: str, data: bytes, posix_mode: int | None = None) -> None:
        attrs = (vim.vm.guest.FileManager.PosixFileAttributes(permissions=posix_mode) if posix_mode is not None
                 else vim.vm.guest.FileManager.WindowsFileAttributes())
        url = self.gom.fileManager.InitiateFileTransferToGuest(self.vm_ref, self.auth, path, attrs, len(data), True)
        req = urllib.request.Request(self._url(url), data=data, method="PUT",
                                     headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=300, context=self.ssl_ctx) as resp:
            resp.read()

    def download(self, path: str) -> bytes:
        info = self.gom.fileManager.InitiateFileTransferFromGuest(self.vm_ref, self.auth, path)
        with urllib.request.urlopen(self._url(info.url), timeout=60, context=self.ssl_ctx) as resp:
            return resp.read(1 << 20)

    def delete(self, path: str) -> None:
        try:
            self.gom.fileManager.DeleteFileInGuest(self.vm_ref, self.auth, path)
        except Exception:
            pass

    def run(self, program: str, arguments: str, timeout: int = 240) -> int:
        spec = vim.vm.guest.ProcessManager.ProgramSpec(programPath=program, arguments=arguments)
        pid = self.gom.processManager.StartProgramInGuest(self.vm_ref, self.auth, spec)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            procs = self.gom.processManager.ListProcessesInGuest(self.vm_ref, self.auth, [pid])
            if procs and procs[0].endTime is not None:
                return procs[0].exitCode
            time.sleep(2)
        raise PushError(f"The installer did not finish within {timeout} seconds")


# -- the job -----------------------------------------------------------------------------------------

class Job:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def step(self, message: str, ok: bool = True) -> None:
        with SessionLocal() as db:
            job = db.get(PushJob, self.job_id)
            job.steps = [*job.steps, {"ts": iso(utcnow()), "message": message, "ok": ok}]
            db.commit()

    def finish(self, status: str, error: str | None = None, output: str | None = None) -> None:
        with SessionLocal() as db:
            job = db.get(PushJob, self.job_id)
            job.status, job.error, job.output, job.finished_at = status, error, output, utcnow()
            db.commit()


def _one_time_token(vm: VM, requested_by: str) -> str:
    token = new_secret("vct_")
    with SessionLocal() as db:
        db.add(EnrollmentToken(name=f"push to {vm.name} by {requested_by}"[:128], token_hash=sha256_hex(token),
                               prefix=token[:10], created_by=requested_by, max_uses=1,
                               expires_at=utcnow() + timedelta(hours=1)))
        db.commit()
    return token


SESSION_FACTORY = GuestSession  # replaced in tests


def run_push(job_id: int, username: str, password: str, server_url: str, session_factory=None) -> None:
    """Executes one push job. Runs in a background thread of the API process."""
    session_factory = session_factory or SESSION_FACTORY
    job = Job(job_id)
    with SessionLocal() as db:
        pj = db.get(PushJob, job_id)
        vm = db.get(VM, pj.vm_id)
        requested_by, started = pj.requested_by, pj.created_at
        pj.status = "running"
        db.commit()
    output = None
    client = None
    try:
        s = get_settings()
        platform = platform_for(vm)
        binary = Path(s.agent_dir or "/nonexistent") / PLATFORMS[platform]
        if not binary.is_file():
            raise PushError(f"The {platform} agent is not available on this server")
        windows = platform.startswith("windows")

        job.step(f"Connecting to the VM through VMware Tools as '{username}'")
        client = VCenterClient(s)
        guest = session_factory(client, vm, username, password)
        guest.validate()
        job.step("Login accepted")

        token = _one_time_token(vm, requested_by)
        fp = certificate_fingerprint()
        args = f"install --server {server_url} --token {token}" + (f" --fingerprint {fp}" if fp else "")
        if windows:
            exe, logf = r"C:\Windows\Temp\vcentral-agent-setup.exe", r"C:\Windows\Temp\vcentral-install.log"
            program, cmdline = r"C:\Windows\System32\cmd.exe", f'/c ""{exe}" {args} > "{logf}" 2>&1"'
        else:
            exe, logf = "/usr/local/bin/vcentral-agent.download", "/var/tmp/vcentral-install.log"
            program, cmdline = "/bin/sh", f"-c '{exe} {args} > {logf} 2>&1'"

        job.step(f"Copying the {platform} agent ({binary.stat().st_size // 1048576} MB) into the VM")
        try:
            guest.upload(exe, binary.read_bytes(), None if windows else 0o755)
        except Exception as exc:
            hint = "use Administrator" if windows else "use root (the agent installs to /usr/local/bin)"
            raise PushError(f"Could not copy the agent into the VM ({hint}): {exc}")

        job.step("Running the installer")
        try:
            code = guest.run(program, cmdline)
        finally:
            try:
                output = guest.download(logf).decode(errors="replace")[-4000:]
            except Exception:
                output = None
            guest.delete(logf)
            guest.delete(exe)
        if code != 0:
            raise PushError(f"The installer exited with code {code}" + (f": {output.strip()[-300:]}" if output else ""))
        job.step("Agent installed and running as a service")

        # Wait briefly for the agent's enrollment so the result is visible straight away.
        for _ in range(ENROLL_WAIT_SECONDS // 2):
            with SessionLocal() as db:
                agent = db.scalar(select(Agent).where(Agent.vm_id == vm.id, Agent.status == "active",
                                                      Agent.enrolled_at >= started))
            if agent:
                job.step(f"Agent enrolled and linked to {vm.name}")
                break
            time.sleep(2)
        else:
            job.step("Agent installed; it will appear on the Agents page within a minute", ok=True)
        job.finish("succeeded", output=output)
    except PushError as exc:
        job.step(str(exc), ok=False)
        job.finish("failed", error=str(exc), output=output)
    except Exception as exc:  # unexpected vSphere / network error
        log.exception("Push job %s failed", job_id)
        job.step(f"Failed: {type(exc).__name__}: {exc}", ok=False)
        job.finish("failed", error=f"{type(exc).__name__}: {exc}", output=output)
    finally:
        if client is not None:
            client.disconnect()


def start_push(job_id: int, username: str, password: str, server_url: str, session_factory=None) -> None:
    """Start a push in a background thread (at most MAX_PARALLEL at once); credentials stay in memory."""
    def worker():
        with _slots:
            run_push(job_id, username, password, server_url, session_factory)
    threading.Thread(target=worker, name=f"push-{job_id}", daemon=True).start()
