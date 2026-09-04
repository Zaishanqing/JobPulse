[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) "infra\.offline-data-restored")) {
    Write-Host "Restored data snapshot detected; preserving it without judge-data import or KG rebuild."
    $env:LOAD_PACKAGED_JUDGE_DATA = "false"
    & (Join-Path $PSScriptRoot "compose-up.ps1") -Layout JobPulse -Offline -Full
    if ($LASTEXITCODE -ne 0) {
        throw "Offline restored-data startup failed with exit code $LASTEXITCODE."
    }
    exit 0
}

Write-Host "[1/3] Starting the offline base stack and importing packaged judge data..."
& (Join-Path $PSScriptRoot "compose-up.ps1") -Layout JobPulse -Offline
if ($LASTEXITCODE -ne 0) {
    throw "Offline base startup failed with exit code $LASTEXITCODE."
}

Write-Host "[2/3] Building required Knowledge Graph profiles from the imported data..."
& (Join-Path $PSScriptRoot "compose-init-kg.ps1") -Layout JobPulse
if ($LASTEXITCODE -ne 0) {
    throw "Offline KG initialization failed with exit code $LASTEXITCODE."
}

Write-Host "[3/3] Starting every full-profile service without building or pulling..."
& (Join-Path $PSScriptRoot "compose-up.ps1") -Layout JobPulse -Offline -Full
if ($LASTEXITCODE -ne 0) {
    throw "Offline full startup failed with exit code $LASTEXITCODE."
}
