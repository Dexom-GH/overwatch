# HOST (Windows dev) -- lint + type-check the package.
# Usage:  ./scripts/dev/lint.ps1
$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_python.ps1"
$py = Get-OverwatchPython
Push-Location (Get-OverwatchRoot)
try {
    Write-Host "== ruff =="
    & $py -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "== mypy =="
    & $py -m mypy src
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
