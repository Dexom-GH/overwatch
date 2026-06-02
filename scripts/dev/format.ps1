# HOST (Windows dev) — auto-format / fix with ruff.
# Usage:  ./scripts/dev/format.ps1
$ErrorActionPreference = "Stop"
Write-Host "== ruff format =="
ruff format src tests
Write-Host "== ruff check --fix =="
ruff check --fix src tests
