"""Application settings, read from environment variables (prefix VCENTRAL_) or a .env file."""
from functools import lru_cache
import ipaddress

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="VCENTRAL_", extra="ignore")

    # Core
    database_url: str = "sqlite:///./vcentral.db"
    secret_key: str = ""  # required: signs login sessions
    static_dir: str = ""  # built frontend; served by the API when set

    # vCenter (read-only service account is enough for Phase 1)
    vcenter_host: str = ""
    vcenter_port: int = 443
    vcenter_user: str = ""
    vcenter_password: str = ""
    vcenter_verify_ssl: bool = True
    # Optional SSO account for managing vCenter's own services (Infrastructure page); defaults to the account above.
    appliance_user: str = ""
    appliance_password: str = ""
    owner_attribute: str = "Owner"  # vCenter custom attribute that, if set, defines the VM owner
    # Shared accounts that say nothing about who owns a VM; never suggested as owners (comma-separated).
    owner_ignore_users: str = "administrator@vsphere.local"

    # Worker (defaults; admins can change intervals on the Settings page)
    sync_interval_seconds: int = 30
    # Sweep the configured subnets (ping/ARP) to find addresses used by devices that are not VMs. 0 = off.
    ip_scan_interval_minutes: int = 15
    metrics_raw_retention_days: int = 7
    metrics_hourly_retention_days: int = 180
    history_retention_days: int = 365

    # Self sign-up on the login page. New accounts always get the read-only Viewer role.
    # Admins can change both on the Settings page; these are the starting values.
    signup_enabled: bool = True
    signup_requires_approval: bool = True

    # Browser SSH / RDP through guacd (Apache Guacamole's proxy), installed next to vCentral.
    remote_access_enabled: bool = True
    guacd_host: str = "127.0.0.1"
    guacd_port: int = 4822

    # Network view: comma-separated CIDRs, e.g. "10.10.20.0/24,10.10.21.0/24"
    subnets: str = ""

    # Agents
    agent_dir: str = ""  # folder with built agent binaries + manifest.json
    agent_report_interval_seconds: int = 20
    agent_inventory_interval_seconds: int = 3600
    # Certificate served by nginx; its fingerprint is pinned by agents (safe with self-signed certs).
    tls_cert_file: str = ""
    agent_pin_certificate: bool = True
    # Ed25519 private key (PEM) that signs tasks for agents. Kept outside the database on purpose.
    task_signing_key_file: str = ""
    task_expiry_minutes: int = 60  # a task not picked up by an agent within this time expires

    # Public address of vCentral as VMs and the admin team reach it (links in messages, agent pushes).
    public_url: str = ""

    # Notifications (to the admin team only)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_security: str = "starttls"  # starttls|ssl|none
    google_chat_webhook: str = ""
    admin_emails: str = ""  # comma-separated admin team addresses

    # Auth
    admin_username: str = "admin"
    admin_password: str = ""  # initial admin password; random one is generated and logged if empty
    session_hours: int = 8
    cookie_secure: bool = True

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def subnet_list(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        nets = []
        for part in self.subnets.split(","):
            part = part.strip()
            if part:
                nets.append(ipaddress.ip_network(part, strict=False))
        return nets


@lru_cache
def get_settings() -> Settings:
    return Settings()
