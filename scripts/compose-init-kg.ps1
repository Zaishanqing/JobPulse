[CmdletBinding()]
param(
    [switch]$Build,
    [ValidateRange(1, 64)][int]$Workers = 1,
    [ValidateRange(1, 3600)][int]$Timeout = 120,
    [ValidateSet("JobPulse")][string]$Layout = "JobPulse",
    [string]$ProjectDir = "",
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1") -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot

Assert-DockerComposeVersion
Assert-DockerEngine
Initialize-LocalEnvironment
Invoke-RootCompose -ComposeArguments @("config", "--quiet")

$image = [string]$ComposeImageNames["knowledge-graph-backend"]
if ($Build -or -not (Test-DockerImageExists -Image $image)) {
    Invoke-RootImageBuild -Service "knowledge-graph-backend"
}

Write-Host "Running the one-time real-data KG graph initialization. Existing published graphs are preserved."
Invoke-RootCompose `
    -AdditionalProfiles @("kg-init") `
    -ComposeArguments @(
        "run", "--rm", "knowledge-graph-real-data-init",
        "python", "scripts/build_kg_graphs.py",
        "--workers", $Workers.ToString(),
        "--timeout", $Timeout.ToString()
    )
Write-Host "KG graph initialization completed. You can now run scripts\compose-up.ps1 -Full."
