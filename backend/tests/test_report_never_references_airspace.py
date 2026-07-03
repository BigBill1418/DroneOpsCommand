"""HARD-RULE guard: the airspace/LAANC preflight is OPERATOR-FACING only
and must NEVER leak into the CLIENT report (ADR-0029 / ADR-0035).

The preflight is computed on demand and is never persisted on the mission,
never passed to any report builder, and never referenced by any
report-generation module. This static guard fails loudly if a future
change wires airspace/LAANC/preflight data into the report path.
"""

from __future__ import annotations

from pathlib import Path

# Modules that participate in building the CLIENT-facing mission report.
_REPORT_MODULES = (
    "app/services/claude_llm.py",
    "app/services/ollama.py",
    "app/services/pdf_generator.py",
    "app/services/map_renderer.py",
    "app/services/flight_metrics.py",
    "app/services/report_audience.py",
    "app/services/email_service.py",
    "app/tasks/celery_tasks.py",
)

# Tokens that, if present in a report module, would indicate the preflight
# has bled into the client deliverable.
_FORBIDDEN = ("airspace", "laanc", "preflight", "controlling_facility", "tfr")

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_report_modules_never_reference_airspace_or_laanc():
    offenders: list[str] = []
    for rel in _REPORT_MODULES:
        path = _BACKEND_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{rel}: contains {token!r}")
    assert not offenders, (
        "Airspace/LAANC/preflight data leaked into a client-report module — "
        "the preflight is operator-facing ONLY (ADR-0035/ADR-0029):\n  "
        + "\n  ".join(offenders)
    )


def test_mission_response_schema_has_no_preflight_field():
    """The preflight is computed on demand, never serialized onto the
    mission (which is what feeds the report context). If someone adds a
    persisted preflight column/field to the mission response, this fails."""
    schema = (_BACKEND_ROOT / "app/schemas/mission.py").read_text(encoding="utf-8").lower()
    for token in ("airspace", "laanc", "preflight"):
        assert token not in schema, (
            f"MissionResponse/MissionCreate schema references {token!r} — the "
            "preflight must not be persisted on the mission model."
        )
