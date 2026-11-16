from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow

ROLES = ("admin", "operator", "viewer")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to end all sessions
    pending_approval: Mapped[bool] = mapped_column(Boolean, default=False)  # signed up, waiting for an admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # vCenter instance UUID
    bios_uuid: Mapped[str | None] = mapped_column(String(64))
    moid: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    power_state: Mapped[str] = mapped_column(String(16), index=True)

    guest_os: Mapped[str | None] = mapped_column(String(255))
    guest_id: Mapped[str | None] = mapped_column(String(64))
    os_family: Mapped[str] = mapped_column(String(16), default="other", index=True)  # windows|linux|other
    hostname: Mapped[str | None] = mapped_column(String(255))
    primary_ip: Mapped[str | None] = mapped_column(String(45), index=True)
    ip_addresses: Mapped[list] = mapped_column(JSON, default=list)
    last_known_ips: Mapped[list] = mapped_column(JSON, default=list)
    mac_addresses: Mapped[list] = mapped_column(JSON, default=list)

    num_cpu: Mapped[int | None] = mapped_column(Integer)
    memory_mb: Mapped[int | None] = mapped_column(Integer)
    storage_committed_gb: Mapped[float | None] = mapped_column(Float)
    storage_provisioned_gb: Mapped[float | None] = mapped_column(Float)
    guest_disks: Mapped[list] = mapped_column(JSON, default=list)  # [{path, capacity_gb, free_gb, used_pct}]

    tools_status: Mapped[str | None] = mapped_column(String(32))
    tools_version_status: Mapped[str | None] = mapped_column(String(32))
    esxi_host: Mapped[str | None] = mapped_column(String(255))
    cluster: Mapped[str | None] = mapped_column(String(255))
    folder: Mapped[str | None] = mapped_column(String(512))
    datastores: Mapped[list] = mapped_column(JSON, default=list)
    annotation: Mapped[str | None] = mapped_column(Text)
    custom_attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    owner: Mapped[str | None] = mapped_column(String(255), index=True)
    owner_source: Mapped[str | None] = mapped_column(String(16))  # manual|attribute|event
    owner_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Proposed owner for VMs whose creator is unknown (see owners.py); accepted by a person on the Owners page.
    owner_suggestion: Mapped[str | None] = mapped_column(String(255))
    owner_suggestion_reason: Mapped[str | None] = mapped_column(String(512))
    owner_suggestion_confidence: Mapped[str | None] = mapped_column(String(8))  # high|medium|low
    notes: Mapped[str | None] = mapped_column(Text)

    # Latest stats from vCenter quick stats (powered-on VMs only)
    cpu_usage_mhz: Mapped[int | None] = mapped_column(Integer)
    cpu_pct: Mapped[float | None] = mapped_column(Float)
    mem_active_mb: Mapped[int | None] = mapped_column(Integer)
    mem_pct: Mapped[float | None] = mapped_column(Float)
    disk_used_pct_max: Mapped[float | None] = mapped_column(Float)
    uptime_seconds: Mapped[int | None] = mapped_column(Integer)

    managed: Mapped[bool] = mapped_column(Boolean, default=False)  # an active agent is linked

    # Health: "critical" VMs must always be up; problems are re-evaluated every few seconds by the worker.
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    health: Mapped[str] = mapped_column(String(16), default="ok")  # ok|warning|critical
    problems: Mapped[list] = mapped_column(JSON, default=list)  # [{code, severity, message, since}]

    # Onboarding: when this VM was first seen powered on without an agent.
    unmanaged_since: Mapped[datetime | None] = mapped_column(DateTime)

    vcenter_created_at: Mapped[datetime | None] = mapped_column(DateTime)
    boot_time: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class VMHistory(Base):
    """Lifecycle events detected by the sync: created, deleted, power changes, IP changes, renames."""

    __tablename__ = "vm_history"
    __table_args__ = (Index("ix_vm_history_vm_ts", "vm_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(String(512))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class VMMetric(Base):
    """Raw samples, one per sync for each powered-on VM."""

    __tablename__ = "vm_metrics"

    vm_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True, index=True)
    cpu_pct: Mapped[float | None] = mapped_column(Float)
    cpu_mhz: Mapped[int | None] = mapped_column(Integer)
    mem_pct: Mapped[float | None] = mapped_column(Float)
    mem_mb: Mapped[int | None] = mapped_column(Integer)
    disk_used_pct_max: Mapped[float | None] = mapped_column(Float)


class VMMetricHourly(Base):
    """Hourly roll-up of raw samples, kept much longer."""

    __tablename__ = "vm_metrics_hourly"

    vm_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True, index=True)
    cpu_pct_avg: Mapped[float | None] = mapped_column(Float)
    cpu_pct_max: Mapped[float | None] = mapped_column(Float)
    mem_pct_avg: Mapped[float | None] = mapped_column(Float)
    mem_pct_max: Mapped[float | None] = mapped_column(Float)
    disk_used_pct_max: Mapped[float | None] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer, default=0)


