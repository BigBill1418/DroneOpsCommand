"""ADR-0038 (flight-attach unification, Phase 1) — the aircraft in a report is
resolved from the LIVE flight, never the junction's copied ``aircraft_id``.

Root defect (ADR-0033, the DJI Avata 2): at attach time the junction copied
``Flight.aircraft_id`` into ``mission_flights.aircraft_id``. When the fleet
serial was registered LATER, ``flights.aircraft_id`` updated but the junction
copy kept its stale NULL — so the report showed the wrong (or "Unknown")
aircraft. Phase 1 stops copying the aircraft onto the junction and resolves it
live from ``Flight.aircraft`` via ``flight_id``, so a serial registered after
attach is reflected on the next report with NO detach/re-attach.

Coverage:
  (a) native flight linked AFTER attach now resolves its aircraft in the report
      — the root-fix proof, against a REAL async session so the SQL join is
      exercised, and asserting the junction copy is never written;
  (b) a legacy-ODL attach (``flight_id IS NULL``) still renders from the
      junction / cache — no regression for the class Phase 2 will materialize;
  (c) an unlinked native flight still falls back to its parsed ``drone_model``
      (ADR-0033 behaviour preserved — an attached flight is never dropped).

No altitude-limit / Part-107 logic is touched (ADR-0029 stays intact).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.invoice import Invoice
from app.models.mission import Mission, MissionFlight, MissionImage, MissionStatus, MissionType
from app.routers.reports import (
    _aircraft_label,
    _build_aircraft_cards,
    _build_flight_summaries,
    _load_live_flight_metrics,
    _load_mission_with_flights,
)
from app.schemas.mission import MissionFlightCreate


# ── real-DB fixture (mirrors test_mission_large_flight_hardening.py) ──────────
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
            # ADR-0039: _load_mission_with_flights now eager-loads the invoice.
            Invoice.__table__,
        ):
            await conn.run_sync(table.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _user():
    return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())


async def _resolved_labels_and_cards(db: AsyncSession, mission_id: uuid.UUID):
    """Regenerate the report's aircraft view exactly as the endpoints do.

    The report endpoints run in a FRESH request session, so nothing is served
    from a stale identity map. This test reuses one session, so expire it first
    to force a real re-read (otherwise the pre-created Mission's empty ``flights``
    collection would shadow the committed attach)."""
    db.expire_all()
    mission = await _load_mission_with_flights(db, mission_id)
    live = await _load_live_flight_metrics(db, mission)
    summaries = _build_flight_summaries(mission, live)
    cards = _build_aircraft_cards(mission, live)
    return [s["aircraft"] for s in summaries], cards


# ── (a) THE ROOT FIX — native flight linked AFTER attach ─────────────────────
@pytest.mark.asyncio
async def test_native_flight_linked_after_attach_resolves_live_aircraft(db: AsyncSession):
    from app.routers.missions import add_flight

    # Fleet record exists, but the flight's serial is NOT yet registered on it,
    # so at upload/attach time the flight is unlinked (aircraft_id NULL).
    avata = Aircraft(model_name="DJI Avata 2", manufacturer="DJI")
    db.add(avata)
    await db.flush()
    flight = Flight(
        name="Promo run", drone_model="Avata2", drone_serial="SN-AVATA-NEW",
        duration_secs=805.6, total_distance=2387.4, max_altitude=23.9, max_speed=8.0,
        source="dji_txt", aircraft_id=None,
    )
    db.add(flight)
    mission = Mission(title="Springfield Drifters Promo", status=MissionStatus.DRAFT,
                      mission_type=MissionType.VIDEOGRAPHY)
    db.add(mission)
    await db.flush()
    # Capture ids as plain values — the report reload expires the identity map,
    # so we never touch these ORM instances again (avoids sync lazy-loads).
    avata_id, flight_id, mission_id = avata.id, flight.id, mission.id

    # Attach the native flight (real handler). Phase 1: the junction copy is
    # NOT written — it stays NULL, so nothing can go stale.
    mf = await add_flight(
        mission_id=mission_id,
        data=MissionFlightCreate(flight_id=flight_id, flight_data_cache={"source": "dji_txt"}),
        db=db, _user=_user(),
    )
    await db.commit()
    mf_id = mf.id
    assert mf.aircraft_id is None, "native attach must not copy the aircraft onto the junction"

    # Before the serial is registered: the flight is unlinked, so it is labelled
    # by its parsed model (never dropped, never 'Unknown').
    labels, cards = await _resolved_labels_and_cards(db, mission_id)
    assert labels == ["Avata2"]
    assert [c["model_name"] for c in cards] == ["Avata2"]

    # The fleet serial is registered LATER — flights.aircraft_id now points at
    # the Avata 2 record. NO detach/re-attach; the junction is untouched. Re-fetch
    # the flight fresh (the prior reload expired the setup instance).
    fresh_flight = (await db.execute(select(Flight).where(Flight.id == flight_id))).scalar_one()
    fresh_flight.aircraft_id = avata_id
    await db.commit()

    # The report now resolves the LIVE fleet aircraft — canonical name + card —
    # even though the junction copy is still NULL. This is the ADR-0033 fix.
    labels2, cards2 = await _resolved_labels_and_cards(db, mission_id)
    assert labels2 == ["DJI Avata 2"], f"live aircraft must resolve after late link, got {labels2}"
    assert len(cards2) == 1
    assert cards2[0]["model_name"] == "DJI Avata 2"
    assert cards2[0]["manufacturer"] == "DJI"

    # And the junction copy was NEVER written — proof the label came from live.
    row = (await db.execute(select(MissionFlight).where(MissionFlight.id == mf_id))).scalar_one()
    assert row.aircraft_id is None


# ── (b) legacy-ODL still renders from the junction / cache (no regression) ────
def test_legacy_odl_renders_from_junction_and_cache():
    """flight_id IS NULL rows have no live backing row — the junction copy and
    the cache snapshot stay authoritative (Phase 2 will materialize them)."""
    junction_ac = SimpleNamespace(
        id=uuid.uuid4(), model_name="DJI Mavic 3 Pro", manufacturer="DJI",
        image_filename=None, specs={"weight_g": 958},
    )
    mission = SimpleNamespace(flights=[
        # ODL row WITH a matched fleet aircraft on the junction.
        MissionFlight(
            id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=None,
            opendronelog_flight_id="odl-1", aircraft=junction_ac,
            flight_data_cache={"drone_model": "Mavic 3 Pro",
                               "duration_secs": 600.0, "total_distance": 1200.0, "max_altitude": 100.0},
        ),
        # ODL row with NO junction aircraft — labelled from the cache snapshot.
        MissionFlight(
            id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=None,
            opendronelog_flight_id="odl-2", aircraft=None,
            flight_data_cache={"drone_model": "Avata2",
                               "duration_secs": 805.6, "total_distance": 2387.4, "max_altitude": 23.9},
        ),
    ])
    # Legacy rows never appear in the live-scalar map (they have no flight_id).
    live: dict = {}

    labels = [_aircraft_label(f, live) for f in mission.flights]
    assert labels == ["DJI Mavic 3 Pro", "Avata2"]

    cards = _build_aircraft_cards(mission, live)
    by_name = {c["model_name"]: c for c in cards}
    # Junction-matched ODL row keeps its full fleet card (specs carried through).
    assert by_name["DJI Mavic 3 Pro"]["specs"] == {"weight_g": 958}
    # Cache-only ODL row is a label card (no fleet record → no specs/image).
    assert by_name["Avata2"]["specs"] == {}
    assert by_name["Avata2"]["image_path"] is None


# ── (c) unlinked native flight still falls back to drone_model (ADR-0033) ─────
def test_unlinked_native_flight_falls_back_to_drone_model():
    """A native flight whose live Flight has no fleet aircraft is labelled by its
    parsed drone_model — never dropped, never 'Unknown'."""
    unlinked_fid = uuid.uuid4()
    mission = SimpleNamespace(flights=[
        MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(),
                      flight_id=unlinked_fid, flight_data_cache={}, aircraft=None),
    ])
    # Live row: no fleet aircraft joined (aircraft_* all None), parsed model set.
    live = {
        unlinked_fid: SimpleNamespace(
            id=unlinked_fid, duration_secs=805.6, total_distance=2387.4,
            max_altitude=23.9, max_speed=8.0, source="dji_txt", notes=None,
            drone_model="Avata2", drone_name=None,
            aircraft_id=None, aircraft_model_name=None, aircraft_manufacturer=None,
            aircraft_image_filename=None, aircraft_specs=None,
        )
    }
    assert _aircraft_label(mission.flights[0], live) == "Avata2"

    cards = _build_aircraft_cards(mission, live)
    assert [c["model_name"] for c in cards] == ["Avata2"]
    assert cards[0]["specs"] == {}

    summaries = _build_flight_summaries(mission, live)
    assert [s["aircraft"] for s in summaries] == ["Avata2"]


def test_native_ignores_stale_junction_copy_when_live_says_otherwise():
    """Even if a stale junction copy is present (pre-Phase-1 rows), a native
    flight resolves the LIVE aircraft — the junction copy is never consulted."""
    fid = uuid.uuid4()
    stale = SimpleNamespace(id=uuid.uuid4(), model_name="DJI Mini 2", manufacturer="DJI",
                            image_filename=None, specs={})
    mf = MissionFlight(id=uuid.uuid4(), mission_id=uuid.uuid4(),
                       flight_id=fid, flight_data_cache={}, aircraft=stale)
    live = {
        fid: SimpleNamespace(
            id=fid, duration_secs=600.0, total_distance=1200.0, max_altitude=100.0,
            max_speed=15.0, source="dji_txt", notes=None,
            drone_model="Avata2", drone_name=None,
            aircraft_id=uuid.uuid4(), aircraft_model_name="DJI Avata 2",
            aircraft_manufacturer="DJI", aircraft_image_filename=None, aircraft_specs={},
        )
    }
    assert _aircraft_label(mf, live) == "DJI Avata 2"  # live wins, not "DJI Mini 2"
    cards = _build_aircraft_cards(SimpleNamespace(flights=[mf]), live)
    assert [c["model_name"] for c in cards] == ["DJI Avata 2"]
