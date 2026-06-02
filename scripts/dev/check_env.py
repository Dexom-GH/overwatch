"""Host dev-environment doctor for Overwatch.

Validates that the *host* development environment is sane before you rely on it:
the running interpreter is a real CPython (not the Microsoft Store stub), the
``overwatch`` package imports, and the dev toolchain (ruff/mypy/pytest) is
installed. Run it via ``scripts/dev/check_env.ps1`` (which resolves a real
interpreter first) or directly with a known-good interpreter::

    <real-python>\\python.exe scripts\\dev\\check_env.py

This is host tooling only -- it is NOT part of the shipped ``overwatch`` package
and never runs on the Jetson target. Kept import-light and 3.8-compatible so it
runs under any supported host interpreter.
"""

import importlib.util
import shutil
import sys
from typing import List, NamedTuple, Optional

# The Store stub and its redirector both live under a ``WindowsApps`` directory.
# Matching that path segment catches both the user alias
# (``...\\Microsoft\\WindowsApps\\python.exe``) and the package-install redirector
# (``...\\Program Files\\WindowsApps\\...AppInstaller...``).
_STORE_STUB_MARKER = "windowsapps"

_REQUIRED_DEV_TOOLS = ("ruff", "mypy", "pytest")
_MIN_PY = (3, 8)


class Check(NamedTuple):
    name: str
    ok: bool
    required: bool
    detail: str


def is_store_stub_path(path: Optional[str]) -> bool:
    """True if ``path`` looks like the Microsoft Store Python stub/redirector.

    The stub is a dead launcher that opens the Store instead of running Python;
    a dev environment must never resolve to it.
    """
    if not path:
        return False
    normalized = path.replace("\\", "/").lower()
    return _STORE_STUB_MARKER in normalized


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_environment() -> List[Check]:
    """Run every environment check and return the results (no printing)."""
    checks: List[Check] = []

    version = "{}.{}.{}".format(*sys.version_info[:3])
    checks.append(
        Check(
            name="python >= {}.{}".format(*_MIN_PY),
            ok=sys.version_info[:2] >= _MIN_PY,
            required=True,
            detail="running {}".format(version),
        )
    )

    checks.append(
        Check(
            name="interpreter is not the Store stub",
            ok=not is_store_stub_path(sys.executable),
            required=True,
            detail=sys.executable,
        )
    )

    checks.append(
        Check(
            name="import overwatch",
            ok=_module_available("overwatch"),
            required=True,
            detail="run `pip install -e .[dev]`" if not _module_available("overwatch") else "ok",
        )
    )

    missing = [t for t in _REQUIRED_DEV_TOOLS if not _module_available(t)]
    checks.append(
        Check(
            name="dev tools (ruff/mypy/pytest)",
            ok=not missing,
            required=True,
            detail="missing: {}".format(", ".join(missing)) if missing else "all present",
        )
    )

    # Warning (not required): bare `python` on PATH shadowed by the Store stub is a
    # common host trap -- the env still works via the resolved interpreter, but the
    # README's bare commands would fail, so surface it.
    path_python = shutil.which("python")
    checks.append(
        Check(
            name="bare `python` on PATH is real (not Store stub)",
            ok=not is_store_stub_path(path_python),
            required=False,
            detail=path_python or "not found on PATH",
        )
    )

    return checks


def main(argv: Optional[List[str]] = None) -> int:
    """Print the environment report; return 0 if all *required* checks pass."""
    checks = check_environment()
    print("Overwatch host dev-environment check\n" + "-" * 36)
    for c in checks:
        if c.ok:
            mark = "PASS"
        else:
            mark = "FAIL" if c.required else "WARN"
        print("  [{}] {:<46} {}".format(mark, c.name, c.detail))

    required_failures = [c for c in checks if c.required and not c.ok]
    if required_failures:
        print("\nFAILED: {} required check(s) did not pass.".format(len(required_failures)))
        print("See README 'Dev quickstart (host)' for the venv + interpreter setup.")
        return 1
    print("\nOK: host dev environment is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
