# vCentral

One web console for every VM in your vSphere environment. It shows which VMs exist, who owns them, whether they are on, which IP they hold, and how much CPU, memory and disk they use. It also records each change as it happens.

vCentral reads VM data from vCenter, which needs no credentials inside the VMs. The optional **vCentral agent** adds in-guest detail: real memory use, every disk, network and disk throughput, top processes, installed software, services and open ports.

## What's included

| Page | What you get |
|---|---|
| **Dashboard** | Critical problems and warnings, VMs without an IP, a "Needs attention" list, VM totals, VMs created/deleted over 30 days, OS breakdown, top CPU / memory / disk consumers, IP usage, recent changes |
| **Virtual machines** | Every VM with power state, agent status, OS, IP (or last known IP), owner, CPU / memory / fullest-disk usage, host and folder. Search, filters, sorting, CSV export |
| **VM detail** | Usage graphs (1 hour to 90 days, from the agent or vCenter), network and disk I/O, disks, top processes, installed software, services, listening ports, IPs and MACs, owner and notes, full change history |
| **Agents** | Every agent with online/offline state and the VM it belongs to; enrollment tokens with ready-to-paste install commands; agent downloads |
| **IP & subnets** | Map of every address in your subnets and what holds it: VMs, gateways, DNS, ESXi, vCenter, templates, reservations, powered-off VMs and other devices found by a network scan. Conflicts, and the next free addresses |
| **Reports** | Inventory, owners, problems, agent coverage, new/deleted VMs, resource usage (idle / overloaded VMs), powered-off VMs, IP usage, task history: view, filter, download as CSV, or print / save as PDF |
| **Activity** | VMs created, deleted, powered on/off, renamed, resized, or changing IP |
| **Settings** | Agent metrics interval (10 s – 1 min), inventory interval (15 min – 6 h), vCenter sync interval (20 s – 5 min); applied without a restart |
| **Onboarding** | Powered-on VMs without the agent, install through VMware Tools or a one-time command, digest to the admin team, messages sent |
| **Users / Audit log** | Local accounts with Admin / Operator / Viewer roles; every sign-in and change is logged |

### VM health and critical VMs

vCentral re-checks every VM every 10 seconds and raises these problems:

| Problem | When | Severity |
|---|---|---|
| **No IP address** | A powered-on VM has no usable IP (none, or only a 169.254.x.x / fe80:: fallback address) | Warning, or **Critical** for critical VMs |
| **Agent stopped reporting** | The VM's agent missed three reports in a row | Warning, or **Critical** for critical VMs |
| **Critical VM is powered off / suspended / deleted** | Only for VMs marked critical | **Critical** |
| **Not monitored** | A critical VM has neither VMware Tools nor an agent, so vCentral cannot see its state | Warning |

Mark a VM as **Critical** (switch on the VM page; admins and operators) for machines that must always be up. Problems appear on the dashboard and VM list, and each start and end is recorded in Activity. Notifications (Google Chat, email) for these events come in Phase 5.

A lost IP is detected through **VMware Tools**, which reports to vCenter through the hypervisor and keeps working when the VM's network is down, so detection takes about one vCenter sync interval plus 10 seconds. (A VM that loses its IP also cannot reach vCentral, so its agent shows "stopped reporting" at the same time.)

### How owners are found

For each VM, vCentral uses the first of these that is available:
1. **Set manually** in vCentral (VM detail → Owner & notes)
2. **vCenter custom attribute** named `Owner` (configurable with `VCENTRAL_OWNER_ATTRIBUTE`)
3. **vCenter creation event**: the account that created, cloned or deployed the VM

vCenter keeps events for about 30 days by default, so VMs created earlier than that show **Unknown** until an owner is set.

**Owners page (finding owners of older VMs).** Every hour (or on **Find owners now**) vCentral reads vCenter's recent event history and suggests an owner for each VM with an unknown owner:
- **High**: a vCenter user's name appears in the VM name, folder or notes, *and* that user has been using the VM (console, power on/off, settings changes).
- **Medium**: the name matches but there is no recent use, or exactly one user has been using the VM.
- **Low**: several users have been using it; the most active one is suggested.

