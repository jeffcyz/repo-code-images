$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

if (-not $edge) {
    throw "Microsoft Edge executable not found."
}

Write-Host "Browser found: $edge"