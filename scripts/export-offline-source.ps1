[CmdletBinding()]
param([string]$Output = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $JobPulseRoot "dist\offline\jobpulse-source.zip"
}
$outputParent = Split-Path -Parent $Output
if (-not (Test-Path -LiteralPath $outputParent)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}

$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("jobpulse-source-" + [guid]::NewGuid().ToString("N"))
$sourceRoot = $staging
New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null
try {
    $paths = @(& git -C $JobPulseRoot ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0 -or $paths.Count -eq 0) {
        throw "Could not enumerate JobPulse source files from Git."
    }
    foreach ($path in $paths) {
        $relative = $path.Replace('/', '\')
        $source = Join-Path $JobPulseRoot $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            continue
        }
        $target = Join-Path $sourceRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
    Compress-Archive -Path (Join-Path $sourceRoot '*') -DestinationPath $Output -CompressionLevel Optimal -Force
}
finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

$sizeMb = [math]::Round((Get-Item -LiteralPath $Output).Length / 1MB)
Write-Host "Offline source package ready: $Output ($sizeMb MB)"
