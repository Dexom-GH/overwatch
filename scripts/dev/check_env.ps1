# HOST (Windows dev) -- environment doctor. Resolves a real interpreter (never the
# Store stub) and runs the Python environment checks.
# Usage:  ./scripts/dev/check_env.ps1
$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_python.ps1"
$py = Get-OverwatchPython
Write-Host "Using interpreter: $py"
& $py (Join-Path $PSScriptRoot 'check_env.py')
exit $LASTEXITCODE
