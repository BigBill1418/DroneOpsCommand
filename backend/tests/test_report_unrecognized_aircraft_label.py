"""Regression: an attached flight whose fleet aircraft is UNRECOGNIZED must
never be dropped from — or genericized to "Unknown" in — the client report.

Field defect (2026-07-02, "Springfield Drifters Promo"): Bill flew a DJI Avata 2,
attached the flight to the newest mission, and generated a client report — the
Avata 2 was missing from the narrative. Root cause: the flight carried a
``drone_serial`` but the fleet "DJI Avata 2" aircraft record had no serial, so
the strict serial-match path (ADR-0007) refused a model fallback and left
``flights.aircraft_id`` NULL. The report then read ONLY ``MissionFlight.aircraft``
and substituted the literal string "Unknown", discarding the flight's own parsed
``drone_model`` ("Avata2"). To the client the Avata 2 simply did not exist.

The report layer must be robust to an unrecognized/unlinked aircraft: it already
knows the flight's model from the parsed ``Flight.drone_model`` (native rows) or
the ``flight_data_cache`` snapshot (legacy-ODL rows). The label resolution order
is: linked fleet aircraft's canonical ``model_name`` → live ``drone_name`` →
live ``drone_model`` → cache ``drone_name``/``drone_model``/``aircraft`` →
"Unknown" only as a true last resort.

No altitude-limit / Part-107 logic is touched (ADR-0029 stays intact).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.models.mission import MissionFlight
from app.routers.reports import _build_flight_summaries


def _live_row(**kw) -> SimpleNamespace:
    """Mirror the scalar row `_load_live_flight_metrics` yields per native flight."""
    base = dict(
        duration_secs=805.6, total_distance=2387.4, max_altitude=23.9,
        max_speed=8.0, source="dji_txt", notes=None,
        drone_model=None, drone_name=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_attached_flight_with_unrecognized_aircraft_uses_parsed_model_not_unknown():
    """The Avata-2 defect, on the NATIVE (live-scalar) attach path."""
    fleet_fid = uuid.uuid4()   # linked to a fleet aircraft
    avata_fid = uuid.uuid4()   # attached, but aircraft_id is NULL (unrecognized)

    mission = SimpleNamespace(flights=[
        MissionFlight(
            id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=fleet_fid,
            flight_data_cache={}, aircraft=SimpleNamespace(model_name="DJI Mini 5 Pro"),
        ),
        MissionFlight(
            id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=avata_fid,
            flight_data_cache={}, aircraft=None,
        ),
    ])
    live = {
        fleet_fid: _live_row(drone_model="DJI Mini 5 Pro"),
        avata_fid: _live_row(drone_model="Avata2", drone_name=None),
    }

    summaries = _build_flight_summaries(mission, live)

    # The attached Avata-2 flight is NOT dropped — count is honest.
    assert len(summaries) == 2, "an attached flight must never be silently dropped"
    labels = [s["aircraft"] for s in summaries]
    # Linked fleet aircraft keeps its canonical name.
    assert "DJI Mini 5 Pro" in labels
    # The unrecognized aircraft is labelled by its parsed model, NOT "Unknown".
    assert "Avata2" in labels, f"Avata 2 must appear by model, got {labels}"
    assert "Unknown" not in labels, "no attached flight should collapse to 'Unknown'"


def test_legacy_odl_flight_falls_back_to_cache_model():
    """The cache (legacy-ODL, flight_id IS NULL) attach path is equally robust."""
    mission = SimpleNamespace(flights=[
        MissionFlight(
            id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=None,
            opendronelog_flight_id="odl-123", aircraft=None,
            flight_data_cache={
                "drone_model": "Avata2", "drone_name": None,
                "duration_secs": 805.6, "total_distance": 2387.4, "max_altitude": 23.9,
            },
        ),
    ])
    summaries = _build_flight_summaries(mission, {})
    assert len(summaries) == 1
    assert summaries[0]["aircraft"] == "Avata2"


def test_truly_unknown_flight_still_labels_unknown():
    """When NO model is available anywhere, 'Unknown' remains the honest label."""
    mission = SimpleNamespace(flights=[
        MissionFlight(
            id=uuid.uuid4(), mission_id=uuid.uuid4(), flight_id=None,
            opendronelog_flight_id="odl-x", aircraft=None,
            flight_data_cache={
                "duration_secs": 600.0, "total_distance": 1200.0, "max_altitude": 100.0,
            },
        ),
    ])
    summaries = _build_flight_summaries(mission, {})
    assert len(summaries) == 1
    assert summaries[0]["aircraft"] == "Unknown"
