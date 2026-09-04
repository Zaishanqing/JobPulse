# Configure the Docker Hub registry mirror for this machine.
#
# Writes registry-mirrors into the user-level ~/.docker/daemon.json (the file
# Docker Desktop reads on Windows). The machine-wide configuration remains
# intact; this is idempotent and backs up any existing file first.
#
# Usage:
#   .\scripts\configure-registry-mirror.ps1                     # default mirror
#   .\scripts\configure-registry-mirror.ps1 -Mirror https://docker.m.daocloud.io
#   .\scripts\configure-registry-mirror.ps1 -Remove             # unset mirror
#
# Requires Docker Desktop restart (Settings -> Docker Engine applies it, or
# fully quit and start Docker Desktop again).

[CmdletBinding()]
param(
    [string]$Mirror = "https://docker.1ms.run",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$DockerConfigDir = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".docker"
$DaemonJson = Join-Path $DockerConfigDir "daemon.json"

if (-not (Test-Path -LiteralPath $DockerConfigDir)) {
    New-Item -ItemType Directory -Path $DockerConfigDir -Force | Out-Null
}

$config = @{}
if (Test-Path -LiteralPath $DaemonJson) {
    try {
        $config = Get-Content -LiteralPath $DaemonJson -Raw | ConvertFrom-Json
    }
    catch {
        throw "~/.docker/daemon.json is not valid JSON; fix it manually before rerunning."
    }
    if (-not $config.PSObject.Properties) {
        $config = @{}
    }
}

if ($Remove) {
    if ($config.PSObject.Properties.Name -contains "registry-mirrors") {
        $config.PSObject.Properties.Remove("registry-mirrors")
        $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $DaemonJson -Encoding UTF8
        Write-Host "Removed registry-mirrors from $DaemonJson"
    }
    else {
        Write-Host "No registry-mirrors configured; nothing to remove."
    }
    exit 0
}

# ConvertFrom-Json may produce an array or object; normalize to a list.
$mirrors = @()
if ($config.PSObject.Properties.Name -contains "registry-mirrors") {
    $mirrors = @($config."registry-mirrors")
}
$normalized = @($Mirror.TrimEnd("/"))
$mirrors = @($mirrors | Where-Object { $normalized -notcontains $_.ToString().TrimEnd("/") })
$mirrors = @($normalized) + $mirrors

$config | Add-Member -NotePropertyName "registry-mirrors" -NotePropertyValue $mirrors -Force

$backup = "$DaemonJson.bak"
if (-not (Test-Path -LiteralPath $backup)) {
    Copy-Item -LiteralPath $DaemonJson -Destination $backup -ErrorAction SilentlyContinue
}

$config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $DaemonJson -Encoding UTF8
Write-Host "registry-mirrors written to $DaemonJson"
Write-Host "Mirrors: $($mirrors -join ', ')"
Write-Host ""
Write-Host "Restart Docker Desktop for it to take effect:"
Write-Host "  Settings -> Docker Engine (the file is shown there), or fully quit and restart Docker Desktop."
