"""Host-only tests for the dev environment doctor (``scripts/dev/check_env.py``).

The doctor is a host dev-tooling helper, not part of the shipped ``overwatch``
package, so we load it by path rather than importing it as a module.
"""

import importlib.util
from pathlib import Path

import pytest

_CHECK_ENV = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "check_env.py"


def _load_check_env():
    spec = importlib.util.spec_from_file_location("check_env", _CHECK_ENV)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check_env():
    return _load_check_env()


@pytest.mark.parametrize(
    "path, expected",
    [
        (r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe", True),
        (r"C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller\redir.exe", True),
        ("/c/Users/x/AppData/Local/Microsoft/WindowsApps/python.exe", True),
        (r"C:\Users\x\AppData\Local\Programs\Python\Python312\python.exe", False),
        ("/usr/bin/python3", False),
        (None, False),
        ("", False),
    ],
)
def test_is_store_stub_path(check_env, path, expected):
    assert check_env.is_store_stub_path(path) is expected


def test_check_environment_is_healthy_under_test_interpreter(check_env):
    # We're running under a real interpreter with overwatch + dev tools installed,
    # so every *required* check must pass.
    checks = check_env.check_environment()
    required_failures = [c for c in checks if c.required and not c.ok]
    assert required_failures == [], required_failures


def test_main_returns_zero_when_healthy(check_env):
    assert check_env.main([]) == 0
