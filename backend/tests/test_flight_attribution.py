"""Regression tests for ``_match_fleet_aircraft`` (ADR-0007, v2.63.14+).

These cases lock in the strict fleet-attribution rules so the v2.49.0
fuzzy three-pass matcher cannot silently come back. Each scenario is a
hermetic unit test: ``AsyncSession`` is mocked with ``AsyncMock`` and
``execute`` returns canned ``Result``-shaped objects in the order the
matcher actually performs queries.

The matcher's call pattern is deterministic per branch:

* **Serial branch (``drone_serial`` truthy after strip):** one call to
  ``db.execute(select(Aircraft).where(...))``; the matcher reads
  ``result.scalar_one_or_none()``. No further calls.
* **Model branch (no serial, ``drone_model`` truthy after strip):** one
  call to ``db.execute(select(Aircraft))``; the matcher reads
  ``result.scalars().all()``. No further calls.
* **Both empty (after strip):** zero calls; returns ``None``.

That ordering is what these mocks rely on.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.conftest  # noqa: F401 — env stubs

from app.routers.flight_library import _match_fleet_aircraft


def _ac(model_name: str, serial_number: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        model_name=model_name,
        serial_number=serial_number,
    )


def _scalar_one_result(value):
    """Mock a Result whose ``scalar_one_or_none()`` returns ``value``."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _scalars_all_result(values):
    """Mock a Result whose ``scalars().all()`` returns ``values``."""
    r = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(values)
    r.scalars.return_value = scalars
    return r


def _session(*results):
    """Build an AsyncSession mock whose ``execute`` returns the given
    Result mocks in call order."""
    s = MagicMock()
    s.execute = AsyncMock(side_effect=list(results))
    return s


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Serial branch ──────────────────────────────────────────────────────


def test_serial_match_returns_aircraft():
    target = _ac("DJI Mavic 3 Pro", serial_number="1581F4ABC123XY")
    db = _session(_scalar_one_result(target))
    got = _run(_match_fleet_aircraft(db, "1581F4ABC123XY", "DJI Mavic 3 Pro"))
    assert got is target
    assert db.execute.await_count == 1, "model fallback must NOT run when serial matched"


def test_serial_match_is_case_insensitive_via_db():
    # We rely on the SQL `func.upper(...)` for case-insensitivity. From
    # the matcher's POV, the DB returns the row regardless of input case.
    target = _ac("DJI Mavic 3 Pro", serial_number="1581F4ABC123XY")
    db = _session(_scalar_one_result(target))
    got = _run(_match_fleet_aircraft(db, "1581f4abc123xy", "DJI Mavic 3 Pro"))
    assert got is target


def test_serial_present_but_unmatched_returns_none_no_model_fallback():
    """The v2.63.14 fix: serial is authoritative. If a serial is
    provided but no fleet record matches it, we do NOT silently fall
    back to model matching."""
    db = _session(_scalar_one_result(None))
    got = _run(_match_fleet_aircraft(db, "DEADBEEFNOTREAL", "DJI Mavic 3 Pro"))
    assert got is None
    assert db.execute.await_count == 1, (
        "model fallback must NOT run when a serial was provided — that "
        "was the regression closed in ADR-0007"
    )


def test_whitespace_only_serial_falls_through_to_model_branch():
    """A whitespace-only serial (some DJI parsers emit `'   '` when the
    field is present but blank) must be normalized to None at the top
    of the matcher so model matching can still attribute the flight."""
    target = _ac("DJI Mavic 3 Pro")
    db = _session(_scalars_all_result([target]))
    got = _run(_match_fleet_aircraft(db, "   ", "DJI Mavic 3 Pro"))
    assert got is target
    # Exactly one call — the serial branch was skipped (not "matched and
    # then fell through"); the model branch ran instead.
    assert db.execute.await_count == 1


# ── Model-only branch (no serial) ──────────────────────────────────────


def test_no_serial_unique_model_match_returns_aircraft():
    target = _ac("DJI Mavic 3 Pro")
    db = _session(_scalars_all_result([target, _ac("DJI Avata 2")]))
    got = _run(_match_fleet_aircraft(db, None, "DJI Mavic 3 Pro"))
    assert got is target


def test_no_serial_ambiguous_model_returns_none():
    """Two fleet aircraft of the same model and no serial → ambiguous.
    The matcher must refuse rather than guess."""
    db = _session(_scalars_all_result([
        _ac("DJI Mavic 3 Pro"),
        _ac("DJI Mavic 3 Pro"),
    ]))
    got = _run(_match_fleet_aircraft(db, None, "DJI Mavic 3 Pro"))
    assert got is None


def test_no_serial_no_model_match_returns_none():
    db = _session(_scalars_all_result([_ac("DJI Avata 2")]))
    got = _run(_match_fleet_aircraft(db, None, "DJI Mavic 3 Pro"))
    assert got is None


def test_prefix_match_no_longer_attributes_flight():
    """The v2.49.0 bug: parsed `"Mavic 3"` would be absorbed by fleet
    `"DJI Mavic 3 Pro"` because `mavic3pro.startswith("mavic3")`. The
    new matcher must reject this case (no exact normalized match)."""
    db = _session(_scalars_all_result([_ac("DJI Mavic 3 Pro")]))
    got = _run(_match_fleet_aircraft(db, None, "Mavic 3"))
    assert got is None, (
        "regression: prefix/substring matching must stay removed "
        "(see ADR-0007)"
    )


def test_substring_match_no_longer_attributes_flight():
    """Pass-3 substring rule was even broader. Lock it out."""
    db = _session(_scalars_all_result([_ac("DJI Matrice 30T")]))
    got = _run(_match_fleet_aircraft(db, None, "30"))
    assert got is None


# ── Empty / whitespace inputs ─────────────────────────────────────────


def test_both_inputs_none_returns_none_no_db_calls():
    db = _session()
    got = _run(_match_fleet_aircraft(db, None, None))
    assert got is None
    assert db.execute.await_count == 0


def test_both_inputs_whitespace_returns_none_no_db_calls():
    db = _session()
    got = _run(_match_fleet_aircraft(db, "   ", "   "))
    assert got is None
    assert db.execute.await_count == 0


def test_only_drone_model_normalizes_aliases():
    """The matcher relies on `_normalize_model` which resolves DJI
    short codes via `_DJI_ALIASES`. A fleet aircraft named `"DJI Matrice
    30T"` should be matched by parsed `"M30T"` (a known alias)."""
    target = _ac("DJI Matrice 30T")
    db = _session(_scalars_all_result([target]))
    got = _run(_match_fleet_aircraft(db, None, "M30T"))
    assert got is target
