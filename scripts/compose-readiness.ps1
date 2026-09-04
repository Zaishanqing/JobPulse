[CmdletBinding()]
param(
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
Initialize-LocalEnvironment
Initialize-ComposeProfiles -Evidence:$Evidence -Full:$Full

$expectedServices = @($ComposeReadinessExpectedServices.Base)
if ($script:ComposeProfileArguments -contains "model-extraction") {
    $expectedServices += @($ComposeReadinessExpectedServices.ModelExtraction)
}
if ($script:ComposeProfileArguments -contains "cv-extraction") {
    $expectedServices += @($ComposeReadinessExpectedServices.CvExtraction)
}
if ($script:ComposeProfileArguments -contains "semantic-demo") {
    $expectedServices += @($ComposeReadinessExpectedServices.Semantic)
}
if ($script:ComposeProfileArguments -contains "evidence-rag") {
    $expectedServices += @($ComposeReadinessExpectedServices.Evidence)
}
if ($script:ComposeProfileArguments -contains "crawler") {
    $expectedServices += @($ComposeReadinessExpectedServices.Crawler)
}

$runningServices = @(
    & docker compose `
        --project-directory $ComposeRepositoryRoot `
        --env-file $ComposeEnvironmentFile `
        --file $ComposeFile `
        ps --status running --services
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect running Compose services."
}

$missingServices = @($expectedServices | Where-Object { $_ -notin $runningServices })
if ($missingServices.Count -gt 0) {
    throw "System is not ready; services not running: $($missingServices -join ', ')"
}

$pythonReadinessServices = @($ComposePythonReadinessServices.Base)
if ($script:ComposeProfileArguments -contains "model-extraction") {
    $pythonReadinessServices += @($ComposePythonReadinessServices.ModelExtraction)
}
if ($script:ComposeProfileArguments -contains "cv-extraction") {
    $pythonReadinessServices += @($ComposePythonReadinessServices.CvExtraction)
}
$publishedKgRequired = $Full -or $RequirePublishedKg

foreach ($service in $pythonReadinessServices) {
    $healthPath = if ($service -eq "knowledge-graph-backend" -and -not $publishedKgRequired) {
        "health"
    }
    else {
        "readiness"
    }
    $pythonProbe = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/$healthPath', timeout=5)"
    Invoke-RootCompose -ComposeArguments @(
        "exec", "-T", $service, "python", "-c", $pythonProbe
    )
    Write-Host "Ready: $service ($healthPath)"
}

$frontendService = [string]$ComposeServiceNames.Frontend
Invoke-RootCompose -ComposeArguments @(
    "exec", "-T", $frontendService, "wget", "-qO-", "http://127.0.0.1/"
)
Write-Host "Ready: $frontendService"
if (-not $publishedKgRequired -and $pythonReadinessServices -contains "knowledge-graph-backend") {
    Write-Host "Published KG graph readiness was not required. Use -RequirePublishedKg (or -Full) after importing and publishing real KG data."
}
Write-Host "System readiness check passed for all services."
