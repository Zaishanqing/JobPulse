[CmdletBinding()]
param([string]$OutputDirectory = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "compose-common.ps1")

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $JobPulseRoot "dist\offline\full"
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$deepSeekKey = Get-LocalEnvironmentValue -Name "DEEPSEEK_API_KEY"
if ([string]::IsNullOrWhiteSpace($deepSeekKey)) {
    Write-Warning "DEEPSEEK_API_KEY is empty; the package will start offline, but JD/CV extraction and Evidence RAG cannot call DeepSeek."
}
else {
    Write-Host "A configured DEEPSEEK_API_KEY will be included in the sensitive offline runtime environment."
}

$images = Join-Path $OutputDirectory "jobpulse-images-full.tar"
$ce = Join-Path $OutputDirectory "jobpulse-responsibility-ce.tar"
$source = Join-Path $OutputDirectory "jobpulse-source.zip"
$data = Join-Path $OutputDirectory "jobpulse-data"

& (Join-Path $PSScriptRoot "export-offline-images.ps1") -Output $images -Full
& (Join-Path $PSScriptRoot "export-offline-ce-model.ps1") -Output $ce
& (Join-Path $PSScriptRoot "export-offline-source.ps1") -Output $source
& (Join-Path $PSScriptRoot "export-offline-data.ps1") -OutputDirectory $data

$artifacts = @(@($images, $ce, $source) | ForEach-Object { Get-Item -LiteralPath $_ }) + @(Get-ChildItem -LiteralPath $data -File)
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path.TrimEnd('\') + '\'
$checksumLines = @($artifacts | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $relative = $_.FullName.Substring($outputRoot.Length).Replace('\', '/')
    "$hash  $relative"
})
$checksumLines | Set-Content -LiteralPath (Join-Path $OutputDirectory "SHA256SUMS.txt") -Encoding ascii

foreach ($instructionName in @("README-OFFLINE.txt", "README-OFFLINE.md")) {
    $instructionPath = Join-Path $OutputDirectory $instructionName
    if (Test-Path -LiteralPath $instructionPath -PathType Leaf) {
        Remove-Item -LiteralPath $instructionPath -Force
    }
}

$totalBytes = ($artifacts | Measure-Object Length -Sum).Sum
$totalGb = [math]::Round($totalBytes / 1GB, 2)
Write-Host "Full offline package ready: $OutputDirectory ($totalGb GB plus manifests)"
