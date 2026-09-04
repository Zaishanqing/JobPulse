param(
    # The layout is explicit so callers do not depend on the number or names
    # of child directories below JobPulse/. JobPulse is the default for this
    # target tree; Jobgraph remains available only through explicit -Layout
    # selection for rollback/reference checks.
    [ValidateSet("Jobgraph", "JobPulse")]
    [string]$Layout = "JobPulse",
    # Optional explicit project directory. For Jobgraph this is the existing
    # application root; for JobPulse it is the candidate infrastructure root.
    [string]$ProjectDir = "",
    # Optional monorepo root for callers launched outside the repository CWD.
    [string]$MonorepoRoot = ""
)

Set-StrictMode -Version Latest

$ComposeMinimumVersion = [version]"2.20.3"

# Docker Desktop may be started after the terminal was opened, so its CLI
# directory is not always present in the inherited PATH on Windows.
$dockerCliCandidates = @(
    (Get-Command docker.exe -ErrorAction SilentlyContinue).Source,
    (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin"),
    (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin")
)
foreach ($dockerCliCandidate in $dockerCliCandidates) {
    if ([string]::IsNullOrWhiteSpace($dockerCliCandidate)) {
        continue
    }
    $dockerCliDirectory = if ((Test-Path -LiteralPath $dockerCliCandidate -PathType Leaf)) {
        Split-Path -Parent $dockerCliCandidate
    }
    else {
        $dockerCliCandidate
    }
    $dockerCliPath = Join-Path $dockerCliDirectory "docker.exe"
    if (Test-Path -LiteralPath $dockerCliPath -PathType Leaf) {
        if (-not (($env:Path -split [IO.Path]::PathSeparator) -contains $dockerCliDirectory)) {
            $env:Path = "$dockerCliDirectory$([IO.Path]::PathSeparator)$env:Path"
        }
        break
    }
}

function Resolve-ComposeMonorepoRoot {
    param(
        [Parameter(Mandatory)][string]$StartPath
    )

    $current = (Resolve-Path -LiteralPath $StartPath).Path
    while ($true) {
        if (Test-Path -LiteralPath (Join-Path $current ".github")) {
            return $current
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "Could not locate monorepo root (.github) above $StartPath."
        }
        $current = $parent
    }
}

if ([string]::IsNullOrWhiteSpace($MonorepoRoot)) {
    if ($Layout -eq "JobPulse") {
        # JobPulse is distributed as a self-contained source ZIP. Resolve that
        # documented layout directly instead of requiring the parent Git
        # checkout (and its .github directory) to exist on the target machine.
        $JobPulseRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
        $standaloneComposeFile = Join-Path $JobPulseRoot "infra\compose\docker-compose.candidate.yml"
        if (-not (Test-Path -LiteralPath $standaloneComposeFile -PathType Leaf)) {
            throw "Invalid JobPulse source package: missing $standaloneComposeFile"
        }
        $MonorepoRoot = Split-Path -Parent $JobPulseRoot
    }
    else {
        $MonorepoRoot = Resolve-ComposeMonorepoRoot -StartPath $PSScriptRoot
        $JobPulseRoot = Join-Path $MonorepoRoot "JobPulse"
    }
}
else {
    $MonorepoRoot = (Resolve-Path -LiteralPath $MonorepoRoot).Path
    $JobPulseRoot = Join-Path $MonorepoRoot "JobPulse"
}

$JobgraphRoot = Join-Path $MonorepoRoot "Jobgraph"
$selectedLayoutRoot = if ($Layout -eq "Jobgraph") { $JobgraphRoot } else { $JobPulseRoot }
if (-not (Test-Path -LiteralPath $selectedLayoutRoot)) {
    throw "$Layout root not found: $selectedLayoutRoot"
}

# This is the single source of truth for every launcher-facing layout value.
# Do not infer any value by scanning child directories: both trees contain
# multiple independent applications and service-local Compose files.
$jobgraphProjectDirectory = if ($ProjectDir) {
    $explicitProjectDir = if ([System.IO.Path]::IsPathRooted($ProjectDir)) {
        $ProjectDir
    }
    else {
        Join-Path $MonorepoRoot $ProjectDir
    }
    (Resolve-Path -LiteralPath $explicitProjectDir).Path
}
else {
    Join-Path $JobgraphRoot "框架实现"
}

$jobpulseProjectDirectory = if ($ProjectDir) {
    $explicitProjectDir = if ([System.IO.Path]::IsPathRooted($ProjectDir)) {
        $ProjectDir
    }
    else {
        Join-Path $MonorepoRoot $ProjectDir
    }
    (Resolve-Path -LiteralPath $explicitProjectDir).Path
}
else {
    Join-Path $JobPulseRoot "infra"
}

$jobgraphBuildContexts = @{
    "knowledge-graph-backend" = Join-Path $JobgraphRoot "框架实现"
    "emerging-discovery" = Join-Path $JobgraphRoot "框架实现"
    "trend-intelligence" = Join-Path $JobgraphRoot "框架实现/services/trend-intelligence"
    "matching-api" = Join-Path $JobgraphRoot "框架实现"
    "main-backend" = Join-Path $JobgraphRoot "框架实现"
    "main-frontend" = Join-Path $JobgraphRoot "框架实现/frontend"
    "embedding-service" = Join-Path $JobgraphRoot "框架实现/services/embedding-service"
    "jd-extraction" = $JobgraphRoot
    "cv-extraction" = $JobgraphRoot
}
$jobgraphDockerfiles = @{
    "knowledge-graph-backend" = Join-Path $JobgraphRoot "框架实现/services/knowledge-graph/Dockerfile"
    "emerging-discovery" = Join-Path $JobgraphRoot "框架实现/services/emerging-discovery/Dockerfile"
    "trend-intelligence" = Join-Path $JobgraphRoot "框架实现/services/trend-intelligence/Dockerfile"
    "matching-api" = Join-Path $JobgraphRoot "框架实现/services/matching-service/Dockerfile"
    "main-backend" = Join-Path $JobgraphRoot "框架实现/Dockerfile"
    "main-frontend" = Join-Path $JobgraphRoot "框架实现/frontend/Dockerfile"
    "embedding-service" = Join-Path $JobgraphRoot "框架实现/services/embedding-service/Dockerfile"
    "jd-extraction" = Join-Path $JobgraphRoot "Extraction/jdextraction/Dockerfile"
    "cv-extraction" = Join-Path $JobgraphRoot "Extraction/cvextraction/Dockerfile"
}
$jobgraphImageNames = @{
    "knowledge-graph-backend" = "jobgraph-knowledge-graph-backend:local"
    "emerging-discovery" = "jobgraph-emerging-discovery:local"
    "trend-intelligence" = "jobgraph-trend-intelligence:local"
    "matching-api" = "jobgraph-matching-service:local"
    "main-backend" = "jobgraph-main-backend:local"
    "main-frontend" = "jobgraph-main-frontend:local"
    "embedding-service" = "jobgraph-integrated-embedding-service"
    "jd-extraction" = "jobgraph-jd-extraction:local"
    "cv-extraction" = "jobgraph-cv-extraction:local"
    "extraction-worker" = "jobgraph-main-backend:local"
    "validation-worker" = "jobgraph-main-backend:local"
    "kg-outbox-worker" = "jobgraph-main-backend:local"
}
$jobpulseBuildContexts = @{
    "knowledge-graph-backend" = $JobPulseRoot
    "emerging-discovery" = $JobPulseRoot
    "trend-intelligence" = $JobPulseRoot
    "matching-api" = $JobPulseRoot
    "main-backend" = $JobPulseRoot
    "main-frontend" = Join-Path $JobPulseRoot "apps/web"
    "embedding-service" = $JobPulseRoot
    "jd-extraction" = $JobPulseRoot
    "cv-extraction" = $JobPulseRoot
    "crawler-api" = $JobPulseRoot
}
$jobpulseDockerfiles = @{
    "knowledge-graph-backend" = Join-Path $JobPulseRoot "services/knowledge-graph/Dockerfile"
    "emerging-discovery" = Join-Path $JobPulseRoot "services/emerging-discovery/Dockerfile"
    "trend-intelligence" = Join-Path $JobPulseRoot "services/trend-intelligence/Dockerfile"
    "matching-api" = Join-Path $JobPulseRoot "services/matching-service/Dockerfile"
    "main-backend" = Join-Path $JobPulseRoot "apps/api/Dockerfile"
    "main-frontend" = Join-Path $JobPulseRoot "apps/web/Dockerfile"
    "embedding-service" = Join-Path $JobPulseRoot "services/embedding-service/Dockerfile"
    "jd-extraction" = Join-Path $JobPulseRoot "services/jd-extraction/Dockerfile"
    "cv-extraction" = Join-Path $JobPulseRoot "services/cv-extraction/Dockerfile"
    "crawler-api" = Join-Path $JobPulseRoot "services/crawler/deploy/Dockerfile.api"
}
$jobpulseImageNames = @{
    "knowledge-graph-backend" = "jobpulse-knowledge-graph-backend:candidate"
    "emerging-discovery" = "jobpulse-emerging-discovery:candidate"
    "trend-intelligence" = "jobpulse-trend-intelligence:candidate"
    "matching-api" = "jobpulse-matching-service:candidate"
    "main-backend" = "jobpulse-main-backend:candidate"
    "main-frontend" = "jobpulse-main-frontend:candidate"
    "embedding-service" = "jobpulse-embedding-service:candidate"
    "jd-extraction" = "jobpulse-jd-extraction:candidate"
    "cv-extraction" = "jobpulse-cv-extraction:candidate"
    "crawler-api" = "jobpulse-crawler:candidate"
    "crawler-scheduler" = "jobpulse-crawler:candidate"
    "knowledge-graph-migrate" = "jobpulse-knowledge-graph-backend:candidate"
    "knowledge-graph-bootstrap" = "jobpulse-knowledge-graph-backend:candidate"
    "knowledge-graph-worker" = "jobpulse-knowledge-graph-backend:candidate"
    "matching-migrate" = "jobpulse-matching-service:candidate"
    "matching-worker" = "jobpulse-matching-service:candidate"
    "matching-dispatcher" = "jobpulse-matching-service:candidate"
    "emerging-discovery-migrate" = "jobpulse-emerging-discovery:candidate"
    "trend-intelligence-worker" = "jobpulse-trend-intelligence:candidate"
    "extraction-worker" = "jobpulse-main-backend:candidate"
    "validation-worker" = "jobpulse-main-backend:candidate"
    "kg-outbox-worker" = "jobpulse-main-backend:candidate"
    "cv-extraction-worker" = "jobpulse-main-backend:candidate"
    "matching-api-semantic-demo" = "jobpulse-matching-service:candidate"
    "matching-vector-worker-semantic-demo" = "jobpulse-matching-service:candidate"
}

$ComposeLayoutMap = @{
    "Jobgraph" = @{
        MonorepoRoot = $MonorepoRoot
        ProjectDirectory = $jobgraphProjectDirectory
        ComposeProjectDirectory = $JobgraphRoot
        ComposeFile = Join-Path $JobgraphRoot "compose.yaml"
        EnvironmentRoot = Join-Path $JobgraphRoot "框架实现"
        BuildContexts = $jobgraphBuildContexts
        Dockerfiles = $jobgraphDockerfiles
        ImageNames = $jobgraphImageNames
        ServiceNames = @{
            All = @(
                "knowledge-graph-postgres", "knowledge-graph-migrate", "knowledge-graph-bootstrap", "knowledge-graph-backend",
                "analytics-postgres", "emerging-discovery-migrate", "emerging-discovery", "main-postgres",
                "matching-postgres", "matching-migrate", "matching-redis", "matching-api", "trend-intelligence",
                "cv-extraction", "main-backend", "kg-outbox-worker", "trend-intelligence-worker", "main-frontend",
                "embedding-service", "qdrant", "matching-vector-worker-semantic-demo", "validation-worker",
                "extraction-worker", "knowledge-graph-worker", "matching-api-semantic-demo", "jd-extraction",
                "matching-dispatcher", "matching-worker"
            )
            BaseBuildTargets = @("knowledge-graph-backend", "emerging-discovery", "trend-intelligence", "matching-api", "main-backend", "main-frontend")
            OptionalBuildTargets = @{
                Semantic = @("embedding-service")
                ModelExtraction = @("jd-extraction")
                CvExtraction = @("cv-extraction")
            }
            Frontend = "main-frontend"
            Databases = @{
                Main = "main-postgres"
                Matching = "matching-postgres"
                Analytics = "analytics-postgres"
            }
        }
        ReadinessExpectedServices = @{
            Base = @("main-postgres", "knowledge-graph-backend", "emerging-discovery", "trend-intelligence", "trend-intelligence-worker", "main-backend", "extraction-worker", "validation-worker", "kg-outbox-worker", "main-frontend")
            ModelExtraction = @("jd-extraction")
            CvExtraction = @("cv-extraction")
            Semantic = @()
        }
        PythonReadinessServices = @{
            Base = @("knowledge-graph-backend", "emerging-discovery", "trend-intelligence", "main-backend")
            ModelExtraction = @("jd-extraction")
            CvExtraction = @("cv-extraction")
        }
        RebuildTargets = @{
            "main-api" = @{ BuildService = "main-backend"; Services = @("main-backend"); NoDependencies = $true; Credential = "main" }
            "main" = @{ BuildService = "main-backend"; Services = @("main-backend", "extraction-worker", "validation-worker", "kg-outbox-worker"); NoDependencies = $true; Credential = "main" }
            "frontend" = @{ BuildService = "main-frontend"; Services = @("main-frontend"); NoDependencies = $true; Credential = $null }
            "knowledge-graph" = @{ BuildService = "knowledge-graph-backend"; Services = @("knowledge-graph-migrate", "knowledge-graph-bootstrap", "knowledge-graph-backend", "knowledge-graph-worker"); NoDependencies = $false; Credential = $null }
            "discovery" = @{ BuildService = "emerging-discovery"; Services = @("emerging-discovery"); NoDependencies = $true; Credential = $null }
            "trend" = @{ BuildService = "trend-intelligence"; Services = @("trend-intelligence", "trend-intelligence-worker"); NoDependencies = $true; Credential = "trend" }
            "matching" = @{ BuildService = "matching-api"; Services = @("matching-migrate", "matching-api", "matching-worker", "matching-dispatcher"); NoDependencies = $false; Credential = "matching" }
            "jd-extraction" = @{ BuildService = "jd-extraction"; Services = @("jd-extraction"); NoDependencies = $true; Credential = $null }
            "cv-extraction" = @{ BuildService = "cv-extraction"; Services = @("cv-extraction"); NoDependencies = $true; Credential = $null }
        }
    }
    "JobPulse" = @{
        MonorepoRoot = $MonorepoRoot
        ProjectDirectory = $jobpulseProjectDirectory
        # Relative paths in docker-compose.candidate.yml (build contexts "..",
        # env_file, bind mounts) are anchored at the compose project directory,
        # which must be infra so that ".." resolves to the JobPulse root.
        # CI pins the same --project-directory. The
        # environment file is passed explicitly via --env-file below, so dotenv
        # resolution does not depend on this path.
        ComposeProjectDirectory = Join-Path $JobPulseRoot "infra"
        ComposeFile = Join-Path $JobPulseRoot "infra/compose/docker-compose.candidate.yml"
        EnvironmentRoot = Join-Path $JobPulseRoot "infra"
        BuildContexts = $jobpulseBuildContexts
        Dockerfiles = $jobpulseDockerfiles
        ImageNames = $jobpulseImageNames
        ServiceNames = @{
            All = @(
                "main-postgres", "knowledge-graph-postgres", "knowledge-graph-migrate", "knowledge-graph-bootstrap",
                "knowledge-graph-backend", "knowledge-graph-worker", "matching-postgres", "matching-redis", "matching-migrate",
                "matching-api", "matching-worker", "matching-dispatcher", "analytics-postgres", "emerging-discovery-migrate",
                "emerging-discovery", "trend-intelligence", "trend-intelligence-worker", "main-backend", "extraction-worker",
                "validation-worker", "kg-outbox-worker", "main-frontend", "embedding-service", "qdrant",
                "matching-api-semantic-demo", "matching-vector-worker-semantic-demo", "jd-extraction", "cv-extraction",
                "cv-extraction-worker", "crawler-mysql", "crawler-api", "crawler-scheduler"
            )
            BaseBuildTargets = @("knowledge-graph-backend", "embedding-service", "emerging-discovery", "trend-intelligence", "matching-api", "main-backend", "main-frontend")
            OptionalBuildTargets = @{
                Semantic = @()
                ModelExtraction = @("jd-extraction")
                CvExtraction = @("cv-extraction")
                Crawler = @("crawler-api")
            }
            Frontend = "main-frontend"
            Databases = @{
                Main = "main-postgres"
                Matching = "matching-postgres"
                Analytics = "analytics-postgres"
            }
        }
        ReadinessExpectedServices = @{
            Base = @("main-postgres", "knowledge-graph-backend", "knowledge-graph-worker", "matching-postgres", "matching-redis", "matching-api", "matching-worker", "matching-dispatcher", "analytics-postgres", "embedding-service", "emerging-discovery", "trend-intelligence", "trend-intelligence-worker", "main-backend", "extraction-worker", "validation-worker", "kg-outbox-worker", "main-frontend")
            ModelExtraction = @("jd-extraction")
            CvExtraction = @("cv-extraction", "cv-extraction-worker")
            Semantic = @("embedding-service", "qdrant", "matching-api-semantic-demo", "matching-vector-worker-semantic-demo")
            Evidence = @("embedding-service", "qdrant")
            Crawler = @("crawler-mysql", "crawler-api", "crawler-scheduler")
        }
        PythonReadinessServices = @{
            Base = @("knowledge-graph-backend", "emerging-discovery", "trend-intelligence", "main-backend")
            ModelExtraction = @("jd-extraction")
            CvExtraction = @("cv-extraction")
        }
        RebuildTargets = @{
            "main-api" = @{ BuildService = "main-backend"; Services = @("main-backend"); NoDependencies = $true; Credential = "main" }
            "main" = @{ BuildService = "main-backend"; Services = @("main-backend", "extraction-worker", "validation-worker", "kg-outbox-worker"); NoDependencies = $true; Credential = "main" }
            "frontend" = @{ BuildService = "main-frontend"; Services = @("main-frontend"); NoDependencies = $true; Credential = $null }
            "knowledge-graph" = @{ BuildService = "knowledge-graph-backend"; Services = @("knowledge-graph-migrate", "knowledge-graph-bootstrap", "knowledge-graph-backend", "knowledge-graph-worker"); NoDependencies = $false; Credential = $null }
            "discovery" = @{ BuildService = "emerging-discovery"; Services = @("emerging-discovery-migrate", "emerging-discovery"); NoDependencies = $false; Credential = $null }
            "trend" = @{ BuildService = "trend-intelligence"; Services = @("trend-intelligence", "trend-intelligence-worker"); NoDependencies = $true; Credential = "trend" }
            "matching" = @{ BuildService = "matching-api"; Services = @("matching-migrate", "matching-api", "matching-worker", "matching-dispatcher"); NoDependencies = $false; Credential = "matching" }
            "jd-extraction" = @{ BuildService = "jd-extraction"; Services = @("jd-extraction"); NoDependencies = $true; Credential = $null }
            "cv-extraction" = @{ BuildService = "cv-extraction"; Services = @("cv-extraction", "cv-extraction-worker"); NoDependencies = $true; Credential = $null }
        }
    }
}

$selectedLayout = $ComposeLayoutMap[$Layout]
$ComposeMonorepoRoot = [string]$selectedLayout.MonorepoRoot
$ComposeRepositoryRoot = [string]$selectedLayout.ComposeProjectDirectory
$ComposeApplicationRoot = [string]$selectedLayout.ProjectDirectory
$ComposeFile = [string]$selectedLayout.ComposeFile
$ComposeEnvironmentTemplate = Join-Path $ComposeApplicationRoot ".env.example"
$ComposeEnvironmentFile = Join-Path $ComposeApplicationRoot ".env"
$ComposeApplicationScriptsRoot = if ($Layout -eq "JobPulse") {
    Join-Path $JobPulseRoot "apps/api/scripts"
}
else {
    Join-Path $ComposeApplicationRoot "scripts"
}
$ComposeApplicationConfigRoot = if ($Layout -eq "JobPulse") {
    Join-Path $JobPulseRoot "apps/api/config"
}
else {
    Join-Path $ComposeApplicationRoot "config"
}
$ComposeSemanticContractPath = Join-Path $ComposeApplicationConfigRoot "semantic-demo-contract.env"
$ComposeBuildContexts = $selectedLayout.BuildContexts
$ComposeDockerfiles = $selectedLayout.Dockerfiles
$ComposeImageNames = $selectedLayout.ImageNames
$ComposeServiceNames = $selectedLayout.ServiceNames
$ComposeReadinessExpectedServices = $selectedLayout.ReadinessExpectedServices
$ComposePythonReadinessServices = $selectedLayout.PythonReadinessServices
$ComposeRebuildTargets = $selectedLayout.RebuildTargets
$ComposeImageBuildTargets = @{}
foreach ($service in $ComposeBuildContexts.Keys) {
    $ComposeImageBuildTargets[$service] = @{
        Image = [string]$ComposeImageNames[$service]
        Context = [string]$ComposeBuildContexts[$service]
        Dockerfile = [string]$ComposeDockerfiles[$service]
    }
}
$script:ComposeProfileArguments = @()

function New-LocalSecret {
    param(
        [Parameter(Mandatory)]
        [string]$Prefix
    )

    # Hex avoids dotenv and SQL quoting surprises while retaining 256 bits of
    # randomness. Values are written only to the ignored local .env file.
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $hex = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    return "$Prefix$hex"
}

function Set-EnvironmentValue {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Value,
        [switch]$ReplaceExisting
    )

    $pattern = "^{0}=(.*)$" -f [regex]::Escape($Name)
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        $match = [regex]::Match($Lines[$index], $pattern)
        if ($match.Success) {
            if ($ReplaceExisting -or [string]::IsNullOrWhiteSpace($match.Groups[1].Value)) {
                $Lines[$index] = "$Name=$Value"
            }
            return
        }
    }
    $Lines.Add("$Name=$Value")
}