Each suggestion shows the reason. Accept it one by one, all **high confidence** at once, or select VMs (Owners page or VM list) and **Set owner for selected**. Shared accounts such as `administrator@vsphere.local` and the vCentral service account are never suggested (add more with `VCENTRAL_OWNER_IGNORE_USERS`). Accepted owners are kept as "Accepted suggestion"; manual owners and the vCenter `Owner` attribute always take priority.

**New and deleted VMs** from the last 7 days are listed on the dashboard, with who created them; the full record is under Activity.

### Where the numbers come from

- **CPU** and **active memory** come from vCenter performance quick stats. Active memory is what the guest touched recently, so it is usually lower than the "used" figure inside the OS.
- **Disk usage per drive or mount point** and **IP addresses** come from VMware Tools. VMs where Tools is not running show none of these.
- vCenter samples are taken every sync (30 s by default) and agent samples every report (20 s by default). Both are kept for 7 days at full detail and 180 days as hourly averages. Graphs for 6 h and 24 h are averaged to at most 720 points; 1 h and 6 h graphs refresh themselves every 15 seconds.
- Storage: at 10-second agent reports, 300 agents produce about 2.6 million samples a day (roughly 2–3 GB for the 7 days of full detail with indexes, estimated). Use 20 or 30 seconds if disk space is tight.

## Getting the agent onto every VM (onboarding)

vCentral is used by the **admin team only**: there is no sign-in or page for VM owners, and nothing is sent to them.

The **Onboarding** page lists every powered-on VM without an agent, its owner (so the team knows whom to ask), how long it has been without one, whether VMware Tools is running, and the last install attempt. Admins and operators install the agent in one of three ways:

| Way | How |
|---|---|
| **Install through VMware Tools** (Onboarding or VM page) | Type the VM's **root** (Linux) or **Administrator** (Windows) login once. vCentral copies the agent into the VM through VMware Tools and installs it, even if the VM's network is blocked. The login is used for this one installation, held only in memory, and never saved or logged. Needs VMware Tools running. |
| **One-time command** (same dialog) | vCentral creates a single-use token (valid 24 h) and shows the command to paste on the VM. |
| **Enrollment token** (Agents page) | For scripts, VM templates or batches. |

**Self-enrolling VMs from a template.** On the Agents page create a token **for a VM template** (unlimited uses, no expiry), then on the template VM run the shown command, which includes `--template` (Linux) or `-Template` (Windows). The agent checks the token and then stays idle on the template itself. Every VM deployed or cloned from the template gets a new BIOS UUID, so its agent notices and enrolls itself within about half a minute of first boot, with no one logging in. Revoke the token on the Agents page to stop new enrollments. Templates are optional: alternatively, a vCenter **customization specification** can run the normal install command at first boot.

**Onboarding digest** (Settings; off until you turn it on): a summary of the VMs still without an agent, **overdue VMs first** (default: more than 14 days), then **grouped by owner**, sent to `VCENTRAL_ADMIN_EMAILS` and the **Google Chat** space every N days. **Send digest now** is on the Onboarding page, **Send test** on Settings, and every message is listed under Onboarding → Messages sent.

**vCenter privileges for VMware Tools installs.** Give the vCentral service account a role with Read-only plus *Virtual machine → Guest operations → Guest operation modifications, Guest operation program execution, Guest operation queries*, on the VMs (or the vCenter root, propagated). The vCentral server must reach the **ESXi hosts on TCP 443** (files are copied through the host). If the hosts' certificates are not trusted by the vCentral server, set `VCENTRAL_VCENTER_VERIFY_SSL=false`.

## IP addresses: when is an address free?

The **IP & subnets** page shows an address as free only when nothing claims it:

| Taken by | How vCentral knows |
|---|---|
| **VM** | VMware Tools reports the IP, or the network scan sees the VM's MAC address on it (VMs without Tools) |
| **Infrastructure** | From vCenter: the default gateways and DNS servers your VMs use (their own routing tables), each ESXi host's management IP, gateway and DNS servers, and the vCenter Server's IP |
| **Template** | IPs a template has, and the IPs a VM had before it was converted to a template |
| **Reserved** | Set on this page (one address or a range, e.g. a DHCP pool or a printer). Admins and operators |
| **Powered-off VM** | The last IP of a VM that is off: it takes the address back when it starts |
| **Other device** | It answered the network scan but is not a VM (printers, firewalls, laptops...). Its MAC address is shown |

