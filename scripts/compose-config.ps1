[CmdletBinding()]
param(
    [ValidateSet("Jobgraph", "JobPulse")][string]$Layout = "JobPulse",
    [string]$ProjectDir = "",
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1") -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot

Assert-DockerComposeVersion
Invoke-RootCompose -ComposeArguments @("config", "--quiet")
Write-Host "Root Compose configuration is valid."
