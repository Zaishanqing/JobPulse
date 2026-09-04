[CmdletBinding()]
param(
    [ValidateSet(
        "Main", "KnowledgeGraph", "MatchingService", "EmergingDiscovery", "Crawler",
        "JDExtraction", "CVExtraction", "Frontend"
    )]
    [Parameter(Mandatory)][string]$Suite
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Completed = [System.Collections.Generic.List[string]]::new()

function Get-ModulePath {
    param(
        [Parameter(Mandatory)][string]$SuitePath
    )
    $selected = Join-Path $RepositoryRoot $SuitePath
    if (-not (Test-Path -LiteralPath $selected)) {
        throw "Module path not found: $selected"
    }
    return $selected
}

function Invoke-TestModule {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][scriptblock]$Command
    )

    Write-Host ""
    Write-Host "=== Testing: $Name ==="
    $ModulePath = $Path
    $PytestBaseTemp = Join-Path `
        $ModulePath `
        (".pytest_tmp_" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    Push-Location -LiteralPath $ModulePath
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            throw "$Name tests exited with code $LASTEXITCODE"
        }
        $Completed.Add($Name)
        Write-Host "=== Passed: $Name ==="
    }
    finally {
        Pop-Location
    }
}

function Invoke-Main {
    Invoke-TestModule "Main backend" (Get-ModulePath "apps/api") {
        python -m pytest --basetemp $PytestBaseTemp
    }
}

function Invoke-KnowledgeGraph {
    Invoke-TestModule "Knowledge Graph" (Get-ModulePath "services/knowledge-graph") {
        python -m pytest --basetemp $PytestBaseTemp
    }
}

try {
    switch ($Suite) {
        "Main" { Invoke-Main }
        "KnowledgeGraph" {
            Invoke-KnowledgeGraph
        }
        "MatchingService" {
            Invoke-TestModule "Matching Service" (Get-ModulePath "services/matching-service") {
                python -m pytest --basetemp $PytestBaseTemp
            }
        }
        "EmergingDiscovery" {
            Invoke-TestModule "Emerging Discovery" (Get-ModulePath "services/emerging-discovery") {
                python -m pytest --basetemp $PytestBaseTemp
            }
        }
        "Crawler" {
            Invoke-TestModule "Crawler" (Get-ModulePath "services/crawler") {
                python -m pytest --basetemp $PytestBaseTemp
            }
        }
        "JDExtraction" {
            Invoke-TestModule "JD Extraction" (Get-ModulePath "services/jd-extraction") {
                python -m pytest --basetemp $PytestBaseTemp
            }
        }
        "CVExtraction" {
            Invoke-TestModule "CV Extraction" (Get-ModulePath "services/cv-extraction") {
                python -m pytest --basetemp $PytestBaseTemp
            }
        }
        "Frontend" {
            Invoke-TestModule "Main frontend" (Get-ModulePath "apps/web") { npm test }
        }
    }
}
catch {
    Write-Host ""
    Write-Host "=== Test summary: FAILED ==="
    $CompletedText = if ($Completed.Count -eq 0) { "none" } else { $Completed -join ", " }
    Write-Host "Completed modules: $CompletedText"
    Write-Error $_
    exit 1
}

Write-Host ""
Write-Host "=== Test summary: PASSED ==="
Write-Host "Completed modules: $($Completed -join ', ')"
