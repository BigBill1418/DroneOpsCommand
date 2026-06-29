"""ADR-0026 — duplicate-flight defence + accurate, unit-correct report metrics.

Root cause: the single-attach path had ZERO dedup and there was NO DB
constraint on ``mission_flights (mission_id, flight_id)``. A retry storm
inserted the same flight many times (the "Savannah Bananas Games" mission:
50 rows for 27 unique flights), and the report engine summed over every row,
inflating flight count / time / distance 100-160%.

These tests pin the full fix, against a REAL async session (in-memory SQLite +
the greenlet bridge), the way the handlers run in production:

  RC-1  — single-add is idempotent (returns the existing row, no duplicate).
  RC-1  — a partial UNIQUE index blocks duplicate attachments at the DB.
  RC-1  — the report engine dedups defensively (a stray duplicate row can
          never re-inflate count / duration / distance).
  RC-2  — area is measured on FULL-RESOLUTION geometry, not the strided
          render decimation.
  WRITE — ghost / aborted launches are excluded from altitude stats.
  WRITE — altitude is formatted with the correct unit (metres, source unit)
          plus an explicit feet conversion — never a bare/mislabeled value.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.mission import Mission, MissionFlight, MissionImage, MissionStatus, MissionType
from app.schemas.mission import MissionFlightCreate


# Partial unique indexes mirroring migration 0003 (the prod guard). Created on
# the SQLite test DB so the constraint behaviour is exercised on a real engine.
_UNIQUE_INDEXES = (
    "CREATE UNIQUE INDEX uq_mission_flights_mission_flight "
    "ON mission_flights (mission_id, flight_id) WHERE flight_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_mission_flights_mission_odl "
    "ON mission_flights (mission_id, opendronelog_flight_id) "
    "WHERE opendronelog_flight_id IS NOT NULL",
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
        for ddl in _UNIQUE_INDEXES:
            await conn.exec_driver_sql(ddl)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _user():
    return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())


async def _seed_mission(db: AsyncSession) -> Mission:
    mission = Mission(title="Savannah", status=MissionStatus.DRAFT, mission_type=MissionType.MAPPING)
    db.add(mission)
    await db.flush()
    return mission


async def _seed_flight(db: AsyncSession, *, name: str, track=None, max_altitude=100.0,
                       duration=600.0, distance=1200.0, aircraft_id=None) -> Flight:
    flight = Flight(
        name=name, drone_model="Mavic 4 Pro", drone_serial=f"SN-{name}",
        duration_secs=duration, total_distance=distance, max_altitude=max_altitude,
        max_speed=15.0, home_lat=44.0, home_lon=-123.0,
        point_count=len(track) if track else 0, source="dji_txt",
        gps_track=track, aircraft_id=aircraft_id,
    )
    db.add(flight)
    await db.flush()
    return flight


# ── RC-1: idempotent single-add ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_single_add_is_idempotent_returns_existing_row(db: AsyncSession):
    from app.routers.missions import add_flight

    mission = await _seed_mission(db)
    flight = await _seed_flight(db, name="F1")
    await db.commit()

    first = await add_flight(
        mission_id=mission.id, data=MissionFlightCreate(flight_id=flight.id, flight_data_cache={}),
        db=db, _user=_user(),
    )
    await db.commit()
    second = await add_flight(
        mission_id=mission.id, data=MissionFlightCreate(flight_id=flight.id, flight_data_cache={}),
        db=db, _user=_user(),
    )
    await db.commit()

    assert first.id == second.id, "re-attach must return the SAME row, not a new one"
    rows = (await db.execute(select(MissionFlight))).scalars().all()
    assert len(rows) == 1, "idempotent single-add must not create a duplicate row"


# ── RC-1: the DB partial unique index blocks duplicate attachments ─────────
@pytest.mark.asyncio
async def test_unique_index_blocks_duplicate_attachment(db: AsyncSession):
    mission = await _seed_mission(db)
    flight = await _seed_flight(db, name="F1")
    await db.commit()

    db.add(MissionFlight(mission_id=mission.id, flight_id=flight.id, flight_data_cache={}))
    await db.flush()
    db.add(MissionFlight(mission_id=mission.id, flight_id=flight.id, flight_data_cache={}))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_unique_index_allows_same_flight_in_different_missions(db: AsyncSession):
    # The guard is per (mission, flight): the same flight may belong to two
    # different missions. Must NOT be blocked.
    m1 = await _seed_mission(db)
    m2 = await _seed_mission(db)
    flight = await _seed_flight(db, name="F1")
    await db.commit()
    db.add(MissionFlight(mission_id=m1.id, flight_id=flight.id, flight_data_cache={}))
    db.add(MissionFlight(mission_id=m2.id, flight_id=flight.id, flight_data_cache={}))
    await db.flush()
    rows = (await db.execute(select(MissionFlight))).scalars().all()
    assert len(rows) == 2


# ── RC-1: report engine dedups defensively ────────────────────────────────
@pytest.mark.asyncio
async def test_report_totals_dedup_over_duplicate_rows():
    # Simulate a mission whose rows contain duplicates (e.g. legacy data that
    # predates the constraint). The report aggregates must dedup by flight key.
    from app.routers.reports import _dedup_flights, _build_flight_summaries, _cache_duration_secs

    fid = uuid.uuid4()
    cache = {"duration_secs": 600.0, "total_distance": 1200.0, "max_altitude": 100.0}
    dup_rows = [
        MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=fid, flight_data_cache=cache),
        MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=fid, flight_data_cache=cache),
        MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=fid, flight_data_cache=cache),
    ]
    unique = _dedup_flights(dup_rows)
    assert len(unique) == 1, "three rows for one flight_id must collapse to one"

    total_duration = sum(_cache_duration_secs(f.flight_data_cache) for f in unique)
    assert total_duration == 600.0, "duration must not be tripled by duplicate rows"

    mission = SimpleNamespace(flights=dup_rows)
    summaries = _build_flight_summaries(mission, {})
    assert len(summaries) == 1, "report shows one flight, not three"


# ── RC-2: area measured on full-resolution geometry ───────────────────────
def _zigzag_track(teeth: int = 5000, amp_deg: float = 1e-4) -> list[dict]:
    """A fine zigzag with > MAX_RENDER_VERTICES_PER_TRACK points. Strided
    decimation drops alternating teeth and collapses the swept shape, shrinking
    the measured coverage — so full-res vs decimated area MUST differ."""
    pts = []
    for i in range(teeth):
        lat = 44.0 + (amp_deg if i % 2 else 0.0)
        lng = -123.0 + i * 1e-5
        pts.append({"lat": lat, "lng": lng})
    return pts


@pytest.mark.asyncio
async def test_area_uses_full_resolution_not_strided_decimation(db: AsyncSession):
    from app.services.map_renderer import (
        MAX_RENDER_VERTICES_PER_TRACK, calculate_area_acres, extract_gps_tracks,
    )
    from app.services.mission_tracks import calculate_mission_area_acres, load_bounded_flight_tracks

    mission = await _seed_mission(db)
    track = _zigzag_track()
    assert len(track) > MAX_RENDER_VERTICES_PER_TRACK
    f1 = await _seed_flight(db, name="N1", track=track)
    db.add(MissionFlight(mission_id=mission.id, flight_id=f1.id, flight_data_cache={"display_name": "N1"}))
    await db.commit()
    mission = (await db.execute(select(Mission).where(Mission.id == mission.id))).scalar_one()

    # MEASUREMENT path (full-res, streamed).
    area_measurement = await calculate_mission_area_acres(db, mission)

    # OLD/render path: decimated to the vertex cap, then area.
    decimated = await load_bounded_flight_tracks(db, mission)
    area_decimated = calculate_area_acres(extract_gps_tracks(decimated))

    # Consistency: measurement equals area computed on the FULL-res track.
    area_fullres_direct = calculate_area_acres(extract_gps_tracks(
        [{"flight_data_cache": {"track": track}}]
    ))
    assert area_measurement > 0
    assert abs(area_measurement - area_fullres_direct) < 1e-6, (
        "measurement path must equal area on the full-resolution track"
    )
    # And it must NOT equal the strided-decimated area (proves RC-2 fix).
    assert abs(area_measurement - area_decimated) > 1e-4, (
        "measurement must differ from the strided-decimated render path"
    )


def test_reports_uses_full_res_measurement_for_area():
    """Source guard: the report endpoint must source area from the full-res
    measurement loader, NOT the strided render path (ADR-0026 RC-2)."""
    from app.routers import reports

    src = inspect.getsource(reports.generate_report)
    assert "calculate_mission_area_acres" in src, "area must use the full-res measurement path"


# ── WRITE: ghost / aborted-launch filtering ───────────────────────────────
@pytest.mark.asyncio
async def test_ghost_flights_excluded_from_altitude_stats(db: AsyncSession):
    from app.routers.reports import _build_flight_summaries, _is_ghost

    real_cache = {"duration_secs": 600.0, "total_distance": 1200.0, "max_altitude": 100.0}
    ghost_cache = {"duration_secs": 7.4, "total_distance": 0.27, "max_altitude": 0.0}
    assert _is_ghost(ghost_cache) is True
    assert _is_ghost(real_cache) is False

    mission = SimpleNamespace(flights=[
        MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=uuid.uuid4(),
                      flight_data_cache=real_cache, aircraft=None),
        MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=uuid.uuid4(),
                      flight_data_cache=ghost_cache, aircraft=None),
    ])
    summaries = _build_flight_summaries(mission, {})
    assert len(summaries) == 2, "both flights are listed (count is honest)"
    ghosts = [s for s in summaries if s.get("aborted")]
    reals = [s for s in summaries if not s.get("aborted")]
    assert len(ghosts) == 1 and len(reals) == 1
    # The ghost contributes NO numeric altitude that could drag the range.
    assert ghosts[0]["max_altitude"] == "N/A — aborted launch"
    assert "0 ft" not in ghosts[0]["max_altitude"] and "0.0 m" not in ghosts[0]["max_altitude"]


# ── WRITE: altitude is unit-correct (metres source, feet conversion) ───────
def test_altitude_formatting_is_unit_correct():
    from app.routers.reports import _format_altitude

    # 190.8 m AGL — the savannah max. The legacy report mislabeled this as
    # "190.8 ft" (understated ~3.28x); the correct rendering is metres + feet.
    assert _format_altitude(190.8) == "190.8 m AGL (626 ft)"
    assert _format_altitude(100.0) == "100.0 m AGL (328 ft)"
    assert _format_altitude(None) == "Unknown"
    # Critically: a metre value is never rendered with a bare "ft" label only.
    out = _format_altitude(190.8)
    assert "m AGL" in out and "ft" in out
