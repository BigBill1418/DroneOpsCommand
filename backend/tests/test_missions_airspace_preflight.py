"""Route-level coverage for the mission-creation airspace/LAANC preflight
(ADR-0035).

Exercised through the full FastAPI ASGI stack (``TestClient`` +
``app.dependency_overrides``) — same pattern as
``test_missions_post_rejects_id_in_body.py``. The upstream aviation
feeds (FAA airspace class, AviationWeather TFR/METAR, Open-Meteo) are
patched at their module boundaries so no live network is touched.

Contract under test:
- ``GET /api/missions/airspace-preflight?lat=&lon=`` returns a structured,
  OPERATOR-FACING preflight.
- LAANC likely required for a controlled-airspace coordinate.
- LAANC not required for uncontrolled (Class G).
- Active TFRs are surfaced.
- Any upstream feed failing degrades gracefully — partial data + advisory,
  NEVER a 500 that blocks the operator.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tests.conftest  # noqa: F401


class _FakeSession:
    """No-op async session — the preflight route only touches the DB when
    ``airport`` is omitted (to resolve the configured reference station).
    Every test passes an explicit ``airport`` so ``execute`` is never hit,
    but we provide it to satisfy the ``get_db`` dependency."""

    async def execute(self, _stmt):  # pragma: no cover - not exercised
        raise AssertionError("DB should not be queried when airport is explicit")

    async def close(self):  # pragma: no cover
        pass


def _build_app() -> FastAPI:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    async def _get_db_override():
        yield _FakeSession()

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app


def _patch_feeds(
    monkeypatch,
    *,
    airspace_result: dict,
    tfrs: list[dict],
    weather: dict | None = None,
    metar: dict | None = None,
):
    """Patch all four upstream fetchers used by the preflight route."""
    import app.routers.missions as missions_mod

    async def _fake_airspace(lat, lon):
        return airspace_result

    async def _fake_tfrs(airport):
        return tfrs

    async def _fake_weather(lat, lon):
        return weather if weather is not None else {"temperature_f": 60, "wind_speed_mph": 5}

    async def _fake_metar(airport):
        return metar if metar is not None else {"flight_category": "VFR"}

    monkeypatch.setattr(missions_mod.airspace, "fetch_airspace_class", _fake_airspace)
    monkeypatch.setattr(missions_mod, "_weather_fetch_tfrs", _fake_tfrs)
    monkeypatch.setattr(missions_mod, "_weather_fetch_weather", _fake_weather)
    monkeypatch.setattr(missions_mod, "_weather_fetch_metar", _fake_metar)


# ── LAANC likely required (controlled airspace) ──────────────────────


def test_preflight_controlled_airspace_flags_laanc_required(monkeypatch):
    _patch_feeds(
        monkeypatch,
        airspace_result={
            "airspace_class": "D",
            "controlling_facility": "EUGENE MAHLON SWEET FLD",
            "e_surface": False,
            "error": None,
        },
        tfrs=[{"status": "No active TFRs for area"}],
    )
    client = TestClient(_build_app())
    resp = client.get("/api/missions/airspace-preflight", params={"lat": 44.12, "lon": -123.22, "airport": "KEUG"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["airspace_class"] == "D"
    assert body["laanc_likely_required"] is True
    assert body["controlling_facility"] == "EUGENE MAHLON SWEET FLD"
    assert body["degraded"] is False
    assert any(a["code"] == "laanc_likely_required" for a in body["advisories"])


# ── LAANC not required (uncontrolled airspace) ───────────────────────


def test_preflight_uncontrolled_airspace_no_laanc(monkeypatch):
    _patch_feeds(
        monkeypatch,
        airspace_result={
            "airspace_class": "G",
            "controlling_facility": None,
            "e_surface": False,
            "error": None,
        },
        tfrs=[{"status": "No active TFRs for area"}],
    )
    client = TestClient(_build_app())
    resp = client.get("/api/missions/airspace-preflight", params={"lat": 43.0, "lon": -120.5, "airport": "KEUG"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["airspace_class"] == "G"
    assert body["laanc_likely_required"] is False
    assert any(a["code"] == "laanc_not_required" for a in body["advisories"])


# ── TFR surfacing ────────────────────────────────────────────────────


def test_preflight_surfaces_active_tfr(monkeypatch):
    _patch_feeds(
        monkeypatch,
        airspace_result={
            "airspace_class": "G",
            "controlling_facility": None,
            "e_surface": False,
            "error": None,
        },
        tfrs=[{"notam_id": "4/3621", "type": "TFR", "text": "TEMPORARY FLIGHT RESTRICTION - WILDFIRE"}],
    )
    client = TestClient(_build_app())
    resp = client.get("/api/missions/airspace-preflight", params={"lat": 44.0, "lon": -123.0, "airport": "KEUG"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["tfrs"]) == 1
    assert body["tfrs"][0]["notam_id"] == "4/3621"
    assert any(a["code"] == "active_tfr" for a in body["advisories"])


# ── Graceful degradation — feed error must NOT 500 ───────────────────


def test_preflight_airspace_feed_error_degrades_no_500(monkeypatch):
    """FAA airspace feed down ⇒ 200 with laanc=null + advisory, not a 500."""
    _patch_feeds(
        monkeypatch,
        airspace_result={
            "airspace_class": None,
            "controlling_facility": None,
            "e_surface": False,
            "error": "arcgis unreachable",
        },
        tfrs=[{"status": "No active TFRs for area"}],
    )
    client = TestClient(_build_app())
    resp = client.get("/api/missions/airspace-preflight", params={"lat": 44.12, "lon": -123.22, "airport": "KEUG"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["laanc_likely_required"] is None  # undetermined, never fabricated
    assert body["degraded"] is True
    assert any(a["code"] == "airspace_unavailable" for a in body["advisories"])


def test_preflight_raising_fetcher_still_no_500(monkeypatch):
    """Even if a fetcher *raises* (not just returns an error dict), the
    route must catch it and return a partial preflight, never a 500."""
    import app.routers.missions as missions_mod

    async def _boom_airspace(lat, lon):
        raise RuntimeError("catastrophic feed failure")

    async def _ok_tfrs(airport):
        return [{"status": "No active TFRs for area"}]

    async def _ok_weather(lat, lon):
        return {"temperature_f": 60}

    async def _ok_metar(airport):
        return {"flight_category": "VFR"}

    monkeypatch.setattr(missions_mod.airspace, "fetch_airspace_class", _boom_airspace)
    monkeypatch.setattr(missions_mod, "_weather_fetch_tfrs", _ok_tfrs)
    monkeypatch.setattr(missions_mod, "_weather_fetch_weather", _ok_weather)
    monkeypatch.setattr(missions_mod, "_weather_fetch_metar", _ok_metar)

    client = TestClient(_build_app())
    resp = client.get("/api/missions/airspace-preflight", params={"lat": 44.12, "lon": -123.22, "airport": "KEUG"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is True
    assert body["laanc_likely_required"] is None


# ── Input validation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 100.0, "lon": -123.0, "airport": "KEUG"},   # lat out of range
        {"lat": 44.0, "lon": -200.0, "airport": "KEUG"},    # lon out of range
        {"lon": -123.0, "airport": "KEUG"},                  # lat missing
    ],
)
def test_preflight_rejects_bad_coordinates(monkeypatch, params):
    _patch_feeds(
        monkeypatch,
        airspace_result={"airspace_class": "G", "controlling_facility": None, "e_surface": False, "error": None},
        tfrs=[],
    )
    client = TestClient(_build_app())
    resp = client.get("/api/missions/airspace-preflight", params=params)
    assert resp.status_code == 422, resp.text
