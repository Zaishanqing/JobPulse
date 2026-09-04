[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet(
        "main-api",
        "main",
        "frontend",
        "knowledge-graph",
        "discovery",
        "trend",
        "matching",
        "jd-extraction",
        "cv-extraction"
    )]
    [string]$Target,
    [ValidateSet("Jobgraph", "JobPulse")][string]$Layout = "JobPulse",
    [string]$ProjectDir = "",
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1") -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot

$targets = $ComposeRebuildTargets

Assert-DockerComposeVersion
Assert-DockerEngine
Initialize-LocalEnvironment
Initialize-ComposeProfiles
Invoke-RootCompose -ComposeArguments @("config", "--quiet")

$selection = $targets[$Target]
$buildService = [string]$selection.BuildService
$services = [string[]]$selection.Services

Write-Host "Building only $buildService for target '$Target'..."
if ($buildService -eq "main-backend") {
    Invoke-MainBackendBaseImageBuild
}
Invoke-RootImageBuild -Service $buildService

if ($selection.Credential -eq "main") {
    Sync-MainDatabaseCredential
}
elseif ($selection.Credential -eq "matching") {
    Sync-MatchingDatabaseCredential
}
elseif ($selection.Credential -eq "trend") {
    Sync-TrendDatabaseCredential
}

$upArguments = @("up", "--detach", "--wait", "--no-build")
if ($selection.NoDependencies) {
    $upArguments += "--no-deps"
}
$upArguments += $services

Write-Host "Recreating only: $($services -join ', ')"
Invoke-RootCompose -ComposeArguments $upArguments
Write-Host "Fast rebuild completed for '$Target'. Existing data volumes were preserved."
