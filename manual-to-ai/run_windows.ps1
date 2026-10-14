[CmdletBinding()]
param(
  [string]$Url = '',
  [string]$UrlsFile = '',
  [string]$Out = '.\manual_ai_export',
  [switch]$Crawl,
  [int]$MaxPages = 200,
  [string]$BrowserExecutable = '',
  [switch]$Login,
  [string]$ProfileDir = '.\private-login-profile'
)
$ErrorActionPreference = 'Stop'
if (-not $Url -and -not $UrlsFile) { throw 'Provide -Url or -UrlsFile' }
$runner = Join-Path $PSScriptRoot 'manual_to_ai.py'
$params = @($runner, '--out', $Out, '--max-pages', "$MaxPages")
if ($Url) { $params += @('--url', $Url) }
if ($UrlsFile) { $params += @('--urls-file', $UrlsFile) }
if ($Crawl) { $params += '--crawl' }
if ($BrowserExecutable) { $params += @('--browser-executable', $BrowserExecutable) }
if ($Login) { $params += @('--headed', '--login', '--profile-dir', $ProfileDir) }
& python @params
if ($LASTEXITCODE -ne 0) { throw "Export failed, exit code $LASTEXITCODE. See manifest.txt and terminal warnings." }
