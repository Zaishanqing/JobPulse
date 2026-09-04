[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)][string]$PackageDirectory
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

Assert-DockerEngine

function Test-DockerVolumeExists {
    param(
        [Parameter(Mandatory)][string]$Name
    )

    # A missing volume is the expected state for an exact restore. Windows
    # PowerShell 5.1 otherwise promotes docker's stderr into a terminating
    # NativeCommandError before the exit code can be inspected.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & docker volume inspect $Name *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

$PackageDirectory = (Resolve-Path -LiteralPath $PackageDirectory).Path
$manifestPath = Join-Path $PackageDirectory "manifest.json"
$environmentPath = Join-Path $PackageDirectory "offline-runtime.env"
if (-not (Test-Path -LiteralPath $manifestPath) -or -not (Test-Path -LiteralPath $environmentPath)) {
    throw "Offline data package must contain manifest.json and offline-runtime.env."
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.package_format -ne "jobpulse-compose-volumes.v1" -or $manifest.compose_project -ne "jobpulse-candidate") {
    throw "Unsupported offline data package."
}

Write-Host "Verifying all volume archives before creating Docker volumes..."
$packageEntries = @($manifest.volumes) + @($manifest.bind_directories)
foreach ($entry in $packageEntries) {
    $archive = Join-Path $PackageDirectory ([string]$entry.archive)
    if (-not (Test-Path -LiteralPath $archive)) {
        throw "Data package is missing $($entry.archive)."
    }
    $item = Get-Item -LiteralPath $archive
    if ($item.Length -ne [long]$entry.size_bytes) {
        throw "Size mismatch for $($entry.archive)."
    }
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne ([string]$entry.sha256).ToLowerInvariant()) {
        throw "SHA-256 mismatch for $($entry.archive)."
    }
}

$targetEnvironment = Join-Path $ComposeApplicationRoot ".env"
if (Test-Path -LiteralPath $targetEnvironment) {
    throw "Target environment already exists: $targetEnvironment. Use a fresh extracted source tree for an exact restore."
}
Copy-Item -LiteralPath $environmentPath -Destination $targetEnvironment

foreach ($entry in $manifest.volumes) {
    $logicalName = [string]$entry.logical_name
    $volumeName = [string]$entry.volume_name
    $expectedName = "jobpulse-candidate_$logicalName"
    if ($volumeName -ne $expectedName) {
        throw "Unexpected volume mapping in manifest: $logicalName -> $volumeName"
    }
    if (Test-DockerVolumeExists -Name $volumeName) {
        throw "Target volume already exists: $volumeName. Restore requires a fresh Docker volume set."
    }
    & docker volume create `
        --label "com.docker.compose.project=jobpulse-candidate" `
        --label "com.docker.compose.volume=$logicalName" `
        $volumeName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create target volume: $volumeName"
    }
    $archivePath = Join-Path $PackageDirectory ([string]$entry.archive)
    Write-Host "Restoring $volumeName ..."
    & docker run --rm `
        --volume "${volumeName}:/data" `
        --volume "${archivePath}:/package/volume.tar:ro" `
        postgres:16-alpine `
        tar -xf /package/volume.tar -C /data
    if ($LASTEXITCODE -ne 0) {
        throw "Volume restore failed: $volumeName"
    }
}

foreach ($entry in $manifest.bind_directories) {
    $relativePath = [string]$entry.relative_path
    if ($relativePath -notin @("services/crawler/data", "services/crawler/output", "services/crawler/cookies")) {
        throw "Unexpected bind directory in manifest: $relativePath"
    }
    $target = Join-Path $JobPulseRoot ($relativePath.Replace('/', '\'))
    if (Test-Path -LiteralPath $target) {
        $existing = @(Get-ChildItem -LiteralPath $target -Force)
        if ($existing.Count -gt 0) {
            throw "Bind directory is not empty: $target"
        }
    }
    else {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
    }
    $archivePath = Join-Path $PackageDirectory ([string]$entry.archive)
    Write-Host "Restoring bind directory $relativePath ..."
    & docker run --rm `
        --volume "${target}:/data" `
        --volume "${archivePath}:/package/directory.tar:ro" `
        postgres:16-alpine `
        tar -xf /package/directory.tar -C /data
    if ($LASTEXITCODE -ne 0) {
        throw "Bind directory restore failed: $relativePath"
    }
}

Set-Content -LiteralPath (Join-Path $ComposeApplicationRoot ".offline-data-restored") -Value $manifest.generated_at -Encoding ascii
Write-Host "All JobPulse data volumes were restored. Start with start-jobpulse.cmd /offline /full."
