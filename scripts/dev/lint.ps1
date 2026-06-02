# HOST (Windows dev) — lint + type-check the package.
# Usage:  ./scripts/dev/lint.ps1
$ErrorActionPreference = "Stop"
Write-Host "== ruff =="
ruff check src tests
Write-Host "== mypy =="
mypy src