class EnrollmentToken(Base):
    """Lets new agents register. Only the SHA-256 hash is stored; the token is shown once."""

    __tablename__ = "enrollment_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))  # first characters, to recognise a token
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    max_uses: Mapped[int | None] = mapped_column(Integer)  # None = unlimited
    uses: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    vm_id: Mapped[int | None] = mapped_column(ForeignKey("vms.id", ondelete="SET NULL"), index=True)
    link_source: Mapped[str | None] = mapped_column(String(16))  # bios_uuid|mac|manual
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|revoked
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    hostname: Mapped[str | None] = mapped_column(String(255), index=True)
    os_family: Mapped[str | None] = mapped_column(String(16))
    os_name: Mapped[str | None] = mapped_column(String(255))
    os_version: Mapped[str | None] = mapped_column(String(128))
    kernel_version: Mapped[str | None] = mapped_column(String(128))
    arch: Mapped[str | None] = mapped_column(String(32))
    bios_uuid: Mapped[str | None] = mapped_column(String(64), index=True)
    serial_number: Mapped[str | None] = mapped_column(String(128))
    macs: Mapped[list] = mapped_column(JSON, default=list)
    ips: Mapped[list] = mapped_column(JSON, default=list)
    agent_version: Mapped[str | None] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(32))  # build the agent runs, e.g. windows-legacy-amd64

    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    enrolled_with: Mapped[str | None] = mapped_column(String(128))  # token name
    enrolled_from_ip: Mapped[str | None] = mapped_column(String(45))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_ip: Mapped[str | None] = mapped_column(String(45))

    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # latest sample
    top_processes: Mapped[dict] = mapped_column(JSON, default=dict)
    inventory: Mapped[dict] = mapped_column(JSON, default=dict)
    inventory_at: Mapped[datetime | None] = mapped_column(DateTime)
    inventory_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentMetric(Base):
    __tablename__ = "agent_metrics"

    agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True, index=True)
    cpu_pct: Mapped[float | None] = mapped_column(Float)
    mem_pct: Mapped[float | None] = mapped_column(Float)
    swap_pct: Mapped[float | None] = mapped_column(Float)
    disk_used_pct_max: Mapped[float | None] = mapped_column(Float)
    disk_read_bps: Mapped[float | None] = mapped_column(Float)
    disk_write_bps: Mapped[float | None] = mapped_column(Float)
    net_rx_bps: Mapped[float | None] = mapped_column(Float)
    net_tx_bps: Mapped[float | None] = mapped_column(Float)
    load1: Mapped[float | None] = mapped_column(Float)


class AgentMetricHourly(Base):
    __tablename__ = "agent_metrics_hourly"

    agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True, index=True)
    cpu_pct_avg: Mapped[float | None] = mapped_column(Float)
    cpu_pct_max: Mapped[float | None] = mapped_column(Float)
    mem_pct_avg: Mapped[float | None] = mapped_column(Float)
    mem_pct_max: Mapped[float | None] = mapped_column(Float)
    disk_used_pct_max: Mapped[float | None] = mapped_column(Float)
    disk_read_bps: Mapped[float | None] = mapped_column(Float)
    disk_write_bps: Mapped[float | None] = mapped_column(Float)
    net_rx_bps: Mapped[float | None] = mapped_column(Float)
    net_tx_bps: Mapped[float | None] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer, default=0)


class PushJob(Base):
    """Agent installation through VMware Tools. Guest credentials are never stored here or anywhere."""

    __tablename__ = "push_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), index=True)
    requested_by: Mapped[str] = mapped_column(String(64))
    guest_user: Mapped[str] = mapped_column(String(128))  # the account name only, for the audit trail
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{ts, message, ok}]
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)  # installer output from the VM
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Script(Base):
    """Saved script in the library. One entry can hold a Linux and a Windows version."""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    linux_script: Mapped[str | None] = mapped_column(Text)  # bash
    windows_script: Mapped[str | None] = mapped_column(Text)  # PowerShell
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="script")  # script|update
    linux_script: Mapped[str | None] = mapped_column(Text)
    windows_script: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    script_id: Mapped[int | None] = mapped_column(ForeignKey("scripts.id", ondelete="SET NULL"))
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"))
    targets: Mapped[dict] = mapped_column(JSON, default=dict)  # how targets were chosen, for re-runs
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class TaskRun(Base):
    """One task on one VM."""

    __tablename__ = "task_runs"
    __table_args__ = (Index("ix_task_runs_agent_status", "agent_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    vm_id: Mapped[int | None] = mapped_column(ForeignKey("vms.id", ondelete="SET NULL"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    vm_name: Mapped[str] = mapped_column(String(255))
    os_family: Mapped[str | None] = mapped_column(String(16))
    # pending -> sent -> running -> succeeded|failed|timeout|cancelled ; or skipped|expired|lost
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str] = mapped_column(Text, default="")
    output_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cron: Mapped[str] = mapped_column(String(64))  # 5-field cron, in UTC
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"))
    targets: Mapped[dict] = mapped_column(JSON, default=dict)
    variables: Mapped[dict | None] = mapped_column(JSON)  # values for {{NAME}} in the script
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_task_id: Mapped[int | None] = mapped_column(Integer)


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    channel: Mapped[str] = mapped_column(String(16))  # email|chat
    recipient: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32))  # digest|test
    subject: Mapped[str] = mapped_column(String(512))
    vm_ids: Mapped[list] = mapped_column(JSON, default=list)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    source_ip: Mapped[str | None] = mapped_column(String(45))
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class IPReservation(Base):
    """An address or range kept free on purpose, or used by something vCentral cannot see (printer, firewall...)."""

    __tablename__ = "ip_reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    start_ip: Mapped[str] = mapped_column(String(45), index=True)
    end_ip: Mapped[str | None] = mapped_column(String(45))  # None = a single address
    label: Mapped[str] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SystemState(Base):
    """Small key/value store shared by the API and the worker (sync status, sync requests)."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