**The network scan:**
- **What it does:** every 15 minutes (Settings → *Network scan*, or Off), the vCentral server pings every address in `VCENTRAL_SUBNETS` and reads its own ARP table. ARP also catches devices on the same subnet that block ping.
- **Ping:** the installer allows ping for the `vcentral` group only (`/etc/sysctl.d/60-vcentral-ping.conf`), because unprivileged ping is often switched off on Ubuntu. Without it, the scan uses TCP connection attempts to common ports instead.
- **Memory:** devices that stop answering are kept for 7 days and shown with when they were last seen.
- **Warnings:** conflicts and suspicious overlaps are highlighted, for example something answering on a powered-off VM's IP, or a VM using a reserved or template address.
- **Network security tools** may notice the scan. Tell your network or security team, or switch it off.

## Reports

**Reports** in the menu: open one to see it in vCentral, filter and sort it, then **Download CSV** (opens in Excel) or **Print / PDF**. Reports are built from live data each time, and some can cover the last 7, 30 or 90 days. Everyone, including viewers, can open reports, and every CSV download is in the audit log.

| Report | Shows |
|---|---|
| VM inventory | Every VM: power, health, OS, IP, owner, size, host, folder, agent |
| VMs by owner | VMs, vCPU, RAM and storage per owner, and VMs without a known owner |
| Current problems | VMs with a health problem now |
| Agent coverage | Which VMs have an agent, its state and version |
| New and deleted VMs | Created, deleted, converted to template or back, with owner |
| Resource usage | Average and peak CPU, memory and disk per VM, flagging **idle** (shrink or switch off) and **busy** VMs |
| Powered-off VMs | Since when they are off, and the storage and IPs they still hold |
| IP address usage | Every taken address and what holds it |
| Task history | Tasks in the period and how many VMs succeeded or failed |

## Sign-up

The login page has **Create an account**. Accounts created there always get the **Viewer** role (read-only); only an admin can give a different role (Users → Edit). On **Settings → Sign-up**, admins choose:
- **Allow sign-up** (on by default).
- **New accounts need admin approval** (on by default; recommended). Requests appear at the top of **Users** with Approve / Reject, and the admin team is told by Google Chat or email when those are set up. Until approved, the person cannot sign in.

Viewers can see every VM's IPs and owners and agent inventories (software, services, open ports); they see task names and status but not scripts or output. Keep approval on unless everyone who can reach vCentral should see that. Sign-ups are limited to 5 per IP address per hour and are recorded in the audit log.

## Remote access: SSH and RDP in the browser

On a VM's page, admins and operators click **Connect (SSH / RDP)**, pick the VM's address, type the login **on the VM**, and work in a full-window session in the browser, with no client software. It includes Ctrl+Alt+Del and Win keys for Windows, clipboard exchange, and full-screen mode, and it resizes with the window.

