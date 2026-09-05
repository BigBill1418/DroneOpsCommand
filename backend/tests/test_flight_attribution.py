"""Regression tests for ``_match_fleet_aircraft`` (ADR-0007, v2.63.14+).

These cases lock in the strict fleet-attribution rules so the v2.49.0
fuzzy three-pass matcher cannot silently come back. Each scenario is a
hermetic unit test: ``AsyncSession`` is mocked with ``AsyncMock`` and
``execute`` returns canned ``Result``-shaped objects in the order the
matcher actually performs queries.

The matcher's call pattern is deterministic per branch:

* **Serial branch (``drone_serial`` truthy after strip):**
  1. ``db.execute(select(Aircraft).where(...))`` — the exact-serial query;
     the matcher reads ``result.scalars().all()``.
  2. Only if that returned **zero** rows: ``db.execute(select(Aircraft))``
     — the ADR-0044 canonical-serial pass; the matcher reads
     ``result.scalars().all()``.
  No further calls. In particular the model branch is unreachable once a
  serial is present, which is the ADR-0007 invariant.
* **Model branch (no serial, ``drone_model`` truthy after strip):** one
  call to ``db.execute(select(Aircraft))``; the matcher reads
  ``result.scalars().all()``. No further calls.
* **Both empty (after strip):** zero calls; returns ``None``.

That ordering is what these mocks rely on.

ADR-0044 (v2.90.0) changed the exact-serial read from
``scalar_one_or_none()`` to ``scalars().all()`` — the former *raises*
``MultipleResultsFound`` on duplicate fleet serials, which the widened
candidate set makes a live crash risk on every import path. The
``_scalar_one_result`` helper is therefore gone; serial-branch mocks now
use ``_scalars_all_result`` for both queries.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.conftest  # noqa: F401 — env stubs

from app.routers.flight_library import _canonical_serial, _match_fleet_aircraft


def _ac(model_name: str, serial_number: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        model_name=model_name,
        serial_number=serial_number,
    )


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
    # asyncio.run (fresh loop per call), NOT asyncio.get_event_loop():
    # on Python 3.12, any earlier test that used asyncio.run() leaves the
    # event-loop policy with set_event_loop(None) (Runner.close() does
    # this), after which get_event_loop() raises RuntimeError. That made
    # all 12 tests here fail in full-suite order while passing in
    # isolation (test-hygiene fix, 2026-06-11).
    return asyncio.run(coro)


# ── Serial branch ──────────────────────────────────────────────────────


def test_serial_match_returns_aircraft():
    target = _ac("DJI Mavic 3 Pro", serial_number="1581F4ABC123XY")
    db = _session(_scalars_all_result([target]))
    got = _run(_match_fleet_aircraft(db, "1581F4ABC123XY", "DJI Mavic 3 Pro"))
    assert got is target
    assert db.execute.await_count == 1, (
        "neither the ADR-0044 canonical pass nor the model fallback may run "
        "once the exact-serial query resolved"
    )


def test_serial_match_is_case_insensitive_via_db():
    # We rely on the SQL `func.upper(...)` for case-insensitivity. From
    # the matcher's POV, the DB returns the row regardless of input case.
    target = _ac("DJI Mavic 3 Pro", serial_number="1581F4ABC123XY")
    db = _session(_scalars_all_result([target]))
    got = _run(_match_fleet_aircraft(db, "1581f4abc123xy", "DJI Mavic 3 Pro"))
    assert got is target


def test_serial_present_but_unmatched_returns_none_no_model_fallback():
    """The v2.63.14 fix: serial is authoritative. If a serial is
    provided but no fleet record matches it, we do NOT silently fall
    back to model matching.

    The decoy in the ADR-0044 canonical pass is the falsification: its
    ``model_name`` is exactly the parsed model, so a matcher that fell
    through to model matching would return it. Its serial shares no
    canonical form with the input, so the canonical pass must not select
    it either.
    """
    decoy = _ac("DJI Mavic 3 Pro", serial_number="1581F67QC23CN014")
    db = _session(_scalars_all_result([]), _scalars_all_result([decoy]))
    got = _run(_match_fleet_aircraft(db, "DEADBEEFNOTREAL", "DJI Mavic 3 Pro"))
    assert got is None, (
        "model fallback must NOT run when a serial was provided — that "
        "was the regression closed in ADR-0007"
    )
    assert db.execute.await_count == 2, (
        "exactly two queries: the exact-serial miss, then the ADR-0044 "
        "canonical pass. A third would mean the model branch ran."
    )


def test_duplicate_exact_serials_return_none_and_do_not_raise():
    """`scalar_one_or_none()` RAISES on two rows. ADR-0044 widened the
    candidate set, so the matcher must treat duplicates as ambiguity —
    an escaping exception in the startup backfill aborts every remaining
    row with a blanket 'Aircraft backfill failed'."""
    db = _session(_scalars_all_result([
        _ac("DJI Matrice 4TD", serial_number="1581F8HGX255P00A"),
        _ac("DJI Matrice 4TD (spare)", serial_number="1581F8HGX255P00A"),
    ]))
    got = _run(_match_fleet_aircraft(db, "1581F8HGX255P00A", "DJI Matrice 4TD"))
    assert got is None
    assert db.execute.await_count == 1, (
        "ambiguous exact match must stop there — no canonical pass, no model "
        "fallback"
    )


# ── ADR-0044: canonical (16-char header ⇄ 20-char OpenDroneLog) serials ──


def test_canonical_serial_strips_only_the_20_char_odl_suffix():
    # The 20-char ODL form reduces to the 16-char DJI header form.
    assert _canonical_serial("1581F8HGX255P00A0FEK") == "1581F8HGX255P00A"
    assert _canonical_serial("1581F7K3C25AA00DMZMG") == "1581F7K3C25AA00D"
    # 16-char header form is already canonical.
    assert _canonical_serial("1581F8HGX255P00A") == "1581F8HGX255P00A"
    # 14-char DJI FPV serials carry no ODL suffix — byte-identical in both forms.
    assert _canonical_serial("37Q7LA800BX0PN") == "37Q7LA800BX0PN"
    # Every other length is returned untouched — this is a fixed-width
    # truncation, NOT a prefix rule. A 12-char truncation stays 12 chars and
    # can therefore never equal a 16-char canonical.
    assert _canonical_serial("1581F8HGX255") == "1581F8HGX255"
    assert _canonical_serial("1581F8HGX255P00A0FE") == "1581F8HGX255P00A0FE"    # 19
    assert _canonical_serial("1581F8HGX255P00A0FEKK") == "1581F8HGX255P00A0FEKK"  # 21
    # Case + surrounding whitespace are normalized.
    assert _canonical_serial("  1581f8hgx255p00a0fek  ") == "1581F8HGX255P00A"


def test_odl_20char_flight_serial_matches_16char_aircraft_row():
    """The 88-flight production defect: `opendronelog_import` rows carry
    the 20-char form; the fleet row carries the 16-char header form."""
    target = _ac("DJI Matrice 4TD", serial_number="1581F8HGX255P00A")
    db = _session(
        _scalars_all_result([]),                            # exact: miss
        _scalars_all_result([target, _ac("DJI Avata 2", "1581F6W8A242N0A3")]),
    )
    got = _run(_match_fleet_aircraft(db, "1581F8HGX255P00A0FEK", ""))
    assert got is target
    assert db.execute.await_count == 2


def test_16char_flight_serial_matches_20char_aircraft_row():
    """The reverse direction is live in production: the `M3P - DECOM`
    fleet row was entered with the 20-char ODL form, while the DJI parser
    emits the 16-char header form for that airframe."""
    target = _ac("M3P - DECOM", serial_number="1581F67QE236L00A0027")
    db = _session(_scalars_all_result([]), _scalars_all_result([target]))
    got = _run(_match_fleet_aircraft(db, "1581F67QE236L00A", "DJI Mavic 3 Pro"))
    assert got is target


def test_exact_match_wins_over_canonical_when_both_forms_exist():
    """Exact equality is always outright. With both forms present in the
    fleet, a 20-char flight serial must land on the row that carries it
    verbatim — the canonical pass must never even run."""
    exact_row = _ac("M3P - DECOM", serial_number="1581F67QE236L00A0027")
    prefix_row = _ac("DJI Mavic 3 Pro", serial_number="1581F67QE236L00A")
    db = _session(
        _scalars_all_result([exact_row]),
        _scalars_all_result([exact_row, prefix_row]),  # must never be consumed
    )
    got = _run(_match_fleet_aircraft(db, "1581F67QE236L00A0027", "DJI Mavic 3 Pro"))
    assert got is exact_row
    assert db.execute.await_count == 1


def test_fpv_14char_serial_still_matches_exactly_and_not_the_other_fpv():
    """14-char DJI FPV serials carry no ODL suffix. They must keep
    matching exactly, and must not canonical-collide with the other FPV
    airframe in the fleet."""
    target = _ac("DJI FPV", serial_number="37Q7LA800BX0PN")
    db = _session(_scalars_all_result([target]))
    got = _run(_match_fleet_aircraft(db, "37Q7LA800BX0PN", "DJI FPV"))
    assert got is target

    # The two real FPV airframes must not canonical-collide with each other.
    # Fleet holds only the DECOM row; a flight from the OTHER FPV must stay
    # unattributed rather than landing on it.
    other = _ac("DJI FPV - DECOM", serial_number="37QBJ5WBD100DN")
    db2 = _session(_scalars_all_result([]), _scalars_all_result([other]))
    got2 = _run(_match_fleet_aircraft(db2, "37Q7LA800BX0PN", "DJI FPV"))
    assert got2 is None, (
        "neither 14-char FPV serial canonicalizes onto the other"
    )


def test_canonical_ambiguity_returns_none_rather_than_guessing():
    """Two fleet rows reducing to the same canonical serial (e.g. the
    16-char form and a 20-char form of the same hardware entered as
    separate airframes) → refuse, per ADR-0007's posture."""
    db = _session(_scalars_all_result([]), _scalars_all_result([
        _ac("DJI Matrice 4TD", serial_number="1581F8HGX255P00A"),
        _ac("DJI Matrice 4TD (dup)", serial_number="1581F8HGX255P00A0FEK"),
    ]))
    got = _run(_match_fleet_aircraft(db, "1581F8HGX255P00AZZZZ", "DJI Matrice 4TD"))
    assert got is None
    assert db.execute.await_count == 2, "must stop at the canonical pass"


