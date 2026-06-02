"""Overwatch — edge-AI farm monitoring system.

V1 scope: animal monitoring (counting, vision-only individual ID, health) on a
Jetson Xavier NX. See CLAUDE.md and docs/ for the full picture.

This top-level package must import cleanly on the Windows dev host. Target-only
submodules (capture.zed_source, inference.deepstream.*, inference.reid.
megadescriptor) guard their heavy/target-only imports so importing `overwatch`
never pulls in pyzed / Jetson torch / DeepStream bindings. See docs/SOFTWARE_STACK.md.
"""

# THE single source of the project version (pyproject reads it via setuptools
# dynamic attr). Bump here only. Scheme: CalVer YYYY.MINOR.PATCH (e.g. 2026.6.0)
# once we cut the first release; "0.0.0" means pre-release / no tag yet.
# See docs/RELEASING.md and docs/DECISIONS/0004-versioning-and-release.md.
__version__ = "0.0.0"