- **How it works:** browser ⇄ vCentral (WebSocket, same HTTPS port) ⇄ **guacd** (Apache Guacamole's proxy, on the vCentral server, listening only on 127.0.0.1) ⇄ the VM on port 22 or 3389. The installer sets guacd up from Ubuntu's own packages.
- **The vCentral server must reach the VMs** on the SSH and RDP ports. The admin's PC does not need to.
- **Logins are not stored.** The password goes to the VM for that session only; it is kept in memory for at most 60 seconds until the session opens. For SSH you can leave it empty and type it at the VM's own login prompt.
- **Audit:** every session is recorded with who connected, the VM, address, protocol, VM user, and duration (Audit log: `remote.connect` / `remote.disconnect`).
- **Limited to the VM's own addresses:** sessions can only go to an IP vCentral knows for that VM (from vCenter or the agent), so vCentral cannot be used as a general proxy.
- **SSH key login is not offered:** the guacd version in Ubuntu 24.04 (1.3.0) cannot use current OpenSSH key formats.
- **VMs with no IP, or SSH/RDP turned off:** the Connect dialog links to **VMware Remote Console** (`vmrc://`). It shows the VM's screen through vCenter even when the VM's network is down. It needs VMRC installed on the admin's PC, and the admin signs in with their own vCenter account.
- **To turn browser SSH/RDP off**, set `VCENTRAL_REMOTE_ACCESS_ENABLED=false`.

## Tasks: running scripts on VMs

**Tasks → Run a task** runs a script on one VM, a selection, or every VM matching a filter (Linux/Windows, critical, name or folder, online only). A task can hold a **bash script for Linux** (runs as root) and a **PowerShell script for Windows** (runs as SYSTEM); each VM gets the one for its OS. You see live output, exit code and duration per VM, can cancel, and can re-run on the VMs that failed. Agents pick tasks up with their next report (your agent interval, 10–60 s); a task not picked up within an hour (an offline VM) expires.

| Page | What it is for |
|---|---|
| **Tasks** | History of every task, its progress and the output of each VM |
| **Script library** | Saved scripts (Linux and/or Windows version, timeout). Run one from here or from a VM's page |
| **Schedules** | Run a library script on a cron schedule (in UTC) on VMs chosen by a filter, re-evaluated at every run |
| **Agents → Update outdated agents** | Agents download the new version from vCentral, check its SHA-256, replace themselves and restart |

**Working with scripts:**
- **Upload from your computer.** Every script box has *upload a file* and accepts drag-and-drop; **Script library → Upload script** creates a script from a file.
  - Linux accepts `.sh`, `.py` and `.txt`. Windows line endings are converted, since they break bash, and Python files are wrapped to run with `python3`.
  - Windows accepts `.ps1`, `.bat`, `.cmd` and `.txt`. Batch files are wrapped to run with `cmd.exe`, and UTF-16 files saved by Windows are decoded.
- **Variables.** Write `{{NAME}}` in a script, with no quotes around it, e.g. `systemctl restart -- {{SERVICE}}`. When the script is run, vCentral asks for the value; a schedule stores its values.
  - Each value is inserted as one quoted literal for bash or PowerShell, so a value can never add commands.
  - Variables are for bash and PowerShell scripts, not for wrapped Python or batch files.
- **Starter scripts** (Script library, admins): disk usage, top processes, pending reboot/updates, who is logged in, listening ports, time sync, failed services, service status, restart a service, and the last lines of a log file. Each has a Linux and a Windows version, and all only read information except *Restart a service*.
- **Export / Import** the whole library as a JSON file, for backup or to copy it to another vCentral server. Scripts with the same name are skipped or replaced, as you choose. Each script can also be downloaded as `.sh` / `.ps1`.
- **Results (CSV)** on a task page downloads VM, status, exit code, timing and output for every VM.

**Quick search:** press **Ctrl+K** (⌘K on Mac) on any page and type a VM name, IP, hostname, owner or folder to jump to it.

**Who can do what:** admins write ad-hoc scripts and manage the library and schedules; operators run library scripts, cancel and read output; viewers see task status but not output (it can contain sensitive data). Every task, script change and schedule is in the audit log.

**How agents decide to run something.** Each task is signed with an Ed25519 key kept in `/etc/vcentral/task-signing.key` (outside the database). An agent runs a task only if the signature matches the key it learned when it enrolled, the task names that agent, it has not expired, and it has not run before (agents remember the runs they executed). Someone who can only change the database cannot make agents run commands.

- **Keep the key with your backups.** If it is lost or replaced, agents refuse all tasks until they are reinstalled.
- Agents installed before version 0.4.0 cannot run tasks or update themselves: reinstall them once (**Install agent** on the Onboarding or VM page). From then on, updates go through vCentral.

## The vCentral agent

A single small program (5–8 MB, no dependencies) that runs as a service on each VM.

| | |
|---|---|
| **Supported** | Windows Server 2008 R2, 2012, 2012 R2, 2016, 2019, 2022 and Windows 7–11 (32/64-bit); Linux with systemd: RHEL / CentOS 7+, AlmaLinux, Rocky, Ubuntu 16.04+, Debian 9+, SUSE (x86, x86_64, ARM64) |
| **Reports every 20 seconds** (10 s – 1 min, see Settings) | CPU, memory used, swap, load (Linux), uptime, current IP addresses, each disk/drive, disk read/write and network receive/send rates, top 5 processes by CPU and by memory |
| **Inventory every hour** (15 min – 6 h, or on request) | OS and kernel, CPU model, network interfaces, installed software (rpm/dpkg, Windows "Programs and Features"), services and their state, listening TCP ports |
| **Network** | Only **outbound HTTPS (443) from the VM to vCentral**. Nothing listens on the VM, so changing DHCP addresses and inbound firewalls do not matter. `HTTPS_PROXY` is honoured |
| **Tasks** | Runs scripts and updates from vCentral only when they are signed with vCentral's task key (see *Tasks*). Install with `--disable-tasks` for a machine that should only report |

Windows Server 2008 R2–2012 R2 and Windows 7/8.1 receive a "legacy" build (made with Go 1.20, the last release that supports them); the installer picks it automatically.

### Installing agents

1. **Agents → Enrollment tokens → New token.** Choose how long it is valid and how many machines may use it (one VM, a batch, or unlimited).
2. Copy the command shown (Linux, Windows or manual) and run it on the VM:
   - **Linux:** as a user with sudo. Needs systemd, `curl` or `wget`, and `openssl`.
   - **Windows:** in PowerShell opened with *Run as administrator*. Systems without .NET 4.5 cannot use TLS 1.2 from PowerShell: download the agent from **Agents → Downloads**, copy it over, and run the manual command instead.
3. The agent appears on the Agents page within a minute and is linked to its VM automatically.

Give a token to a VM owner so they can install the agent themselves: they never have to share credentials with you, and you never have to ask for them.

### How agents are matched to VMs

By **BIOS UUID** (the same ID vCenter shows; both byte orders are accepted), otherwise by **MAC address** from the VM's virtual hardware. If neither matches, the agent shows as *Not linked*; use **Link VM** to choose the VM by hand (manual links are never changed automatically).

### Security model

- **Tokens** are shown once and stored only as a hash. They can expire, be limited to N machines, and be revoked; agents already enrolled keep working.
- Each agent gets its **own secret** at enrollment (stored hashed on the server; on the VM in a file readable only by root / SYSTEM + Administrators).
- **Certificate pinning:** install commands carry the SHA-256 fingerprint of vCentral's HTTPS certificate. The command checks it before downloading anything, and the agent accepts only that certificate. This keeps a self-signed certificate safe. **If you replace the certificate, re-run the install command on the VMs** (or set `VCENTRAL_AGENT_PIN_CERTIFICATE=false` when using a certificate your VMs already trust).
- **Revoke** an agent on the Agents page to stop accepting its reports immediately.
- **Cloned VMs:** a clone of a VM with an agent has a different BIOS UUID. The server refuses the copied credentials; if the original token was reusable, the clone enrolls itself as a new agent, otherwise re-run install on it.
- Reinstalling on the same VM replaces the old agent record automatically.

### On the VM

| Task | Linux | Windows |
|---|---|---|
| Status and connection test | `sudo vcentral-agent status` | `& "$env:ProgramFiles\vCentral\vcentral-agent.exe" status` |
| Logs | `journalctl -u vcentral-agent` | `C:\ProgramData\vCentral\agent.log` |
| See what it sends | `vcentral-agent metrics` / `inventory` | same, with the `.exe` |
| Uninstall | `sudo vcentral-agent uninstall` | `& "$env:ProgramFiles\vCentral\vcentral-agent.exe" uninstall` (as Administrator) |

Files: Linux `/usr/local/bin/vcentral-agent`, `/etc/vcentral-agent/agent.json`, systemd unit `vcentral-agent`. Windows `C:\Program Files\vCentral\`, `C:\ProgramData\vCentral\`, service "vCentral Agent" (restarts automatically if it stops).

## Requirements

- An **Ubuntu 22.04 or 24.04** VM (native install, recommended), or any Linux VM with Docker. Suggested size for about 300 VMs: 4 vCPU, 8 GB RAM, 100 GB disk. Most of the disk is metric history.
- A **static IP outside your DHCP range**.
- Network: from vCentral **to vCenter and the ESXi hosts on TCP 443**; from your team and all VMs **to vCentral on TCP 443**; to your SMTP server and `chat.googleapis.com` for messages. Internet access during install/upgrade (Ubuntu packages, Node.js, Go, PyPI, npm).
- A vCenter **service account**:
  1. In vCenter, create a user such as `svc-vcentral@vsphere.local`.
  2. *Administration → Access Control → Roles*: clone **Read-only** as `vCentral`, and add *Virtual machine → Guest operations → Guest operation modifications, Guest operation program execution, Guest operation queries* (for installing agents through VMware Tools).
  3. *Global Permissions*: give the account the `vCentral` role and tick **Propagate to children**.

## Install on Ubuntu without Docker (recommended)

Copy the vCentral folder to the server (for example to `/opt/vcentral`), then:

```bash
cd /opt/vcentral
sudo deploy/native/install.sh                 # 1st run: installs packages, creates /etc/vcentral/vcentral.env, stops
sudo nano /etc/vcentral/vcentral.env          # vCenter host/user/password, VCENTRAL_SUBNETS, VCENTRAL_PUBLIC_URL,
                                              # VCENTRAL_ADMIN_PASSWORD, SMTP / Google Chat / admin emails
sudo deploy/native/install.sh vcentral.example.local 10.10.20.5   # 2nd run: builds, sets up and starts everything
```

The names/IPs on the second run go into the self-signed HTTPS certificate: list **every name and IP people and agents use** to reach vCentral (agents pin this certificate). The second run takes about 10–20 minutes the first time (it builds the UI and all agent versions).

What it sets up:

| Part | Where |
|---|---|
| Code, Python environment, built UI and agents | `/opt/vcentral` (owned by root, read-only to the service) |
| Build tools (Node.js, Go, Go 1.20 for legacy Windows agents) | `/opt/vcentral/toolchains`, nothing installed system-wide |
| Configuration and secrets | `/etc/vcentral/vcentral.env` (root:vcentral, 640) |
| HTTPS certificate | `/etc/vcentral/certs/` (replace with your CA's certificate if you have one, then `sudo systemctl reload nginx`) |
| Database | PostgreSQL from Ubuntu, database and user `vcentral` (password generated) |
| Services | `vcentral-api` (localhost:8000) and `vcentral-worker`, both systemd, running as the unprivileged `vcentral` user |
| Web server | nginx on 443 (80 redirects), site `/etc/nginx/sites-available/vcentral` |

Day-to-day (native):

| Task | Command |
|---|---|
| Status | `systemctl status vcentral-api vcentral-worker` |
| Logs | `journalctl -u vcentral-api -u vcentral-worker -f` (look for `Sync (scheduled) ok`) |
| After editing the config | `sudo systemctl restart vcentral-api vcentral-worker` |
| Upgrade | Copy the new version over `/opt/vcentral`, then `sudo deploy/native/install.sh` |
| Back up | `sudo deploy/native/backup.sh` (to `/var/backups/vcentral`, keeps 14). Nightly: `echo '0 2 * * * root /opt/vcentral/deploy/native/backup.sh >/dev/null' \| sudo tee /etc/cron.d/vcentral-backup` |
| Restore | `gunzip -c FILE.sql.gz \| sudo -u postgres psql -d vcentral` |
| Locked-out admin | `sudo -u vcentral bash -c 'set -a; . /etc/vcentral/vcentral.env; cd /opt/vcentral/backend; /opt/vcentral/venv/bin/python -c "from app.db import SessionLocal; from app.models import User; from app.security import hash_password; db=SessionLocal(); u=db.query(User).filter_by(username=\"admin\").one(); u.password_hash=hash_password(\"TempPass12345\"); u.must_change_password=True; u.is_active=True; u.token_version+=1; db.commit()"'` |

Keep a copy of `/etc/vcentral` with your backups: it holds the secrets, the certificate and the task signing key (without that key, agents must be reinstalled before they accept tasks).

## Install with Docker (alternative)

```bash
git clone <this repository> vcentral && cd vcentral
scripts/install.sh            # 1st run: creates .env with random secrets, then stops
nano .env                     # set vCenter host/user/password, VCENTRAL_SUBNETS, VCENTRAL_ADMIN_PASSWORD
scripts/install.sh            # 2nd run: creates an HTTPS certificate and starts everything
```

Open `https://<vm-ip>/` and sign in as `admin` with the password from `.env`. You must choose a new password at first sign-in. The first vCenter sync starts within a minute.

The default certificate is self-signed, so browsers show a warning. It must include the name or IP that people and agents use to reach vCentral (`scripts/gen-cert.sh vcentral.example.local 10.0.0.5` to choose them). To use your own CA's certificate, replace `deploy/certs/vcentral.crt` and `deploy/certs/vcentral.key` and run `docker compose up -d`. Replacing the certificate means re-running the agent install command on VMs (see *Security model*).

### What runs

| Container | Role |
|---|---|
| `nginx` | HTTPS on ports 443 and 80 (80 redirects to 443). The only container with published ports |
| `app` | API, web UI, agent endpoints and agent downloads (all agent builds are inside the image) |
| `worker` | Syncs with vCenter on a schedule, handles "Sync now", rolls up and prunes metrics |
| `db` | PostgreSQL 16 with TimescaleDB for metric history |

## Day-to-day operations (Docker)

| Task | Command |
|---|---|
| View logs | `docker compose logs -f app worker` |
| Check the vCenter sync | Header badge in the UI, or `docker compose logs worker \| grep Sync` |
| Restart after editing `.env` | `docker compose up -d` |
| Upgrade to a new version | `git pull && docker compose up -d --build` |
| Back up | `scripts/backup.sh` (writes `backups/*.sql.gz`, keeps the last 14). Schedule it with cron |
| Restore | `gunzip -c backups/FILE.sql.gz \| docker compose exec -T db psql -U vcentral -d vcentral` |
| Locked-out admin | `docker compose exec app python -c "from app.db import SessionLocal; from app.models import User; from app.security import hash_password; db=SessionLocal(); u=db.query(User).filter_by(username='admin').one(); u.password_hash=hash_password('TempPass12345'); u.must_change_password=True; u.is_active=True; u.token_version+=1; db.commit()"` |

Keep a copy of `.env` with your backups. It holds the session secret and the database password.

## Security notes

- Sign-in sessions use an HttpOnly, SameSite=Strict cookie over HTTPS. State-changing requests also need a custom header, which blocks cross-site form submissions.
- Five failed sign-ins for the same user from the same address lock that pair out for 15 minutes.
- Passwords are stored with bcrypt and must be at least 10 characters. New and reset accounts must set their own password at first sign-in. Changing a role, disabling an account or resetting a password signs that user out everywhere.
- CSV exports neutralise spreadsheet formulas in VM names and notes.
- The vCenter password is stored in `.env`. Keep that file at `chmod 600` and use a dedicated read-only service account.

## Configuration reference

All settings are environment variables in `.env`. See [.env.example](.env.example) for the full list with comments.

| Variable | Default | Purpose |
|---|---|---|
| `VCENTRAL_VCENTER_HOST` / `_USER` / `_PASSWORD` | – | vCenter connection |
| `VCENTRAL_VCENTER_VERIFY_SSL` | `true` | Set `false` only for a self-signed vCenter certificate |
| `VCENTRAL_SUBNETS` | – | CIDRs shown on the IP page and scanned, comma-separated |
| `VCENTRAL_IP_SCAN_INTERVAL_MINUTES` | `15` | Starting value for the network scan (Settings page; 0 = off) |
| `VCENTRAL_OWNER_ATTRIBUTE` | `Owner` | vCenter custom attribute that holds the owner |
| `VCENTRAL_OWNER_IGNORE_USERS` | `administrator@vsphere.local` | Shared accounts never suggested as owners, comma-separated |
| `VCENTRAL_SIGNUP_ENABLED` / `_REQUIRES_APPROVAL` | `true` / `true` | Starting values for sign-up (Settings page) |
| `VCENTRAL_REMOTE_ACCESS_ENABLED` | `true` | Browser SSH/RDP (through guacd) |
| `VCENTRAL_GUACD_HOST` / `_PORT` | `127.0.0.1` / `4822` | Where guacd listens |
| `VCENTRAL_SYNC_INTERVAL_SECONDS` | `30` | Starting vCenter sync interval (change later on the Settings page) |
| `VCENTRAL_AGENT_REPORT_INTERVAL_SECONDS` / `_INVENTORY_` | `20` / `3600` | Starting agent intervals (Settings page) |
| `VCENTRAL_METRICS_RAW_RETENTION_DAYS` | `7` | Full-detail metric history |
| `VCENTRAL_METRICS_HOURLY_RETENTION_DAYS` | `180` | Hourly-average metric history |
| `VCENTRAL_HISTORY_RETENTION_DAYS` | `365` | Activity history |
| `VCENTRAL_SESSION_HOURS` | `8` | Sign-in session length |

## Development

```bash
# Backend (Python 3.12)
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt pytest httpx
cd backend && ../.venv/bin/python -m pytest -q        # SQLite by default
TEST_DATABASE_URL=postgresql+psycopg2://user@host/db ../.venv/bin/python -m pytest -q   # against PostgreSQL

# Try it against VMware's vCenter simulator instead of a real vCenter
go install github.com/vmware/govmomi/vcsim@v0.43.0 && vcsim -l 127.0.0.1:8989 &
# then set VCENTRAL_VCENTER_HOST=127.0.0.1, _PORT=8989, _USER=user, _PASSWORD=pass, _VERIFY_SSL=false

uvicorn app.main:app --reload       # API on :8000   (needs VCENTRAL_SECRET_KEY etc. in backend/.env)
python -m app.worker                # sync worker

# Frontend (Node 20)
cd frontend && npm install && npm run dev    # UI on :5173, proxies /api to :8000

# Agent (Go 1.21+; plus Go 1.20 for the legacy Windows builds)
cd agent && go vet ./... && GOOS=windows go vet ./...
./build.sh /tmp/agent-bin 0.2.0 /path/to/go1.20/bin/go   # all platforms + manifest.json
# point the API at the builds: VCENTRAL_AGENT_DIR=/tmp/agent-bin
# try an agent without installing it (http allowed only for local testing):
VCENTRAL_AGENT_ALLOW_HTTP=1 ./vcentral-agent enroll --server http://127.0.0.1:8000 --token vct_... --config ./agent.json
VCENTRAL_AGENT_ALLOW_HTTP=1 ./vcentral-agent run --config ./agent.json
```

The agent's `go.mod` stays at `go 1.20` so the legacy build keeps compiling: avoid newer language features (`min`/`max` built-ins, `slices`, `log/slog`, range-over-int).

Code layout:

```
backend/app/
  vcenter.py   batched vCenter queries (PropertyCollector) and creation-event lookup
  sync.py      compares vCenter with the database, records history, metrics, roll-ups
  worker.py    scheduler: sync, "Sync now" requests, hourly roll-up, nightly pruning
  security.py  passwords, session cookies, roles, login throttling
  agents.py    agent ↔ VM matching, online state, certificate fingerprint
  api/         REST endpoints used by the UI (OpenAPI docs at /api/docs); agent_api.py is the agent protocol
  agent_scripts/  install.sh / install.ps1 served to VMs
agent/         Go agent: collect.go (metrics), inventory*.go, platform_*.go (service install), run.go (report loop)
frontend/src/  React + Ant Design UI
deploy/        nginx config;  scripts/  install, certificate, backup
```

## Roadmap

1. **Visibility** (done): inventory, owners, vCenter metrics, IP map, change history, users and audit
2. **Agent** (done): Windows and Linux agent with in-guest metrics, processes, software, services and ports; enrollment tokens; certificate pinning; VM health and critical VMs
3. **Onboarding** (done): agent install through VMware Tools, onboarding queue, email/Google Chat digest to the admin team
4. **Operations** (done): signed tasks across Linux and Windows VMs with live output, script library, schedules, agent self-update
5. **Owners, self-enrollment & console** (this release): owner suggestions, new/deleted VM list, template self-enrollment, browser SSH/RDP, VMware Remote Console links
6. **Alerts**: Google Chat and email alerts for critical VMs
