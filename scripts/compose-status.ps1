[CmdletBinding()]
param(
    [switch]$SkipReadiness,
    [switch]$Evidence,
    [switch]$Full,
    [switch]$RequirePublishedKg,
    [ValidateSet("Jobgraph", "JobPulse")][string]$Layout = "JobPulse",
    [string]$ProjectDir = "",
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1") -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot

Assert-DockerComposeVersion
Invoke-RootCompose -ComposeArguments @("ps")

if (-not $SkipReadiness) {
    & (Join-Path $PSScriptRoot "compose-readiness.ps1") -Evidence:$Evidence -Full:$Full -RequirePublishedKg:$RequirePublishedKg -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "System readiness check failed with exit code $LASTEXITCODE."
    }
}
