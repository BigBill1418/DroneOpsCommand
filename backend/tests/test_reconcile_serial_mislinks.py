"""Serial evidence corrects mislinked flights (2026-10-06).

79 flights carrying the decommissioned Mavic 3 Pro's serial were linked to the
CURRENT Mavic 3 Pro: they were imported before the 'M3P - DECOM' aircraft
existed, and the backfill only ever looked at UNLINKED flights.
"""
import uuid
from types import SimpleNamespace

from app.routers import flight_library

OLD, NEW = uuid.uuid4(), uuid.uuid4()


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, flights):
        self.flights = flights
        self.updates = []

    async def execute(self, stmt):
        if stmt.is_select:
            return _Res(self.flights)
        self.updates.append(stmt)
        return _Res([])


def _flight(serial, aircraft_id):
    return SimpleNamespace(id=uuid.uuid4(), drone_serial=serial, drone_model="DJI Mavic 3 Pro", aircraft_id=aircraft_id)


async def test_serial_match_moves_mislinked_flight_and_its_mission_link(monkeypatch):
    async def match(db, serial, model):
        return SimpleNamespace(id=NEW) if serial == "1581F67QE236L00A0027" else None

    monkeypatch.setattr(flight_library, "_match_fleet_aircraft", match)
    f = _flight("1581F67QE236L00A0027", OLD)
    db = _Session([f])
    assert await flight_library.reconcile_serial_mislinks(db) == 1
    assert f.aircraft_id == NEW
    assert len(db.updates) == 1  # mission_flights follow the flight


async def test_correct_link_and_unmatched_serial_are_left_alone(monkeypatch):
    async def match(db, serial, model):
        return SimpleNamespace(id=OLD) if serial == "A" else None

    monkeypatch.setattr(flight_library, "_match_fleet_aircraft", match)
    ok, unknown, blank = _flight("A", OLD), _flight("ZZZ", OLD), _flight("   ", OLD)
    db = _Session([ok, unknown, blank])
    assert await flight_library.reconcile_serial_mislinks(db) == 0
    assert ok.aircraft_id == unknown.aircraft_id == blank.aircraft_id == OLD
    assert db.updates == []
