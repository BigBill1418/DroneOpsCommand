"""ADR-0027 — regression tests for ``_generate_flight_name``.

Locks in the fixed naming contract: the trailing sequence is the flight's
**start-time rank within its (label, operator-local day) group**, zero-padded,
with a conflict-bump so a duplicate auto name can never be produced. These guard
against the pre-ADR-0027 bug where the sequence was a fleet-wide ``created_at``
tally inside a ``start_time``-day window — which collided on ``_0001`` whenever
start-day ≠ ingest-day and produced garbage descending sequences.

Hermetic: the ``AsyncSession`` is mocked. ``_generate_flight_name`` issues two
queries in order — (1) the rank count, (2) the set of names already taken in the
group — so each test feeds two canned results.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.conftest  # noqa: F401 — env stubs

from app.routers.flight_library import _generate_flight_name


def _mock_db(rank_count: int, taken_names: list[str]):
    """An AsyncSession whose two execute() calls return the rank count then the
    taken-name rows, in the order ``_generate_flight_name`` issues them."""
    rank_res = MagicMock()
    rank_res.scalar.return_value = rank_count
    taken_res = MagicMock()
    taken_res.all.return_value = [(n,) for n in taken_names]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[rank_res, taken_res])
    return db


@pytest.mark.asyncio
async def test_first_flight_of_group_is_0001():
    db = _mock_db(rank_count=0, taken_names=[])
    name = await _generate_flight_name(
        db, "DJI Mavic 4 Pro", None, datetime(2026, 6, 27, 18, 1, 49)
    )
    # 11:01 PDT on 2026-06-27 (18:01 UTC) → Pacific day 20260627, rank 1.
    assert name == "DJI-Mavic-4-Pro_20260627_0001"


@pytest.mark.asyncio
async def test_sequence_is_start_time_rank():
    # Two flights of this group already started earlier today → this one is 3rd.
    db = _mock_db(
        rank_count=2,
        taken_names=["DJI-Mavic-4-Pro_20260627_0001", "DJI-Mavic-4-Pro_20260627_0002"],
    )
    name = await _generate_flight_name(
        db, "DJI Mavic 4 Pro", None, datetime(2026, 6, 27, 20, 0, 0)
    )
    assert name == "DJI-Mavic-4-Pro_20260627_0003"


@pytest.mark.asyncio
async def test_conflict_bump_prevents_duplicate():
    # Rank says slot 1, but _0001 is already taken (out-of-order import / two
    # airframes sharing a model label) → bump to the first free slot.
    db = _mock_db(
        rank_count=0,
        taken_names=["DJI-Mavic-4-Pro_20260627_0001", "DJI-Mavic-4-Pro_20260627_0002"],
    )
    name = await _generate_flight_name(
        db, "DJI Mavic 4 Pro", None, datetime(2026, 6, 27, 18, 0, 0)
    )
    assert name == "DJI-Mavic-4-Pro_20260627_0003"


@pytest.mark.asyncio
async def test_date_token_is_operator_local_not_utc():
    # 19:14 PDT on 2026-06-27 is stored as 02:14 UTC on 2026-06-28. The name must
    # carry the Pacific calendar day (0627), not the UTC day (0628) — ADR-0017.
    db = _mock_db(rank_count=0, taken_names=[])
    name = await _generate_flight_name(
        db, "DJI Mavic 4 Pro", None, datetime(2026, 6, 28, 2, 14, 37)
    )
    assert name == "DJI-Mavic-4-Pro_20260627_0001"


@pytest.mark.asyncio
async def test_fleet_model_name_takes_priority_for_label():
    db = _mock_db(rank_count=0, taken_names=[])
    fleet = SimpleNamespace(model_name="DJI Matrice 4TD", serial_number="X")
    name = await _generate_flight_name(
        db, "M4TD", fleet, datetime(2026, 6, 27, 18, 0, 0)
    )
    assert name == "DJI-Matrice-4TD_20260627_0001"
