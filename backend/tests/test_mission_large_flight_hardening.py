"""ADR-0025 — large-mission flight handling is bulletproof.

Regression + contract coverage for the "savannah" OOM class: a mission with
many flights used to (a) OOM the worker on GET /api/missions/{id} because the
detail response serialized the full GPS track per attached flight, (b)
duplicate the whole track into flight_data_cache on every attach, and (c)
force one POST per flight when adding. These tests pin the fixes:

  A1 — detail/write responses strip track/gps_data/coordinates/telemetry,
       so the payload is O(rows), not O(track points).
  A2 — attaching a native flight stores SCALAR display fields only (no track).
  A3 — the report/map track loader is bounded: each track is loaded one at a
       time and decimated to a vertex cap.
  B  — POST /flights/bulk attaches many flights in ONE transaction,
       idempotently, with scalar caches; the legacy-ODL path is preserved.

They run against a REAL async session (in-memory SQLite + the greenlet
bridge), the way the handlers run in production — not a hand-rolled fake.
"""

from __future__ import annotations

import inspect
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.mission import (
    Mission,
    MissionFlight,
    MissionImage,
    MissionStatus,
    MissionType,
)
from app.schemas.mission import (
    MissionFlightBulkAttach,
    MissionFlightBulkItem,
    MissionFlightCreate,
)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (
            Customer.__table__,
            Aircraft.__table__,
            Flight.__table__,
            Mission.__table__,
            MissionFlight.__table__,
            MissionImage.__table__,
        ):
            await conn.run_sync(table.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _user():
    return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())


def _big_track(n: int = 5000) -> list[dict]:
    """A track large enough to exceed the render/load vertex cap."""
    return [{"lat": 44.0 + i * 1e-6, "lng": -123.0 + i * 1e-6} for i in range(n)]


async def _seed_flight(db: AsyncSession, *, name: str, aircraft_id=None, track=None) -> Flight:
    flight = Flight(
        name=name,
        drone_model="Mavic 3 Pro",
        drone_serial=f"SN-{name}",
        duration_secs=600.0,
        total_distance=1200.0,
        max_altitude=100.0,
        max_speed=15.0,
        home_lat=44.0,
        home_lon=-123.0,
        point_count=len(track) if track else 0,
        source="native",
        gps_track=track,
        aircraft_id=aircraft_id,
    )
    db.add(flight)
    await db.flush()
    return flight


async def _seed_mission(db: AsyncSession) -> Mission:
    mission = Mission(
        title="Savannah",
        status=MissionStatus.DRAFT,
        mission_type=MissionType.MAPPING,
    )
    db.add(mission)
    await db.flush()
    return mission


# ── A2: attach stores scalars only, never the track ──────────────────────
@pytest.mark.asyncio
async def test_single_attach_native_stores_scalar_cache_without_track(db: AsyncSession):
    from app.routers.missions import add_flight

    mission = await _seed_mission(db)
    flight = await _seed_flight(db, name="F1", track=_big_track())
    await db.commit()

    resp = await add_flight(
        mission_id=mission.id,
        data=MissionFlightCreate(flight_id=flight.id, flight_data_cache={}),
        db=db,
        _user=_user(),
    )

    # Scalar display fields present; GPS track absent.
    cache = resp.flight_data_cache or {}
    assert cache.get("duration_secs") == 600.0
    assert cache.get("drone_model") == "Mavic 3 Pro"
    assert "track" not in cache and "gps_data" not in cache and "coordinates" not in cache

    # And the persisted row carries no track either (the root data-model fix).
    row = (await db.execute(select(MissionFlight))).scalar_one()
    assert "track" not in (row.flight_data_cache or {})


# ── A1: detail GET strips heavy keys + payload stays O(rows) ──────────────
@pytest.mark.asyncio
async def test_detail_get_strips_track_and_payload_is_small(db: AsyncSession):
    from app.routers.missions import get_mission

    mission = await _seed_mission(db)
    # Simulate a LEGACY row that still has a fat track baked into the cache.
    fat = _big_track()
    db.add(
        MissionFlight(
            mission_id=mission.id,
            opendronelog_flight_id="odl-legacy",
            flight_data_cache={
                "display_name": "Legacy",
                "duration_secs": 600,
                "track": fat,
                "gps_data": fat,
                "telemetry": fat,
            },
        )
    )
    await db.commit()

    resp = await get_mission(mission_id=mission.id, db=db, _user=_user())

    cache = resp.flights[0].flight_data_cache or {}
    assert cache.get("display_name") == "Legacy"
    assert cache.get("duration_secs") == 600
    for heavy in ("track", "gps_data", "coordinates", "telemetry"):
        assert heavy not in cache, f"{heavy} must be stripped from detail payload"

    # O(rows): the serialized body must NOT contain the 5000-point track.
    body = resp.model_dump_json()
    assert len(body) < 5000, "detail payload should be tiny once the track is stripped"

    # The stored row is untouched — stripping is outbound-only, no data loss,
    # no write-on-read.
    row = (await db.execute(select(MissionFlight))).scalar_one()
    assert len(row.flight_data_cache["track"]) == len(fat)


