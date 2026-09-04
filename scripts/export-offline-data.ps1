[CmdletBinding()]
param([string]$OutputDirectory = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

Assert-DockerComposeVersion
Assert-DockerEngine
Initialize-LocalEnvironment

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $JobPulseRoot "dist\offline\data"
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$logicalVolumes = @(
    "main_postgres_data",
    "main_uploads",
    "knowledge_graph_postgres_data",
    "analytics_postgres_data",
    "matching_postgres_data",
    "matching_redis_data",
    "embedding_model_cache",
    "matching_qdrant_data",
    "crawler_mysql_data",
    "cv_extraction_checkpoints"
)
$profiles = @("full", "model-extraction", "cv-extraction", "semantic-demo", "evidence-rag", "crawler", "kg-init")
$profileArguments = @($profiles | ForEach-Object { @("--profile", $_) })
$composePrefix = @(
    "compose",
    "--project-directory", $ComposeRepositoryRoot,
    "--env-file", $ComposeEnvironmentFile,
    "--file", $ComposeFile
) + $profileArguments

$runningServices = @(& docker @composePrefix ps --services --filter status=running)
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the current Compose service state."
}

Write-Host "Stopping JobPulse writers for a cross-volume consistent snapshot..."
& docker @composePrefix stop
if ($LASTEXITCODE -ne 0) {
    throw "Could not stop the JobPulse Compose services."
}

$entries = @()
$bindEntries = @()
try {
    foreach ($logicalName in $logicalVolumes) {
        $volumeName = "jobpulse-candidate_$logicalName"
        & docker volume inspect $volumeName *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Required local data volume is missing: $volumeName"
        }
        $archiveName = "$logicalName.tar"
        $archivePath = Join-Path $OutputDirectory $archiveName
        Write-Host "Exporting $volumeName ..."
        & docker run --rm `
            --volume "${volumeName}:/data:ro" `
            --volume "${OutputDirectory}:/out" `
            postgres:16-alpine `
            tar -cf "/out/$archiveName" -C /data .
        if ($LASTEXITCODE -ne 0) {
            throw "Volume export failed: $volumeName"
        }
        $item = Get-Item -LiteralPath $archivePath
        $entries += @{
            logical_name = $logicalName
            volume_name = $volumeName
            archive = $archiveName
            size_bytes = $item.Length
            sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $bindDirectories = @(
        @{ name = "crawler_data"; relative_path = "services/crawler/data" },
        @{ name = "crawler_output"; relative_path = "services/crawler/output" },
        @{ name = "crawler_cookies"; relative_path = "services/crawler/cookies" }
    )
    $emptyDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("jobpulse-empty-data-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $emptyDirectory -Force | Out-Null
    try {
        foreach ($binding in $bindDirectories) {
            $sourcePath = Join-Path $JobPulseRoot ([string]$binding.relative_path)
            if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
                $sourcePath = $emptyDirectory
            }
            $archiveName = "$($binding.name).tar"
            $archivePath = Join-Path $OutputDirectory $archiveName
            Write-Host "Exporting bind directory $($binding.relative_path) ..."
            & docker run --rm `
                --volume "${sourcePath}:/data:ro" `
                --volume "${OutputDirectory}:/out" `
                postgres:16-alpine `
                tar -cf "/out/$archiveName" -C /data .
            if ($LASTEXITCODE -ne 0) {
                throw "Bind directory export failed: $($binding.relative_path)"
            }
            $item = Get-Item -LiteralPath $archivePath
            $bindEntries += @{
                name = [string]$binding.name
                relative_path = [string]$binding.relative_path
                archive = $archiveName
                size_bytes = $item.Length
                sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    }
    finally {
        Remove-Item -LiteralPath $emptyDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
finally {
    if ($runningServices.Count -gt 0) {
        Write-Host "Restoring the services that were running before the snapshot..."
        & docker @composePrefix start @runningServices
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "The data snapshot completed, but one or more previously running services did not restart."
        }
    }
}

$manifest = @{
    package_format = "jobpulse-compose-volumes.v1"
    compose_project = "jobpulse-candidate"
    generated_at = [DateTimeOffset]::UtcNow.ToString("o")
    volumes = $entries
    bind_directories = $bindEntries
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputDirectory "manifest.json") -Encoding UTF8

# Preserve the complete runtime environment, including the explicitly supplied
# temporary DeepSeek API key, so the restored installation behaves like the
# packaging machine. The generated data package is therefore sensitive.
$offlineEnvironment = @(
    Get-Content -LiteralPath $ComposeEnvironmentFile | ForEach-Object {
        if ($_ -match '^\s*LOAD_PACKAGED_JUDGE_DATA\s*=') { "LOAD_PACKAGED_JUDGE_DATA=false" }
        elseif ($_ -match '^\s*MATCHING_RESPONSIBILITY_CE_MODEL_HOST_PATH\s*=') { "MATCHING_RESPONSIBILITY_CE_MODEL_HOST_PATH=../models/responsibility-ce-v1" }
        else { $_ }
    }
)
if (-not ($offlineEnvironment -match '^\s*LOAD_PACKAGED_JUDGE_DATA\s*=')) {
    $offlineEnvironment += "LOAD_PACKAGED_JUDGE_DATA=false"
}
$offlineEnvironment | Set-Content -LiteralPath (Join-Path $OutputDirectory "offline-runtime.env") -Encoding UTF8

$totalBytes = (@($entries) + @($bindEntries) | ForEach-Object { [long]$_.size_bytes } | Measure-Object -Sum).Sum
Write-Host "Offline data snapshot ready: $OutputDirectory ($([math]::Round($totalBytes / 1GB, 2)) GB)"
