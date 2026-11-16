# vCentral agent installer for Windows (PowerShell 2.0 or later, run as Administrator).
# Loaded by the one-line command on the vCentral Agents page, which also sets up certificate pinning.
# Windows Server 2016 / Windows 10 and later get the standard build; older versions get the legacy build.
# Update-VCentralAgent replaces an installed agent (any version) with the one on vCentral, keeping its identity.

function Get-VCentralAgentFile {
    param([Parameter(Mandatory = $true)][string]$Server)
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Please run PowerShell as Administrator.'
    }
    $cpu = $env:PROCESSOR_ARCHITEW6432
    if (-not $cpu) { $cpu = $env:PROCESSOR_ARCHITECTURE }
    switch ($cpu) {
        'AMD64' { $arch = 'amd64' }
        'ARM64' { $arch = 'amd64' }   # runs under x64 emulation
        'x86'   { $arch = '386' }
        default { throw "Unsupported CPU architecture: $cpu" }
    }
    if ([Environment]::OSVersion.Version.Major -lt 10) { $platform = "windows-legacy-$arch" } else { $platform = "windows-$arch" }
    $dir = Join-Path $env:ProgramFiles 'vCentral'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $exe = Join-Path $dir 'vcentral-agent-setup.exe'
    $url = $Server.TrimEnd('/') + "/api/agent/download/$platform"
    Write-Host "Downloading vCentral agent ($platform)..."
    (New-Object Net.WebClient).DownloadFile($url, $exe)
    return $exe
}

function Update-VCentralAgent {
    param([Parameter(Mandatory = $true)][string]$Server)
    $ErrorActionPreference = 'Stop'
    $exe = Get-VCentralAgentFile -Server $Server
    & $exe upgrade
    $code = $LASTEXITCODE
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    if ($code -ne 0) { throw "vCentral agent update failed (exit code $code)." }
}

function Install-VCentralAgent {
    param(
        [Parameter(Mandatory = $true)][string]$Server,
        [Parameter(Mandatory = $true)][string]$Token,
        [string]$Fingerprint = '',
        [switch]$Template
    )
    $ErrorActionPreference = 'Stop'

    $exe = Get-VCentralAgentFile -Server $Server

    $cliArgs = @('install', '--server', $Server, '--token', $Token)
    if ($Fingerprint) { $cliArgs += @('--fingerprint', $Fingerprint) }
    if ($Template) { $cliArgs += '--template' }  # VM template: deployed VMs enroll themselves
    & $exe @cliArgs
    $code = $LASTEXITCODE
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    if ($code -ne 0) { throw "vCentral agent installation failed (exit code $code)." }
}