function Initialize-LocalEnvironment {
    if (-not (Test-Path -LiteralPath $ComposeEnvironmentTemplate)) {
        throw "Environment template not found: $ComposeEnvironmentTemplate"
    }

    $created = -not (Test-Path -LiteralPath $ComposeEnvironmentFile)
    if ($created) {
        Copy-Item -LiteralPath $ComposeEnvironmentTemplate -Destination $ComposeEnvironmentFile
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in [System.IO.File]::ReadAllLines($ComposeEnvironmentFile)) {
        $lines.Add($line)
    }

    # Template keys added after the local .env was first created must be
    # appended with their template defaults; otherwise compose files that
    # require them (for example ${VAR:?...}) fail interpolation on upgrades.
    foreach ($templateLine in [System.IO.File]::ReadAllLines($ComposeEnvironmentTemplate)) {
        if ($templateLine -match '^([A-Z_]+)=(.*)$') {
            Set-EnvironmentValue `
                -Lines $lines `
                -Name $Matches[1] `
                -Value $Matches[2]
        }
    }

    $secretNames = @(
        "MAIN_BACKEND_JWT_SECRET_KEY",
        "KNOWLEDGE_GRAPH_JWT_SECRET_KEY",
        "KNOWLEDGE_GRAPH_SERVICE_PASSWORD",
        "EMERGING_DISCOVERY_INTERNAL_TOKEN",
        "EMERGING_DISCOVERY_MAINTENANCE_TOKEN",
        "TREND_INTELLIGENCE_INTERNAL_TOKEN",
        "JD_EXTRACTION_INTERNAL_TOKEN",
        "CV_EXTRACTION_INTERNAL_TOKEN",
        "MATCHING_SERVICE_SIGNING_KEY",
        "MATCHING_UPSTREAM_SERVICE_TOKEN",
        "MAIN_POSTGRES_PASSWORD",
        "MATCHING_POSTGRES_PASSWORD"
    )
    foreach ($name in $secretNames) {
        Set-EnvironmentValue `
            -Lines $lines `
            -Name $name `
            -Value (New-LocalSecret -Prefix "jobgraph-local-") `
            -ReplaceExisting:$created
    }

    $workerToken = Get-LocalEnvironmentValueFromLines `
        -Lines $lines `
        -Name "MATCHING_WORKER_TOKEN"
    if (-not [string]::IsNullOrWhiteSpace($workerToken) -and $workerToken.Split(".").Count -ne 3) {
        # The worker can mint a short-lived JWT from the local signing key.
        # Plain-text template values are invalid JWT credentials.
        Set-EnvironmentValue `
            -Lines $lines `
            -Name "MATCHING_WORKER_TOKEN" `
            -Value "" `
            -ReplaceExisting
    }

    # UTF-8 without BOM is understood consistently by Docker Compose and Git.
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($ComposeEnvironmentFile, $lines, $utf8WithoutBom)
    if ($created) {
        Write-Host "Created ignored local environment file with generated secrets."
    }
}

function Get-LocalEnvironmentValueFromLines {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)]
        [string]$Name
    )

    $pattern = "^{0}=(.*)$" -f [regex]::Escape($Name)
    foreach ($line in $Lines) {
        $match = [regex]::Match($line, $pattern)
        if ($match.Success) {
            return $match.Groups[1].Value.Trim()
        }
    }
    return $null
}

function Get-LocalEnvironmentValue {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $ComposeEnvironmentFile)) {
        return $null
    }
    $pattern = "^{0}=(.*)$" -f [regex]::Escape($Name)
    foreach ($line in [System.IO.File]::ReadAllLines($ComposeEnvironmentFile)) {
        $match = [regex]::Match($line, $pattern)
        if ($match.Success) {
            return $match.Groups[1].Value.Trim()
        }
    }
    return $null
}

function Initialize-ComposeProfiles {
    param(
        [switch]$Semantic,
        [switch]$Evidence,
        [switch]$Full,
        [switch]$AllowMissingExternalCredentials
    )

    $cvEnabled = Get-LocalEnvironmentValue -Name "CV_EXTRACTION_ENABLED"
    $deepSeekKey = Get-LocalEnvironmentValue -Name "DEEPSEEK_API_KEY"
    $acquisitionEnabled = Get-LocalEnvironmentValue -Name "ACQUISITION_ENABLED"
    $evidenceOnly = $Evidence -and -not $Full
    $profiles = @()
    if ($Evidence -or $Full) {
        if ($Layout -ne "JobPulse") {
            throw "The -Evidence profile is only supported by the JobPulse Compose layout."
        }
        if ([string]::IsNullOrWhiteSpace($deepSeekKey) -and -not $AllowMissingExternalCredentials) {
            throw "JobPulse Evidence RAG requires DEEPSEEK_API_KEY."
        }
        $env:RAG_EVIDENCE_ENABLED = "true"
        $profiles += @("--profile", "evidence-rag")
    }
    if ($Full) {
        if ($Layout -ne "JobPulse") {
            throw "The -Full profile is only supported by the JobPulse Compose layout."
        }
        if ([string]::IsNullOrWhiteSpace($deepSeekKey) -and -not $AllowMissingExternalCredentials) {
            throw "Full JobPulse requires DEEPSEEK_API_KEY for JD/CV extraction."
        }
        if ($cvEnabled -ine "true") {
            throw "Full JobPulse requires CV_EXTRACTION_ENABLED=true."
        }
        if ($acquisitionEnabled -ine "true") {
            throw "Full JobPulse requires ACQUISITION_ENABLED=true."
        }
        $profiles += @(
            "--profile", "full",
            "--profile", "model-extraction",
            "--profile", "cv-extraction",
            "--profile", "semantic-demo",
            "--profile", "crawler"
        )
    }
    if ($Semantic) {
        $profiles += @("--profile", "semantic-demo")
    }
    if (-not $evidenceOnly -and -not [string]::IsNullOrWhiteSpace($deepSeekKey)) {
        $profiles += @("--profile", "model-extraction")
    }
    if (-not $evidenceOnly -and $cvEnabled -ieq "true") {
        if ([string]::IsNullOrWhiteSpace($deepSeekKey) -and -not $AllowMissingExternalCredentials) {
            throw "CV extraction is enabled but DEEPSEEK_API_KEY is empty. Set the key or use CV_EXTRACTION_ENABLED=false."
        }
        $profiles += @("--profile", "cv-extraction")
    }
    $script:ComposeProfileArguments = $profiles
}

function Get-DockerComposeVersion {
    $versionOutput = & docker compose version --short 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose is unavailable. Install Docker Compose >= $ComposeMinimumVersion."
    }

    $match = [regex]::Match(($versionOutput | Out-String), '\d+\.\d+\.\d+')
    if (-not $match.Success) {
        throw "Could not parse Docker Compose version from: $versionOutput"
    }

    return [version]$match.Value
}

function Assert-DockerComposeVersion {
    $installedVersion = Get-DockerComposeVersion
    if ($installedVersion -lt $ComposeMinimumVersion) {
        throw "Docker Compose $installedVersion is too old; version >= $ComposeMinimumVersion is required for include."
    }

    Write-Host "Docker Compose version check passed: $installedVersion (required >= $ComposeMinimumVersion)"
}

function Assert-DockerEngine {
    $serverVersion = & docker info --format "{{.ServerVersion}}" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($serverVersion | Out-String))) {
        throw "Docker engine is not running. Start Docker Desktop, wait until it reports Running, then retry."
    }
    Write-Host "Docker engine is running."
}

function Invoke-RootCompose {
    param(
        [Parameter(Mandatory)]
        [string[]]$ComposeArguments,
        [string[]]$AdditionalProfiles = @()
    )

    $profileArguments = @($script:ComposeProfileArguments)
    foreach ($profile in $AdditionalProfiles) {
        $profileArguments += @("--profile", [string]$profile)
    }

    & docker compose `
        --project-directory $ComposeRepositoryRoot `
        --env-file $ComposeEnvironmentFile `
        --file $ComposeFile `
        @profileArguments `
        @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Get-ConfiguredDockerBuildArguments {
    param(
        [Parameter(Mandatory)]
        [string[]]$Names
    )

    $arguments = @()
    foreach ($name in $Names) {
        # Match Docker Compose interpolation precedence: a process environment
        # override wins, followed by the ignored layout-specific .env file.
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value)) {
            $value = Get-LocalEnvironmentValue -Name $name
        }
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $arguments += @("--build-arg", "$name=$value")
        }
    }
    return $arguments
}

