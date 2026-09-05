"""ADR-0043 §4.4 / D1 — the pilot's position never reaches a client artifact.

Operator decision D1 stores the raw pilot position track (AppGPS lat/lon).
Mission reports and the CSV/GPX/KML exports are **client deliverables**
(ADR-0029). Nothing from ``flight_details`` / ``flight_series`` may reach one
unless it is deliberately added, and the pilot's coordinates least of all.

**What each detector can and cannot see — stated up front, because a guard
whose blind spot is undocumented is worse than no guard.**

* ``test_report_modules_never_reference_the_sidecars`` is a STATIC scan of the
  report-producing modules' ASTs. It catches an import or a name reference —
  which is how the leak would actually arrive, since neither table can be
  queried without naming its model. It does **not** observe runtime SQL, so a
  reference reached through ``getattr``/string reflection would slip past. No
  such indirection exists in these modules today, and the positive control
  proves the scanner fires on a real reference rather than passing vacuously.
  The runtime ``before_execute`` capture the plan describes needs a live DB
  and lands with the DB-backed backfill tests.

* ``test_exports_emit_no_pilot_fields`` is a RUNTIME test: it calls the three
  exporters for real, with a flight object that carries a fully populated
  details sidecar and pilot coordinates, and asserts none of it appears in the
  bytes a client would receive.
"""

from __future__ import annotations

import ast
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.routers.flight_library import _export_csv, _export_gpx, _export_kml
from app.routers.reports import REPORT_READABLE_DETAIL_FIELDS

BACKEND = Path(__file__).resolve().parent.parent

#: Every module that can put bytes in front of a client.
CLIENT_ARTIFACT_MODULES = (
    "app/routers/reports.py",
    "app/services/pdf_generator.py",
    "app/services/mission_tracks.py",
    "app/services/map_renderer.py",
    "app/services/claude_llm.py",
    "app/services/ollama.py",
)

FORBIDDEN_NAMES = {"FlightDetails", "FlightSeries"}
FORBIDDEN_STRINGS = {"flight_details", "flight_series"}


def _references(source: str) -> set[str]:
    """Names and string literals in ``source`` that would reach the sidecars."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.add(node.attr)
        elif isinstance(node, ast.alias) and node.name in FORBIDDEN_NAMES:
            found.add(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("flight_details"):
                found.add(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for needle in FORBIDDEN_STRINGS:
                # Substring, so an f-string fragment or a raw SQL snippet is
                # caught too — not just an exact table name.
                if needle in node.value:
                    found.add(node.value)
    return found


@pytest.mark.parametrize("relpath", CLIENT_ARTIFACT_MODULES)
def test_report_modules_never_reference_the_sidecars(relpath):
    path = BACKEND / relpath
    assert path.exists(), f"{relpath} moved — update this guard's module list"
    # The guard constant's own docstring names the tables; strip comments and
    # docstrings by parsing, which ``_references`` already does (comments are
    # not in the AST). Docstrings ARE string constants, so exempt them.
    source = path.read_text()
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    hits = _references(source) - docstrings
    assert not hits, (
        f"{relpath} references the extended-log sidecars: {sorted(hits)}. "
        "Mission reports and exports are client deliverables (ADR-0029/0043 "
        "§4.4); pilot position and other extended data must not reach them. "
        "If this is deliberate, add the field to "
        "reports.REPORT_READABLE_DETAIL_FIELDS and amend the ADR first."
    )


def test_scanner_positive_control():
    """Prove the scanner fires. Without this, every assertion above could be
    passing because ``_references`` never finds anything at all."""
    leaky = (
        "from app.models.flight_details import FlightDetails\n"
        "def go(db):\n"
        "    return db.execute(select(FlightDetails.pilot_max_distance_m))\n"
    )
    assert _references(leaky) >= {"FlightDetails"}

    raw_sql = 'def go(db):\n    return db.execute("SELECT * FROM flight_series")\n'
    assert _references(raw_sql), "a raw-SQL reference must also be caught"


def test_the_allowlist_is_empty():
    """Nothing from the details surface is report-eligible yet. Growing this
    tuple is the deliberate, reviewable act §4.4 asks for — it should come
    with an ADR amendment, not slip in with a feature."""
    assert REPORT_READABLE_DETAIL_FIELDS == ()


# ── Runtime: the exports themselves ────────────────────────────────────

PILOT_LAT = 44.0521234
PILOT_LON = -123.0867891


def _flight_with_pilot_data():
    """A flight whose sidecar is fully populated with pilot position."""
    details = SimpleNamespace(
        pilot_sample_count=657,
        pilot_max_distance_m=412.7,
        pilot_avg_distance_m=188.3,
        pilot_track_stored=True,
    )
    series = [
        SimpleNamespace(source="pilot", name="pilot_lat", values=[PILOT_LAT] * 657),
        SimpleNamespace(source="pilot", name="pilot_lon", values=[PILOT_LON] * 657),
    ]
    return SimpleNamespace(
        id=uuid.uuid4(), name="Matrice 4TD Flight 12", details=details, series=series,
    )


def _track():
    return [
        {"lat": 44.0601, "lng": -123.0901, "alt": 92.4, "speed": 8.1,
         "timestamp": "2026-09-04T18:22:01+00:00"},
        {"lat": 44.0602, "lng": -123.0902, "alt": 94.1, "speed": 8.4,
         "timestamp": "2026-09-04T18:22:02+00:00"},
    ]


async def _body(response) -> str:
    """Drain a StreamingResponse the way Starlette does — ``body_iterator`` is
    an async generator even when the response was built from a sync iterator."""
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    return b"".join(chunks).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("exporter", [_export_csv, _export_gpx, _export_kml])
async def test_exports_emit_no_pilot_fields(exporter):
    """CSV/GPX/KML are shareable artifacts. They carry the AIRCRAFT track and
    nothing from the pilot sidecar — checked on the actual emitted bytes, not
    on the code that produces them."""
    body = await _body(exporter(_flight_with_pilot_data(), _track()))
    lowered = body.lower()

    for needle in (
        str(PILOT_LAT), str(PILOT_LON), "44.0521", "-123.0867",
        "pilot", "412.7", "188.3",
    ):
        assert needle not in lowered, (
            f"{exporter.__name__} leaked {needle!r} into a client-shareable export"
        )

    # Positive control: the aircraft track IS present, so the assertions above
    # are not passing on an empty document.
    assert "44.0601" in body
    assert "-123.0901" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("exporter", [_export_csv, _export_gpx, _export_kml])
async def test_exports_emit_no_altitude_limit_commentary(exporter):
    """ADR-0029 stays in force on every surface this plan touches: an export
    presents recorded values and never a verdict about them."""
    body = (await _body(exporter(_flight_with_pilot_data(), _track()))).lower()
    for phrase in ("400 ft", "part 107", "part-107", "exceed", "limit", "violation"):
        assert phrase not in body, f"{exporter.__name__} emitted compliance commentary: {phrase!r}"
