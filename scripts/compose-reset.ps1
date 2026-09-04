[CmdletBinding()]
param(
    [ValidateSet("Jobgraph", "JobPulse")][string]$Layout = "JobPulse",
    [string]$ProjectDir = "",
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1") -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot

Assert-DockerComposeVersion
Assert-DockerEngine
Initialize-LocalEnvironment
Initialize-ComposeProfiles
Invoke-RootCompose -ComposeArguments @("config", "--quiet")
Write-Host "Removing $Layout containers and named volumes from every Compose profile."
Invoke-RootCompose `
    -AdditionalProfiles @("*") `
    -ComposeArguments @("down", "--volumes", "--remove-orphans")

# `docker compose down --volumes` can leave named volumes that belong only to
# profiles which were not running. Resolve the exact Compose project name, then
# remove only volumes carrying that project's Compose label.
$configJson = @(
    & docker compose `
        --project-directory $ComposeRepositoryRoot `
        --env-file $ComposeEnvironmentFile `
        --file $ComposeFile `
        config --format json
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve the Compose project before removing residual volumes."
}
$composeConfig = ($configJson -join [Environment]::NewLine) | ConvertFrom-Json
$composeProjectName = [string]$composeConfig.name
if ([string]::IsNullOrWhiteSpace($composeProjectName)) {
    throw "Compose config did not provide a project name; residual volumes were not removed."
}

$remainingVolumes = @(
    & docker volume ls `
        --filter "label=com.docker.compose.project=$composeProjectName" `
        --format "{{.Name}}"
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect residual volumes for Compose project '$composeProjectName'."
}
if ($remainingVolumes.Count -gt 0) {
    Write-Host "Removing residual profile volumes: $($remainingVolumes -join ', ')"
    & docker volume rm @remainingVolumes
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove every residual volume for Compose project '$composeProjectName'."
    }
}

& (Join-Path $PSScriptRoot "compose-up.ps1") -Build -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "$Layout failed to start after the data reset."
}
