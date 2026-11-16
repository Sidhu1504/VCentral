"""Ready-made scripts an admin can add to the library in one click (Script library → Add starter scripts).

All only read information, except "Restart a service". Linux versions run in bash as root; Windows versions run
in Windows PowerShell as SYSTEM and avoid commands newer than PowerShell 3 where practical.
"""

STARTER_SCRIPTS = [
    {
        "name": "Disk usage",
        "description": "Starter script. Size, used space and % full of every drive / mount point (same figures as df and Explorer).",
        "timeout_seconds": 60,
        "linux_script": """df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null || df -h
echo
echo "Inodes (filesystems over 50% of inodes used):"
df -i -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null | awk 'NR==1 || $5+0 >= 50'
exit 0
""",
        "windows_script": """Get-WmiObject Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
    $used = $_.Size - $_.FreeSpace
    $pct = if ($_.Size) { $used * 100 / $_.Size } else { 0 }
    "{0}  {1,9:N1} GB used of {2,9:N1} GB  ({3:N0}% full)  {4}" -f $_.DeviceID, ($used / 1GB), ($_.Size / 1GB), $pct, $_.VolumeName
}
exit 0
""",
    },
    {
        "name": "Top processes",
        "description": "Starter script. The 10 processes using the most CPU and the 10 using the most memory.",
        "timeout_seconds": 60,
        "linux_script": """echo "By CPU:"
ps -eo pid,user,%cpu,%mem,etime,comm --sort=-%cpu | head -n 11
echo
echo "By memory:"
ps -eo pid,user,%cpu,%mem,rss,comm --sort=-rss | head -n 11
exit 0
""",
        "windows_script": """$props = 'Id', 'ProcessName', @{n='CPU(s)'; e={[math]::Round($_.CPU, 1)}}, @{n='Memory(MB)'; e={[math]::Round($_.WorkingSet64 / 1MB)}}
"By CPU time since start:"
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 $props | Format-Table -AutoSize | Out-String -Width 200
"By memory:"
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 $props | Format-Table -AutoSize | Out-String -Width 200
exit 0
""",
    },
    {
        "name": "Pending reboot and updates",
        "description": "Starter script. Whether the VM is waiting for a reboot, how many updates are available (Linux) and the last updates installed (Windows).",
        "timeout_seconds": 300,
        "linux_script": """if [ -f /var/run/reboot-required ]; then
    echo "Reboot required: YES"
    [ -f /var/run/reboot-required.pkgs ] && { echo "Because of:"; sed 's/^/  /' /var/run/reboot-required.pkgs; }
elif command -v needs-restarting >/dev/null 2>&1; then
    if needs-restarting -r >/dev/null 2>&1; then echo "Reboot required: no"; else echo "Reboot required: YES"; fi
else
    echo "Reboot required: no"
fi
echo "Up since: $(uptime -s 2>/dev/null)"
echo
if command -v apt-get >/dev/null 2>&1; then
    list=$(apt-get -s upgrade 2>/dev/null | grep '^Inst ')
    total=$(printf '%s' "$list" | grep -c '^Inst ')
    security=$(printf '%s' "$list" | grep -ci 'security')
    echo "Updates available: $total (security: $security), based on the last 'apt update'"
elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    pm=$(command -v dnf || command -v yum)
    total=$($pm -q check-update 2>/dev/null | grep -c '^[[:alnum:]]')
    echo "Updates available: $total"
else
    echo "Updates: unknown package manager"
fi
exit 0
""",
        "windows_script": """$reasons = @()
if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending') { $reasons += 'component servicing' }
if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired') { $reasons += 'Windows Update' }
$renames = (Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager' -ErrorAction SilentlyContinue).PendingFileRenameOperations
if ($renames) { $reasons += 'pending file renames' }
if ($reasons.Count -gt 0) { "Reboot required: YES (" + ($reasons -join ', ') + ")" } else { "Reboot required: no" }
$os = Get-WmiObject Win32_OperatingSystem
"Up since: " + $os.ConvertToDateTime($os.LastBootUpTime)
""
"Last 5 updates installed:"
Get-HotFix -ErrorAction SilentlyContinue | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending |
    Select-Object -First 5 HotFixID, Description, InstalledOn | Format-Table -AutoSize | Out-String -Width 200
exit 0
""",
    },
    {
        "name": "Who is logged in",
        "description": "Starter script. Users logged in now, and recent logins (Linux).",
        "timeout_seconds": 60,
        "linux_script": """echo "Logged in now:"
who
echo
echo "Recent logins:"
last -n 15 -a 2>/dev/null | head -n 15 || echo "(login history not available)"
exit 0
""",
        "windows_script": """"Logged in now:"
$sessions = quser 2>$null
if ($sessions) { $sessions | Out-String -Width 200 } else { "Nobody is logged in." }
exit 0
""",
    },
    {
        "name": "Listening ports",
        "description": "Starter script. Every port the VM accepts connections on, with the program behind it.",
        "timeout_seconds": 60,
        "linux_script": """if command -v ss >/dev/null 2>&1; then ss -tulpn; else netstat -tulpn; fi
exit 0
""",
        "windows_script": """$names = @{}
Get-Process | ForEach-Object { $names[$_.Id] = $_.ProcessName }
"{0,-6} {1,-30} {2,-7} {3}" -f 'Proto', 'Local address', 'PID', 'Program'
netstat -ano | Select-String 'LISTENING' | ForEach-Object {
    $p = @($_.Line -split '\\s+' | Where-Object { $_ })
    "{0,-6} {1,-30} {2,-7} {3}" -f $p[0], $p[1], $p[4], $names[[int]$p[4]]
}
exit 0
""",
    },
    {
        "name": "Time sync status",
        "description": "Starter script. The VM's clock and whether it is synchronised (wrong time breaks logins, certificates and logs).",
        "timeout_seconds": 60,
        "linux_script": """date
echo
timedatectl 2>/dev/null
echo
chronyc tracking 2>/dev/null || ntpq -p 2>/dev/null || echo "(chrony / ntpd not installed)"
exit 0
""",
        "windows_script": """Get-Date
""
w32tm /query /status
exit 0
""",
    },
    {
        "name": "Failed services",
        "description": "Starter script. Linux services that failed; Windows services set to start automatically that are not running (some, like Google Update or Software Protection, stop by design).",
        "timeout_seconds": 60,
        "linux_script": """if command -v systemctl >/dev/null 2>&1; then
    systemctl --failed --no-pager
else
    service --status-all 2>/dev/null | grep -- ' - '
fi
exit 0
""",
        "windows_script": """$s = Get-WmiObject Win32_Service -Filter "StartMode='Auto' AND State<>'Running'"
if ($s) { $s | Select-Object Name, DisplayName, State | Format-Table -AutoSize | Out-String -Width 200 } else { "All automatic services are running." }
exit 0
""",
    },
    {
        "name": "Service status",
        "description": "Starter script. Status of one service. Asks for SERVICE when you run it (e.g. nginx, sshd, W32Time, Spooler).",
        "timeout_seconds": 60,
        "linux_script": """systemctl status --no-pager -l -- {{SERVICE}}
""",
        "windows_script": """Get-Service -Name {{SERVICE}} -ErrorAction Stop | Select-Object Name, DisplayName, Status | Format-List | Out-String
""",
    },
    {
        "name": "Restart a service",
        "description": "Starter script. CHANGES THE VM: restarts one service, then shows whether it is running. Asks for SERVICE when you run it.",
        "timeout_seconds": 180,
        "linux_script": """systemctl restart -- {{SERVICE}}
sleep 2
systemctl is-active -- {{SERVICE}}
""",
        "windows_script": """Restart-Service -Name {{SERVICE}} -Force -ErrorAction Stop
Start-Sleep -Seconds 2
Get-Service -Name {{SERVICE}} | Select-Object Name, Status | Format-Table -AutoSize | Out-String
""",
    },
    {
        "name": "Last lines of a log file",
        "description": "Starter script. The last 100 lines of a log file. Asks for LOGFILE when you run it (e.g. /var/log/syslog or C:\\\\inetpub\\\\logs\\\\app.log).",
        "timeout_seconds": 60,
        "linux_script": """tail -n 100 -- {{LOGFILE}}
""",
        "windows_script": """Get-Content -LiteralPath {{LOGFILE}} -Tail 100 -ErrorAction Stop
""",
    },
]
