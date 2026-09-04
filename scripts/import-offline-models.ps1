[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$PackagePath,
    [string]$CacheDir = "",
    [string]$VolumeName = "jobpulse-candidate_embedding_model_cache"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

Assert-DockerEngine

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "Offline model package not found: $PackagePath"
}
$contractRevision = ""
foreach ($line in Get-Content -LiteralPath $ComposeSemanticContractPath) {
    if ($line -match '^\s*EMBEDDING_MODEL_REVISION\s*=\s*(\S+)') {
        $contractRevision = $Matches[1]
    }
}
if ($contractRevision -notmatch '^[0-9a-f]{40}$') {
    throw "Semantic contract must pin a 40-hex EMBEDDING_MODEL_REVISION."
}

$image = [string]$ComposeImageBuildTargets["embedding-service"].Image
& docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Image $image is missing. Import the offline images before importing the model."
}

if ([string]::IsNullOrWhiteSpace($CacheDir)) {
    & docker volume create $VolumeName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create Compose model volume $VolumeName."
    }
    $cacheMountSource = $VolumeName
    Write-Host "Installing into Compose model volume: $VolumeName"
}
else {
    New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    $CacheDir = (Resolve-Path -LiteralPath $CacheDir).Path
    $cacheMountSource = $CacheDir
    Write-Host "Installing into host model cache: $CacheDir"
}

$resolvedPackage = (Resolve-Path -LiteralPath $PackagePath).Path
Write-Host "Verifying and installing $resolvedPackage ..."
& docker run --rm `
    -v "${resolvedPackage}:/package/model.tar:ro" `
    -v "${cacheMountSource}:/models" `
    -v "${PSScriptRoot}:/work:ro" `
    $image `
    python /work/import_offline_models.py `
        --package /package/model.tar `
        --cache-dir /models `
        --expected-revision $contractRevision
if ($LASTEXITCODE -ne 0) {
    throw "Model import failed with exit code $LASTEXITCODE."
}

Write-Host "Start the system offline with: start-jobpulse.cmd /offline"