# ── B: bulk attach — one txn, idempotent, scalar caches, ODL preserved ────
@pytest.mark.asyncio
async def test_bulk_attach_many_in_one_call_with_scalar_caches(db: AsyncSession):
    from app.routers.missions import add_flights_bulk

    mission = await _seed_mission(db)
    air = Aircraft(model_name="Mavic 3 Pro", manufacturer="DJI")
    db.add(air)
    await db.flush()
    flights = [
        await _seed_flight(db, name=f"F{i}", aircraft_id=air.id, track=_big_track(3000))
        for i in range(5)
    ]
    await db.commit()

    items = [MissionFlightBulkItem(flight_id=f.id, flight_data_cache={"source": "native"}) for f in flights]
    # Include a legacy-ODL item to prove that path still works.
    items.append(
        MissionFlightBulkItem(
            opendronelog_flight_id="odl-7",
            flight_data_cache={"display_name": "ODL", "drone_model": "Mini 4 Pro", "track": _big_track(10)},
        )
    )

    created = await add_flights_bulk(
        mission_id=mission.id, data=MissionFlightBulkAttach(flights=items), db=db, _user=_user()
    )
    await db.commit()

    assert len(created) == 6
    # Native rows keep flight linkage + scalar cache (no track). ADR-0035: the
    # junction no longer copies the aircraft — reports resolve it live from
    # Flight.aircraft via flight_id, so the junction aircraft_id stays NULL.
    native_rows = [r for r in created if r.flight_id is not None]
    assert len(native_rows) == 5
    for r in native_rows:
        assert r.aircraft_id is None
        assert "track" not in (r.flight_data_cache or {})
    # ODL row preserved with its id + scalar display fields, track stripped.
    odl_rows = [r for r in created if r.opendronelog_flight_id == "odl-7"]
    assert len(odl_rows) == 1
    assert odl_rows[0].flight_data_cache.get("display_name") == "ODL"
    assert "track" not in odl_rows[0].flight_data_cache

    total = (await db.execute(select(MissionFlight))).scalars().all()
    assert len(total) == 6


@pytest.mark.asyncio
async def test_bulk_attach_is_idempotent(db: AsyncSession):
    from app.routers.missions import add_flights_bulk

    mission = await _seed_mission(db)
    flight = await _seed_flight(db, name="F1", track=_big_track(100))
    await db.commit()

    item = MissionFlightBulkItem(flight_id=flight.id, flight_data_cache={})
    first = await add_flights_bulk(
        mission_id=mission.id, data=MissionFlightBulkAttach(flights=[item]), db=db, _user=_user()
    )
    await db.commit()
    assert len(first) == 1

    # Re-submit the SAME flight (plus a within-batch duplicate) — all skipped.
    second = await add_flights_bulk(
        mission_id=mission.id,
        data=MissionFlightBulkAttach(flights=[item, item]),
        db=db,
        _user=_user(),
    )
    await db.commit()
    assert second == []

    rows = (await db.execute(select(MissionFlight))).scalars().all()
    assert len(rows) == 1, "idempotent re-attach must not create duplicate rows"


def test_bulk_attach_uses_a_single_flush():
    """Source guard: the bulk handler must commit all rows in ONE flush —
    the whole point of the endpoint (and what keeps it a single transaction
    for hundreds of flights)."""
    from app.routers import missions

    src = inspect.getsource(missions.add_flights_bulk)
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert code.count("await db.flush()") == 1, (
        "add_flights_bulk must flush exactly once (single transaction)"
    )


# ── A3: bounded per-track loading ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_bounded_track_loader_decimates_and_loads_on_demand(db: AsyncSession):
    from app.services.map_renderer import MAX_RENDER_VERTICES_PER_TRACK
    from app.services.mission_tracks import load_bounded_flight_tracks

    mission = await _seed_mission(db)
    # Native flight: track lives on Flight.gps_track, cache has NO track (A2).
    f1 = await _seed_flight(db, name="N1", track=_big_track(8000))
    db.add(MissionFlight(mission_id=mission.id, flight_id=f1.id, flight_data_cache={"display_name": "N1"}))
    # Legacy flight: track only in the cache (no Flight row).
    db.add(
        MissionFlight(
            mission_id=mission.id,
            opendronelog_flight_id="odl-legacy",
            flight_data_cache={"track": _big_track(9000)},
        )
    )
    await db.commit()

    loaded = await db.execute(select(Mission).where(Mission.id == mission.id))
    mission = loaded.scalar_one()

    flights = await load_bounded_flight_tracks(db, mission)

    assert len(flights) == 2
    for fd in flights:
        track = fd["flight_data_cache"]["track"]
        assert 0 < len(track) <= MAX_RENDER_VERTICES_PER_TRACK + 1, (
            "each track must be decimated to the bounded vertex cap"
        )


# ── Pure unit: the strip helper ───────────────────────────────────────────
def test_strip_cache_heavy_keys_is_pure_and_safe():
    from app.routers.missions import _strip_cache_heavy_keys

    assert _strip_cache_heavy_keys(None) is None
    # Nothing heavy → original object returned untouched (cheap common case).
    scalar = {"duration_secs": 10, "display_name": "x"}
    assert _strip_cache_heavy_keys(scalar) is scalar
    # Heavy keys removed; scalars preserved; input not mutated.
    heavy = {"duration_secs": 10, "track": [1, 2, 3], "telemetry": [4]}
    out = _strip_cache_heavy_keys(heavy)
    assert out == {"duration_secs": 10}
    assert "track" in heavy, "must not mutate the input dict"
