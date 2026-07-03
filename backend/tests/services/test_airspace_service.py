"""Unit coverage for the airspace / LAANC-awareness service (ADR-0035).

These are pure-logic + single-feed tests. The route-level ASGI coverage
(mission-creation-time preflight, graceful degradation, TFR surfacing)
lives in ``tests/test_missions_airspace_preflight.py``.

The airspace preflight is OPERATOR-FACING pre-flight awareness only. It
surfaces facts (airspace class, whether LAANC is likely required, nearby
TFRs, weather suitability) — it never renders a compliance verdict and it
is never wired into the client report (see
``tests/test_report_never_references_airspace.py``).
"""

from __future__ import annotations

import pytest

# Force conftest's env stubs to apply before importing app modules.
import tests.conftest  # noqa: F401

from app.services import airspace


# ── normalize_class ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("D", "D"),
        ("d", "D"),
        ("CLASS D", "D"),
        ("Class C", "C"),
        (" b ", "B"),
        ("G", "G"),
        ("E", "E"),
        (None, None),
        ("", None),
        ("unknown", None),
        ("Z", None),
    ],
)
def test_normalize_class(raw, expected):
    assert airspace.normalize_class(raw) == expected


# ── derive_laanc_requirement ─────────────────────────────────────────


def test_laanc_required_for_controlled_class_bcd():
    assert airspace.derive_laanc_requirement("B") is True
    assert airspace.derive_laanc_requirement("C") is True
    assert airspace.derive_laanc_requirement("D") is True


def test_laanc_required_for_class_e_surface_only():
    # Class E at the surface (E2 surface area) is LAANC-eligible controlled
    # airspace; Class E starting above the surface is not.
    assert airspace.derive_laanc_requirement("E", e_surface=True) is True
    assert airspace.derive_laanc_requirement("E", e_surface=False) is False


def test_laanc_not_required_for_uncontrolled_class_g():
    assert airspace.derive_laanc_requirement("G") is False


def test_laanc_unknown_when_class_undetermined():
    # None (not a bool) signals "could not determine" — the caller must
    # surface a verify-manually advisory rather than assert a safe default.
    assert airspace.derive_laanc_requirement(None) is None
    assert airspace.derive_laanc_requirement("") is None


# ── extract_latlon ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "coords,expected",
    [
        ({"lat": 44.05, "lon": -123.09}, (44.05, -123.09)),
        ({"latitude": 44.05, "longitude": -123.09}, (44.05, -123.09)),
        ({"lat": 44.05, "lng": -123.09}, (44.05, -123.09)),
        ({"center": {"lat": 44.05, "lon": -123.09}}, (44.05, -123.09)),
        ({"type": "Point", "coordinates": [-123.09, 44.05]}, (44.05, -123.09)),
        # Polygon → centroid of the (closed) ring.
        (
            {
                "type": "Polygon",
                "coordinates": [[[-123.1, 44.0], [-123.0, 44.0], [-123.0, 44.1], [-123.1, 44.1], [-123.1, 44.0]]],
            },
            (44.05, -123.05),
        ),
        (None, None),
        ({}, None),
        ({"lat": 44.05}, None),  # lon missing
        ({"lat": 999, "lon": -123.0}, None),  # out of range
        ({"lat": "x", "lon": "y"}, None),  # non-numeric
    ],
)
def test_extract_latlon(coords, expected):
    got = airspace.extract_latlon(coords)
    if expected is None:
        assert got is None
    else:
        assert got is not None
        assert got[0] == pytest.approx(expected[0], abs=1e-6)
        assert got[1] == pytest.approx(expected[1], abs=1e-6)


# ── fetch_airspace_class (graceful degradation) ──────────────────────


@pytest.mark.asyncio
async def test_fetch_airspace_class_controlled(monkeypatch):
    """A point inside a Class D polygon resolves to controlled airspace."""

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "features": [
                    {
                        "attributes": {
                            "CLASS": "D",
                            "NAME": "EUGENE MAHLON SWEET FLD",
                            "LOCAL_TYPE": "CLASS_D",
                            "LOWER_VAL": 0,
                        }
                    }
                ]
            }

        def raise_for_status(self):
            return None

    _patch_httpx_get(monkeypatch, _Resp())

    out = await airspace.fetch_airspace_class(44.12, -123.22)
    assert out["airspace_class"] == "D"
    assert out["controlling_facility"] == "EUGENE MAHLON SWEET FLD"
    assert out["error"] is None


@pytest.mark.asyncio
async def test_fetch_airspace_class_uncontrolled_when_no_features(monkeypatch):
    """No intersecting airspace polygon ⇒ Class G (uncontrolled)."""

    class _Resp:
        status_code = 200

        def json(self):
            return {"features": []}

        def raise_for_status(self):
            return None

    _patch_httpx_get(monkeypatch, _Resp())

    out = await airspace.fetch_airspace_class(43.0, -120.0)
    assert out["airspace_class"] == "G"
    assert out["error"] is None