function Assert-KgGraphReadyForFull {
    if ($Layout -ne "JobPulse") {
        return
    }

    Write-Host "Checking published KG graph data before starting -Full..."
    try {
        Invoke-RootCompose `
            -AdditionalProfiles @("kg-init") `
            -ComposeArguments @("run", "--rm", "knowledge-graph-readiness-check")
    }
    catch {
        throw @"
The -Full profile requires at least one published KG position profile.
Initialize it once with:
  powershell -ExecutionPolicy Bypass -File scripts\compose-init-kg.ps1
The command expects real published facts to already be present in the Knowledge Graph database.
After it succeeds, rerun scripts\compose-up.ps1 -Full.
"@
    }
}

function Invoke-RootImageBuild {
    param(
        [Parameter(Mandatory)]
        [string]$Service
    )

    if (-not $ComposeImageBuildTargets.ContainsKey($Service)) {
        throw "No direct Docker build target is configured for service '$Service'."
    }

    $target = $ComposeImageBuildTargets[$Service]
    $image = [string]$target.Image
    $dockerfile = [string]$target.Dockerfile
    $context = [string]$target.Context
    Write-Host "Building image $image directly for service '$Service'..."
    $buildArguments = @("--tag", $image, "--file", $dockerfile)
    if ($Service -eq "main-frontend") {
        $buildArguments += @(Get-ConfiguredDockerBuildArguments -Names @("NPM_CONFIG_REGISTRY"))
    }
    else {
        $buildArguments += @(Get-ConfiguredDockerBuildArguments -Names @("PIP_INDEX_URL"))
    }
    if ($Service -eq "matching-api") {
        $buildArguments += @(Get-ConfiguredDockerBuildArguments -Names @("TORCH_CPU_INDEX_URL"))
        $installResponsibilityCe = Get-LocalEnvironmentValue -Name "MATCHING_INSTALL_RESPONSIBILITY_CE"
        $responsibilityCeMode = Get-LocalEnvironmentValue -Name "MATCHING_RESPONSIBILITY_CE_MODE"
        # CE mode without torch in the image crashes matching-api at startup;
        # force the dependency into the build instead of failing healthchecks.
        if ($responsibilityCeMode -eq "enabled" -and $installResponsibilityCe -ne "true") {
            Write-Host "MATCHING_RESPONSIBILITY_CE_MODE=enabled requires CE dependencies; forcing INSTALL_RESPONSIBILITY_CE=true for this build."
            $installResponsibilityCe = "true"
        }
        if (-not [string]::IsNullOrWhiteSpace($installResponsibilityCe)) {
            $buildArguments += @("--build-arg", "INSTALL_RESPONSIBILITY_CE=$installResponsibilityCe")
        }
    }
    if ($Service -eq "embedding-service") {
        $buildArguments += @(Get-ConfiguredDockerBuildArguments -Names @("PIP_DEFAULT_TIMEOUT", "PIP_RETRIES", "TORCH_CPU_INDEX_URL", "TORCH_CPU_VERSION"))
        $buildArguments += @("--build-arg", "TORCH_VARIANT=cpu")
    }
    if ($Service -in @("crawler-api", "crawler-scheduler")) {
        $buildArguments += @(Get-ConfiguredDockerBuildArguments -Names @("DEBIAN_MIRROR_URL", "PLAYWRIGHT_DOWNLOAD_HOST"))
    }
    & docker build @buildArguments $context
    if ($LASTEXITCODE -ne 0) {
        throw "docker build for service '$Service' failed with exit code $LASTEXITCODE."
    }
}

