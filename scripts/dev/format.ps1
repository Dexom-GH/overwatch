# HOST (Windows dev) -- auto-format / fix with ruff.
# Usage:  ./scripts/dev/format.ps1
$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_python.ps1"
$py = Get-OverwatchPython
Push-Location (Get-OverwatchRoot)
try {
    Write-Host "== ruff format =="
    & $py -m ruff format src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "== ruff check --fix =="
    & $py -m ruff check --fix src tests
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
