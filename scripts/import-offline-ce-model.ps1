[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)][string]$PackagePath,
    [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "Offline Responsibility CE package not found: $PackagePath"
}
if ([string]::IsNullOrWhiteSpace($TargetDir)) {
    $TargetDir = Join-Path $JobPulseRoot "models\responsibility-ce-v1"
}
& python (Join-Path $PSScriptRoot "responsibility_ce_package.py") import --package $PackagePath --target $TargetDir
if ($LASTEXITCODE -ne 0) {
    throw "Responsibility CE import failed with exit code $LASTEXITCODE."
}
Write-Host "Responsibility CE model installed. start-jobpulse.cmd /offline can now use the formal semantic verifier."
