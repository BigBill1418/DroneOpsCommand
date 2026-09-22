"""Application version and the outbound HTTP identity built from it.

``APP_VERSION`` **must** equal the ``version=`` literal in ``app/main.py``.
``tests/test_app_version_parity.py`` parses that file and fails if they drift,
so a half-finished version bump is a red test rather than a wrong User-Agent in
production. This module is deliberately import-free: ``app.main`` imports the
routers, which import the services that need the User-Agent, so anything that
reaches back into ``app.main`` for the version would be a cycle.

Why a User-Agent at all: the OpenStreetMap Foundation's Tile Usage Policy
requires server-side tile consumers to send "a clear, unique User-Agent string"
identifying the application, and names sending a library's default UA as a
thing you must not do. ``staticmap`` defaults to ``User-Agent: StaticMap``.
See ADR-0046.
"""

from __future__ import annotations

APP_VERSION = "2.95.0"

#: Sent on every outbound tile request (report renderer, tile-health probe).
#: Identifies the app, the deployment and a reachable contact, per the OSMF
#: Tile Usage Policy's own example format.
USER_AGENT = (
    f"DroneOpsCommand/{APP_VERSION} "
    "(+https://droneops.barnardhq.com; bill@barnardhq.com)"
)
