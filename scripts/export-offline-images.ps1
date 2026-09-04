[CmdletBinding()]
param(
    [string]$Output = "",
    [switch]$IncludeModelExtraction,
    [switch]$Full
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

Assert-DockerComposeVersion
Assert-DockerEngine

if ([string]::IsNullOrWhiteSpace($Output)) {
    $packageName = if ($Full) { "jobpulse-images-full.tar" } else { "jobpulse-images.tar" }
    $Output = Join-Path $JobPulseRoot "dist\offline\$packageName"
}
$outputParent = Split-Path -Parent $Output
if (-not (Test-Path $outputParent)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}

# Build every image the default stack needs, straight from the checked-out
# source, so the package never ships stale layers.
$buildServices = @(
    "knowledge-graph-backend",
    "emerging-discovery",
    "trend-intelligence",
    "matching-api",
    "main-backend",
    "main-frontend",
    "embedding-service"
)
if ($IncludeModelExtraction -or $Full) {
    $buildServices += "jd-extraction"
}
if ($Full) {
    $buildServices += @("cv-extraction", "crawler-api")
}
# The main backend FROMs a local base image (Tesseract OCR layer); build it
# first so the main-backend build below never tries to pull it from a registry.
Invoke-MainBackendBaseImageBuild
foreach ($service in $buildServices) {
    Invoke-RootImageBuild -Service $service
}

# Pulled base images are required at runtime too; offline machines cannot
# fetch them, so they ship inside the same tarball.
$runtimeImages = @("postgres:16-alpine", "redis:7-alpine", "qdrant/qdrant:v1.12.5")
if ($Full) {
    $runtimeImages += "mysql:8.0"
}
foreach ($image in $runtimeImages) {
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Pulling base image $image..."
        & docker pull $image
        if ($LASTEXITCODE -ne 0) {
            throw "docker pull $image failed with exit code $LASTEXITCODE."
        }
    }
}

$packageImages = @(@($runtimeImages) + @($buildServices | ForEach-Object {
    [string]$ComposeImageBuildTargets[$_].Image
}) | Select-Object -Unique)

Write-Host "Saving $($packageImages.Count) images to $Output ..."
& docker save --output $Output @packageImages
if ($LASTEXITCODE -ne 0) {
    throw "docker save failed with exit code $LASTEXITCODE."
}

$sizeMb = [math]::Round((Get-Item $Output).Length / 1MB)
Write-Host "Offline image package ready: $Output ($sizeMb MB)"
Write-Host "Copy this file to the offline machine and run: scripts\import-offline-images.ps1 <path>"
if ($Full) {
    Write-Host "This is the full image set. Import it with -Full before using start-jobpulse.cmd /offline /full."
}
