"""`app/version.py` and `app/main.py` must carry the same version.

``APP_VERSION`` feeds the outbound User-Agent the OSMF Tile Usage Policy
requires (ADR-0046); ``main.py``'s literal is what ``/openapi.json`` reports and
what the fleet deployer's version assertion reads. They are bumped by hand, in
the same commit, per CLAUDE.md — so the only thing standing between a
half-finished bump and a production server identifying itself as the wrong
release is this test.

``main.py`` is parsed rather than imported: importing it constructs the whole
FastAPI app, and this assertion should not depend on that succeeding.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.version import APP_VERSION, USER_AGENT

_MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
_VERSION_RE = re.compile(r'^\s*version="(\d+\.\d+\.\d+)",\s*$', re.M)


def test_main_declares_exactly_one_fastapi_version():
    matches = _VERSION_RE.findall(_MAIN.read_text(encoding="utf-8"))
    assert len(matches) == 1, f"expected one version= literal in main.py, found {matches}"


def test_version_module_matches_main():
    declared = _VERSION_RE.findall(_MAIN.read_text(encoding="utf-8"))[0]
    assert APP_VERSION == declared, (
        f"app/version.py says {APP_VERSION}, app/main.py says {declared} — "
        "finish the version bump (CLAUDE.md lists every location)"
    )


def test_user_agent_identifies_the_app_and_a_contact():
    """The OSMF policy names sending a library default UA as a thing you must
    not do, and requires the string to identify the application."""
    assert USER_AGENT.startswith(f"DroneOpsCommand/{APP_VERSION} ")
    assert "droneops.barnardhq.com" in USER_AGENT
    assert "bill@barnardhq.com" in USER_AGENT
    assert "StaticMap" not in USER_AGENT
