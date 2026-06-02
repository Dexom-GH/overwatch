# HOST (Windows dev) -- resolve a REAL Python interpreter, never the Microsoft
# Store stub (...\WindowsApps\python.exe, a dead launcher that opens the Store).
# Dot-source this, then call Get-OverwatchPython:
#   . "$PSScriptRoot/_python.ps1"; $py = Get-OverwatchPython

function Test-StoreStub {
    param([string]$Path)
    if ([string]::IsNullOrEmpty($Path)) { return $false }
    return ($Path -replace '\\', '/').ToLower().Contains('windowsapps')
}

function Get-OverwatchRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

function Get-OverwatchPython {
    $repoRoot = Get-OverwatchRoot

    # 1) Project venv (preferred -- pins the interpreter for this repo).
    $venvPy = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPy) { return $venvPy }

    # 2) The `py` launcher resolves a real install and bypasses the stub.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe -and -not (Test-StoreStub $exe)) {
            return $exe.Trim()
        }
    }

    # 3) Per-user CPython installs under LocalAppData (newest first).
    $base = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    $candidate = Get-ChildItem -Path $base -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'python.exe' } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
    if ($candidate) { return $candidate }

    # 4) Whatever `python` resolves to, IF it isn't the Store stub.
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and -not (Test-StoreStub $cmd.Source)) { return $cmd.Source }

    throw "No real Python interpreter found (only the Microsoft Store stub). See README 'Dev quickstart (host)' -- create a .venv from a real CPython install."
}