@pytest.mark.asyncio
async def test_fetch_airspace_class_degrades_on_upstream_error(monkeypatch):
    """Upstream failure returns a structured error, NEVER raises."""

    async def _boom(*_a, **_kw):
        raise RuntimeError("arcgis unreachable")

    # Patch the client's get to raise.
    import app.services.airspace as mod

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise RuntimeError("arcgis unreachable")

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)

    out = await airspace.fetch_airspace_class(44.0, -123.0)
    assert out["airspace_class"] is None
    assert out["error"] is not None  # error surfaced, not raised


# ── assemble_preflight (pure) ────────────────────────────────────────


def test_assemble_preflight_controlled_flags_laanc():
    out = airspace.assemble_preflight(
        lat=44.12,
        lon=-123.22,
        airport="KEUG",
        airspace={"airspace_class": "D", "controlling_facility": "EUGENE", "e_surface": False, "error": None},
        tfrs=[{"status": "No active TFRs for area"}],
        weather={"temperature_f": 60, "wind_speed_mph": 6},
        metar={"flight_category": "VFR", "flight_category_desc": "clear for ops"},
    )
    assert out["airspace_class"] == "D"
    assert out["laanc_likely_required"] is True
    assert out["controlling_facility"] == "EUGENE"
    assert out["degraded"] is False
    codes = {a["code"] for a in out["advisories"]}
    assert "laanc_likely_required" in codes


def test_assemble_preflight_uncontrolled_no_laanc():
    out = airspace.assemble_preflight(
        lat=43.0,
        lon=-120.0,
        airport="KEUG",
        airspace={"airspace_class": "G", "controlling_facility": None, "e_surface": False, "error": None},
        tfrs=[{"status": "No active TFRs for area"}],
        weather={"temperature_f": 55, "wind_speed_mph": 4},
        metar={"flight_category": "VFR"},
    )
    assert out["airspace_class"] == "G"
    assert out["laanc_likely_required"] is False
    codes = {a["code"] for a in out["advisories"]}
    assert "laanc_not_required" in codes
    assert "laanc_likely_required" not in codes


def test_assemble_preflight_surfaces_active_tfr():
    out = airspace.assemble_preflight(
        lat=44.12,
        lon=-123.22,
        airport="KEUG",
        airspace={"airspace_class": "G", "controlling_facility": None, "e_surface": False, "error": None},
        tfrs=[{"notam_id": "4/1234", "type": "TFR", "text": "TEMPORARY FLIGHT RESTRICTION"}],
        weather={"temperature_f": 60},
        metar={"flight_category": "VFR"},
    )
    assert len(out["tfrs"]) == 1
    assert out["tfrs"][0]["notam_id"] == "4/1234"
    codes = {a["code"] for a in out["advisories"]}
    assert "active_tfr" in codes
    tfr_adv = next(a for a in out["advisories"] if a["code"] == "active_tfr")
    assert tfr_adv["severity"] in ("caution", "warning")


def test_assemble_preflight_degraded_airspace_is_unknown_not_500():
    out = airspace.assemble_preflight(
        lat=44.12,
        lon=-123.22,
        airport="KEUG",
        airspace={"airspace_class": None, "controlling_facility": None, "e_surface": False, "error": "boom"},
        tfrs=[{"status": "TFR feeds unavailable — check tfr.faa.gov manually"}],
        weather={"error": "open-meteo down"},
        metar={"error": "metar down"},
    )
    # laanc requirement is UNKNOWN (null), never fabricated.
    assert out["laanc_likely_required"] is None
    assert out["degraded"] is True
    codes = {a["code"] for a in out["advisories"]}
    assert "airspace_unavailable" in codes


def test_assemble_preflight_never_contains_compliance_verdict():
    """HARD RULE: preflight surfaces facts, never a compliance judgement.

    No advisory may frame anything as a violation / illegal / non-compliant
    exceedance — that is the operator's determination, not the tool's.
    """
    out = airspace.assemble_preflight(
        lat=44.12,
        lon=-123.22,
        airport="KEUG",
        airspace={"airspace_class": "B", "controlling_facility": "SEATTLE", "e_surface": False, "error": None},
        tfrs=[{"notam_id": "4/9", "type": "TFR", "text": "x"}],
        weather={"wind_speed_mph": 30},
        metar={"flight_category": "LIFR"},
    )
    blob = " ".join(a["message"].lower() for a in out["advisories"])
    for banned in ("violation", "illegal", "non-compliant", "noncompliant", "unlawful", "prohibited by law"):
        assert banned not in blob, f"advisory leaked a compliance verdict: {banned!r}"


# ── helpers ──────────────────────────────────────────────────────────


def _patch_httpx_get(monkeypatch, resp):
    """Patch app.services.airspace.httpx.AsyncClient to yield ``resp`` on GET."""
    import app.services.airspace as mod

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return resp

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
