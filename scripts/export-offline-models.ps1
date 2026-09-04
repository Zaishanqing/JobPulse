[CmdletBinding()]
param(
    [string]$Output = "",
    [string]$CacheDir = "",
    [string]$VolumeName = "jobpulse-candidate_embedding_model_cache"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

Assert-DockerEngine

# Model identity comes from the same contract that pins the runtime services,
# so the package can never drift from what the stack expects at startup.
$contractPath = $ComposeSemanticContractPath
$contract = @{}
foreach ($line in Get-Content -LiteralPath $contractPath) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $key, $value = $line -split '=', 2
    $contract[$key.Trim()] = $value.Trim()
}
$repoId = $contract["EMBEDDING_MODEL_ID"]
$revision = $contract["EMBEDDING_MODEL_REVISION"]
if ([string]::IsNullOrWhiteSpace($repoId) -or $revision -notmatch '^[0-9a-f]{40}$') {
    throw "Contract $contractPath must pin EMBEDDING_MODEL_ID and a 40-hex EMBEDDING_MODEL_REVISION."
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $JobPulseRoot ("dist\offline\jobpulse-models-bge-m3-{0}.tar" -f $revision.Substring(0, 8))
}
$outputParent = Split-Path -Parent $Output
if (-not (Test-Path -LiteralPath $outputParent)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}

if ([string]::IsNullOrWhiteSpace($CacheDir)) {
    & docker volume inspect $VolumeName *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Compose model volume not found: $VolumeName. Warm the stack model cache before exporting."
    }
    $cacheMountSource = $VolumeName
    Write-Host "Using Compose model volume: $VolumeName"
}
else {
    $CacheDir = (Resolve-Path -LiteralPath $CacheDir).Path
    $modelCacheDir = Join-Path $CacheDir ("models--" + ($repoId -replace '/', '--'))
    if (-not (Test-Path -LiteralPath (Join-Path $modelCacheDir "snapshots\$revision"))) {
        throw "Model snapshot not found under $modelCacheDir. Warm the cache first."
    }
    $cacheMountSource = $CacheDir
    Write-Host "Using host model cache: $CacheDir"
}

# The container-created cache stores snapshot entries as Linux symlinks that
# Windows cannot follow, so packaging runs inside a Linux container where the
# symlinks resolve. The cache is mounted read-only.
$image = [string]$ComposeImageBuildTargets["embedding-service"].Image
& docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Image $image is missing. Build the stack once before exporting."
}

$scriptsDir = $PSScriptRoot
$outputName = Split-Path -Leaf $Output
Write-Host "Packing model snapshot $repoId @ $revision into $Output ..."
& docker run --rm `
    -v "${cacheMountSource}:/models:ro" `
    -v "${scriptsDir}:/work:ro" `
    -v "$(Split-Path -Parent $Output):/out" `
    $image `
    python /work/export_offline_models.py `
        --repo-id $repoId `
        --revision $revision `
        --dimension $contract["EMBEDDING_DIMENSION"] `
        --cache-dir /models `
        --output "/out/$outputName"
if ($LASTEXITCODE -ne 0) {
    throw "Model export failed with exit code $LASTEXITCODE."
}

$sizeMb = [math]::Round((Get-Item -LiteralPath $Output).Length / 1MB)
Write-Host "Offline model package ready: $Output ($sizeMb MB)"
Write-Host "Copy this file to the offline machine and run: scripts\import-offline-models.ps1 <path>"
