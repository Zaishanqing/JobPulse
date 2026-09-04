[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$PackagePath,
    [switch]$Full
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

Assert-DockerComposeVersion
Assert-DockerEngine

if (-not (Test-Path $PackagePath)) {
    throw "Offline image package not found: $PackagePath"
}

Write-Host "Loading images from $PackagePath ..."
& docker load --input $PackagePath
if ($LASTEXITCODE -ne 0) {
    throw "docker load failed with exit code $LASTEXITCODE."
}

$expectedImages = @(
    "postgres:16-alpine",
    "redis:7-alpine",
    "qdrant/qdrant:v1.12.5",
    "jobpulse-knowledge-graph-backend:candidate",
    "jobpulse-emerging-discovery:candidate",
    "jobpulse-trend-intelligence:candidate",
    "jobpulse-matching-service:candidate",
    "jobpulse-main-backend:candidate",
    "jobpulse-main-frontend:candidate",
    "jobpulse-embedding-service:candidate"
)
if ($Full) {
    $expectedImages += @(
        "mysql:8.0",
        "jobpulse-jd-extraction:candidate",
        "jobpulse-cv-extraction:candidate",
        "jobpulse-crawler:candidate"
    )
}
$missingImages = @()
foreach ($image in $expectedImages) {
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        $missingImages += $image
    }
}
if ($missingImages.Count -gt 0) {
    throw "These images are missing after load: $($missingImages -join ', ')"
}

Write-Host "Offline images loaded. Start the system without rebuilding:"
if ($Full) {
    Write-Host "  start-jobpulse.cmd /offline /full"
}
else {
    Write-Host "  start-jobpulse.cmd /offline"
}
