[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Repo,
    [string]$Out = '',
    [int]$MaxLines = 80,
    [int]$MaxWidth = 2000
)
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'repo_to_images.py'
if (-not (Test-Path -LiteralPath $script)) { throw "Missing script: $script" }
$argList = @($script, $Repo, '--max-lines', "$MaxLines", '--max-width', "$MaxWidth")
if ($Out) { $argList += @('--out', $Out) }
& python @argList
if ($LASTEXITCODE -ne 0) { throw "Export failed with exit code $LASTEXITCODE" }