# The JobPulse main backend Dockerfile FROMs this base image. The Tesseract
# OCR layer changes rarely, so it is built on its own with an empty build
# context (the Dockerfile COPYs nothing); warm layer cache makes repeat runs
# nearly free while apps/api edits can never invalidate the apt layer again.
$MainBackendBaseImage = "jobpulse-main-backend-base:candidate"
$MainBackendBaseDockerfile = Join-Path $JobPulseRoot "apps/api/Dockerfile.base"

function Invoke-MainBackendBaseImageBuild {
    if ($Layout -ne "JobPulse") {
        return
    }
    Write-Host "Building base image $MainBackendBaseImage (Tesseract OCR layer)..."
    $emptyContext = Join-Path ([System.IO.Path]::GetTempPath()) "jobpulse-empty-build-context"
    New-Item -ItemType Directory -Path $emptyContext -Force | Out-Null
    $buildArguments = @("--tag", $MainBackendBaseImage, "--file", $MainBackendBaseDockerfile)
    $buildArguments += @(Get-ConfiguredDockerBuildArguments -Names @("DEBIAN_MIRROR_URL"))
    & docker build @buildArguments $emptyContext
    if ($LASTEXITCODE -ne 0) {
        throw "docker build for base image '$MainBackendBaseImage' failed with exit code $LASTEXITCODE."
    }
}

