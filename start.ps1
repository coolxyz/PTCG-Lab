param(
    [switch]$Setup,
    [switch]$Check,
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if ($Setup) {
    & $Python -X utf8 (Join-Path $PSScriptRoot 'scripts/portable/setup.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Run .\start.ps1 -Setup first (Python 3.14+, Node/npm and Git required).'
}
$launchArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'scripts/start.py'), '--port', "$Port")
if ($Check) { $launchArgs += '--check' }
& $projectPython @launchArgs
exit $LASTEXITCODE
