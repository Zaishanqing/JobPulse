[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Offline,
    [switch]$Semantic,
    [switch]$Evidence,
    [switch]$Full,
    [ValidateSet("Jobgraph", "JobPulse")][string]$Layout = "JobPulse",
    [string]$ProjectDir = "",
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1") -Layout $Layout -ProjectDir $ProjectDir -MonorepoRoot $MonorepoRoot

function Write-Phase {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

function Test-EmbeddingModelInComposeCache {
    param(
        [Parameter(Mandatory)][string]$ServiceName,
        [Parameter(Mandatory)][string]$Revision
    )

    # The JobPulse Compose layout stores /models in a named Docker volume,
    # so checking a host .cache directory is not enough. Probe the volume that
    # the service will actually use before attempting any network access.
    $probe = @(
        "run", "--rm", "--no-deps",
        $ServiceName,
        "python", "-c",
        "from pathlib import Path; import sys; root=Path('/models/models--BAAI--bge-m3'); snapshot=root/'snapshots'/'$Revision'; weight=snapshot/'pytorch_model.bin'; incomplete=any(root.rglob('*.incomplete')); sys.exit(0 if weight.is_file() and weight.stat().st_size > 0 and not incomplete else 1)"
    )
    $composeArguments = @(
        "--project-directory", $ComposeRepositoryRoot,
        "--env-file", $ComposeEnvironmentFile,
        "--file", $ComposeFile
    ) + @($script:ComposeProfileArguments) + $probe

    # Compose writes normal container-status messages to stderr. Windows
    # PowerShell 5.1 promotes that output to NativeCommandError when the script
    # uses ErrorActionPreference=Stop, even when the stream is redirected.
    # Relax it only around this probe, preserve the native exit code, then
    # restore the script-wide fail-fast behavior.
    $previousErrorActionPreference = $ErrorActionPreference
    $composeExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        & docker compose @composeArguments 2>&1 | Out-Null
        $composeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return $composeExitCode -eq 0
}

Assert-DockerComposeVersion
Assert-DockerEngine
Initialize-LocalEnvironment
Initialize-ComposeProfiles `
    -Semantic:$Semantic `
    -Evidence:$Evidence `
    -Full:$Full `
    -AllowMissingExternalCredentials:$Offline
Invoke-RootCompose -ComposeArguments @("config", "--quiet")

$buildTargetNames = @($ComposeServiceNames.BaseBuildTargets)
$semanticEnabled = ($Layout -eq "JobPulse") -or $Semantic -or $Evidence -or $Full
if ($semanticEnabled) {
    $buildTargetNames += @($ComposeServiceNames.OptionalBuildTargets.Semantic)
}
if ($script:ComposeProfileArguments -contains "model-extraction") {
    $buildTargetNames += @($ComposeServiceNames.OptionalBuildTargets.ModelExtraction)
}
if ($script:ComposeProfileArguments -contains "cv-extraction") {
    $buildTargetNames += @($ComposeServiceNames.OptionalBuildTargets.CvExtraction)
}
if ($script:ComposeProfileArguments -contains "crawler") {
    $buildTargetNames += @($ComposeServiceNames.OptionalBuildTargets.Crawler)
}
$buildTargets = @(
    $buildTargetNames | ForEach-Object {
        $serviceName = [string]$_
        @{
            Service = $serviceName
            Image = [string]$ComposeImageNames[$serviceName]
        }
    }
)
Write-Phase "[1/4] 镜像检查与构建"
$missingTargets = @(
    $buildTargets | Where-Object { -not (Test-DockerImageExists -Image $_.Image) }
)
if ($Offline -and -not $Build -and $missingTargets.Count -gt 0) {
    $missingImages = @($missingTargets | ForEach-Object { [string]$_.Image })
    throw "Offline startup is missing imported images: $($missingImages -join ', '). Run scripts\import-offline-images.ps1 first."
}
# Online startup always asks Docker to rebuild every selected target. Docker's
# layer cache keeps unchanged starts cheap while ensuring a git pull cannot run
# new Compose commands against stale application images. Offline startup keeps
# the old missing-image-only behavior unless -Build is explicitly requested.
# Assign in branches rather than an if-expression: an if-expression would
# unwrap an empty array to $null, and $null.Count throws under strict mode.
if ($Build -or -not $Offline) {
    $targetsToBuild = $buildTargets
}
else {
    $targetsToBuild = @()
}
if ($targetsToBuild.Count -gt 0) {
    if ($targetsToBuild.Service -contains "main-backend") {
        Invoke-MainBackendBaseImageBuild
    }
    if ($targetsToBuild.Count -eq 1) {
        # A single target builds directly; Bake orchestration would add
        # overhead without parallelism to exploit.
        Invoke-RootImageBuild -Service ([string]$targetsToBuild[0].Service)
    } else {
        # Multiple targets go through the compose file so Bake builds them in
        # parallel and tags them with the compose image names.
        Write-Host "Building $($targetsToBuild.Count) images in parallel via docker compose build..."
        $buildArguments = @("build") + @($targetsToBuild | ForEach-Object { [string]$_.Service })
        Invoke-RootCompose -ComposeArguments $buildArguments
    }
}

if ($Full) {
    Assert-KgGraphReadyForFull
}

if ($semanticEnabled) {
    Write-Phase "[2/4] Embedding 模型缓存校验（正式发现必需）"
    $contractPath = $ComposeSemanticContractPath
    if (-not (Test-Path -LiteralPath $contractPath)) {
        throw "Semantic demo is not available in the selected '$Layout' layout: $contractPath"
    }
    $modelRevision = ""
    foreach ($line in Get-Content -LiteralPath $contractPath) {
        if ($line -match '^\s*EMBEDDING_MODEL_REVISION\s*=\s*(\S+)') {
            $modelRevision = $Matches[1]
        }
    }
    if ($modelRevision -notmatch '^[0-9a-f]{40}$') {
        throw "Contract $contractPath must pin a 40-hex EMBEDDING_MODEL_REVISION."
    }
    $embeddingServiceName = if ($Layout -eq "JobPulse") { "embedding-service" } else { [string]$ComposeServiceNames.OptionalBuildTargets.Semantic[0] }
    if (Test-EmbeddingModelInComposeCache -ServiceName $embeddingServiceName -Revision $modelRevision) {
        Write-Host "Embedding model cache is warm in the Compose volume (revision $modelRevision)."
        # A complete local snapshot must never trigger another Hugging Face
        # request during prefetch or service startup.
        $env:HF_HUB_OFFLINE = "1"
    } elseif ($Offline -or $env:HF_HUB_OFFLINE -eq "1") {
        $modelCacheDir = Join-Path $ComposeApplicationRoot ".cache\embedding-models\models--BAAI--bge-m3"
        $weightFile = Join-Path $modelCacheDir "snapshots\$modelRevision\pytorch_model.bin"
        throw "Embedding model cache is missing from the Compose volume: $weightFile. Import it first or remove HF_HUB_OFFLINE and retry online."
    } else {
        Write-Host "Semantic demo needs the BGE-M3 weights (about 2.3GB); downloading with progress below."
        Write-Host "The configured HF_ENDPOINT mirror is used; override it in infra/.env if needed."
        $prefetchScripts = $ComposeApplicationScriptsRoot
        $prefetchConfig = $ComposeApplicationConfigRoot
        Invoke-RootCompose -ComposeArguments @(
            "run", "--rm", "--no-deps",
            "-v", "${prefetchScripts}:/prefetch:ro",
            "-v", "${prefetchConfig}:/config:ro",
            $embeddingServiceName,
            "python", "/prefetch/prefetch_bge_m3.py", "--cache-dir", "/models"
        )
        # The successful prefetch populated the same Compose volume that the
        # service will use, so subsequent startup is local-only as well.
        $env:HF_HUB_OFFLINE = "1"
        Write-Host "Embedding model cache is warm in the Compose volume (revision $modelRevision)."
    }
} else {
    Write-Phase "[2/4] 跳过 Embedding 模型校验（基础模式）"
}

Write-Phase "[3/4] 启动服务"
Sync-MainDatabaseCredential
Sync-MatchingDatabaseCredential
Sync-TrendDatabaseCredential
Invoke-RootCompose -ComposeArguments @("up", "--detach", "--wait", "--no-build")
Write-Phase "[4/4] 服务就绪"
$frontendPort = Get-LocalEnvironmentValue -Name "MAIN_FRONTEND_PORT"
if ([string]::IsNullOrWhiteSpace($frontendPort)) {
    $frontendPort = "3000"
}
Write-Host "$Layout is ready: http://localhost:$frontendPort"