def test_truncated_serial_does_not_match_a_longer_real_one():
    """The narrow rule ADR-0044 chose over 'either string starts with the
    other'. A short/partial serial canonicalizes to itself and can never
    become equal to a real 16- or 20-char canonical."""
    real = _ac("DJI Matrice 4TD", serial_number="1581F8HGX255P00A")
    for truncated in ("1581F8HGX255", "1581", "1581F8HGX255P00", "1581F8HGX255P00A0FE"):
        db = _session(_scalars_all_result([]), _scalars_all_result([real]))
        got = _run(_match_fleet_aircraft(db, truncated, "DJI Matrice 4TD"))
        assert got is None, f"truncated serial {truncated!r} must not match {real.serial_number!r}"


def test_blank_serial_aircraft_row_never_absorbs_via_canonical():
    """Production has a `DJI Mavic 3 Pro` row with a NULL serial. Its
    canonical is `''`; it must be excluded from the candidate set rather
    than compared as a real value."""
    blank = _ac("DJI Mavic 3 Pro", serial_number=None)
    empty = _ac("DJI Mavic 3 Pro (2)", serial_number="   ")
    db = _session(_scalars_all_result([]), _scalars_all_result([blank, empty]))
    got = _run(_match_fleet_aircraft(db, "1581F8HGX255P00A0FEK", "DJI Mavic 3 Pro"))
    assert got is None


def test_unknown_20char_serial_stays_unattributed_and_skips_model_fallback():
    """A genuinely unknown serial must not fall through to model
    matching, even when a fleet aircraft of exactly that model exists."""
    same_model = _ac("DJI Matrice 4TD", serial_number="1581F8HGX255P00A")
    db = _session(_scalars_all_result([]), _scalars_all_result([same_model]))
    got = _run(_match_fleet_aircraft(db, "9999ZZZZZZZZZZZZQQQQ", "DJI Matrice 4TD"))
    assert got is None
    assert db.execute.await_count == 2, (
        "a third query would mean the model branch ran — the ADR-0007 "
        "invariant this test exists to protect"
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