function Test-DockerImageExists {
    param(
        [Parameter(Mandatory)]
        [string]$Image
    )

    try {
        & docker image inspect $Image *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        # A missing local image is expected on first run and triggers the
        # sequential build path in compose-up.ps1.
        return $false
    }
}

function Sync-MatchingDatabaseCredential {
    $password = Get-LocalEnvironmentValue -Name "MATCHING_POSTGRES_PASSWORD"
    if ([string]::IsNullOrWhiteSpace($password)) {
        throw "MATCHING_POSTGRES_PASSWORD is missing from the local environment file."
    }

    # PostgreSQL applies POSTGRES_PASSWORD only when a volume is first created.
    # Reapplying the configured password through the local socket keeps old
    # volumes usable without deleting any competition/demo data.
    Invoke-RootCompose -ComposeArguments @(
        "up", "--detach", "--wait", "--no-build", [string]$ComposeServiceNames.Databases.Matching
    )
    $escapedPassword = $password.Replace("'", "''")
    $sql = "ALTER ROLE matching WITH PASSWORD '$escapedPassword';"
    $sql | & docker compose `
        --project-directory $ComposeRepositoryRoot `
        --env-file $ComposeEnvironmentFile `
        --file $ComposeFile `
        @script:ComposeProfileArguments `
        exec -T ([string]$ComposeServiceNames.Databases.Matching) psql -U matching -d matching -v ON_ERROR_STOP=1 *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not synchronize the matching database credential."
    }
    Write-Host "Matching database credential is synchronized; existing data was preserved."
}

function Sync-TrendDatabaseCredential {
    $password = Get-LocalEnvironmentValue -Name "TREND_INTELLIGENCE_POSTGRES_PASSWORD"
    if ([string]::IsNullOrWhiteSpace($password)) {
        throw "TREND_INTELLIGENCE_POSTGRES_PASSWORD is missing from the local environment file."
    }

    # The analytics volume initializes the trend_intelligence role only once;
    # reapply the configured password so upgraded .env files stay usable.
    Invoke-RootCompose -ComposeArguments @(
        "up", "--detach", "--wait", "--no-build", [string]$ComposeServiceNames.Databases.Analytics
    )
    $escapedPassword = $password.Replace("'", "''")
    $sql = "ALTER ROLE trend_intelligence WITH PASSWORD '$escapedPassword';"
    $sql | & docker compose `
        --project-directory $ComposeRepositoryRoot `
        --env-file $ComposeEnvironmentFile `
        --file $ComposeFile `
        @script:ComposeProfileArguments `
        exec -T ([string]$ComposeServiceNames.Databases.Analytics) psql -U analytics_admin -d postgres -v ON_ERROR_STOP=1 *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not synchronize the trend database credential."
    }
    Write-Host "Trend database credential is synchronized; existing data was preserved."
}

function Sync-MainDatabaseCredential {
    $password = Get-LocalEnvironmentValue -Name "MAIN_POSTGRES_PASSWORD"
    if ([string]::IsNullOrWhiteSpace($password)) {
        throw "MAIN_POSTGRES_PASSWORD is missing from the local environment file."
    }

    # POSTGRES_PASSWORD initializes only a new volume. Keep an existing
    # PostgreSQL volume usable when the ignored local credential changes.
    Invoke-RootCompose -ComposeArguments @(
        "up", "--detach", "--wait", "--no-build", [string]$ComposeServiceNames.Databases.Main
    )
    $escapedPassword = $password.Replace("'", "''")
    $sql = "ALTER ROLE jobgraph_main WITH PASSWORD '$escapedPassword';"
    $sql | & docker compose `
        --project-directory $ComposeRepositoryRoot `
        --env-file $ComposeEnvironmentFile `
        --file $ComposeFile `
        @script:ComposeProfileArguments `
        exec -T ([string]$ComposeServiceNames.Databases.Main) psql -U jobgraph_main -d jobgraph_main -v ON_ERROR_STOP=1 *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not synchronize the main database credential."
    }
    Write-Host "Main database credential is synchronized; existing data was preserved."
}
