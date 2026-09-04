[CmdletBinding()]
param(
    [string]$ModelDir = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

if ([string]::IsNullOrWhiteSpace($ModelDir)) {
    $configured = Get-LocalEnvironmentValue -Name "MATCHING_RESPONSIBILITY_CE_MODEL_HOST_PATH"
    if ([string]::IsNullOrWhiteSpace($configured)) {
        $configured = "../models/responsibility-ce-v1"
    }
    $ModelDir = if ([System.IO.Path]::IsPathRooted($configured)) {
        $configured
    }
    else {
        Join-Path $ComposeApplicationRoot $configured
    }
}
$ModelDir = (Resolve-Path -LiteralPath $ModelDir).Path
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $JobPulseRoot "dist\offline\jobpulse-responsibility-ce.tar"
}
$outputParent = Split-Path -Parent $Output
if (-not (Test-Path -LiteralPath $outputParent)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}

& python (Join-Path $PSScriptRoot "responsibility_ce_package.py") export --model-dir $ModelDir --output $Output
if ($LASTEXITCODE -ne 0) {
    throw "Responsibility CE export failed with exit code $LASTEXITCODE."
}
$sizeMb = [math]::Round((Get-Item -LiteralPath $Output).Length / 1MB)
Write-Host "Offline Responsibility CE package ready: $Output ($sizeMb MB)"
