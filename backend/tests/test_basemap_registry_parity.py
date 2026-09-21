"""The backend basemap registry must mirror the frontend one (ADR-0046).

``frontend/src/lib/basemaps.ts`` is authoritative — it is what the browser
actually requests. ``app/services/basemap_registry.py`` is a copy that exists
only because the tile-health probe runs in the backend container, and the two
images are built from separate Docker contexts (``./frontend`` and
``./backend``), so no single file is present in both at runtime.

Without this test the copy is free to rot, and a probe watching URLs the app no
longer uses reports green about nothing. Add a provider on one side only and
this goes red.

The frontend tree is present in the repo checkout (where the suite is run per
CLAUDE.md) but not inside the backend image, so the parity assertions skip when
it is genuinely absent rather than failing for the wrong reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.basemap_registry import (
    LAYERS,
    PROBE_X,
    PROBE_Y,
    PROBE_Z,
    probe_url,
    tile_url,
)

_TS_REGISTRY = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "basemaps.ts"

# `url: \`${ESRI}/Canvas/...\`` or `url: 'https://tile.openstreetmap.org/...'`
_URL_RE = re.compile(r"^\s*url:\s*[`'\"](.+?)[`'\"],\s*$", re.M)
_ESRI_CONST_RE = re.compile(r"^const ESRI = '([^']+)';$", re.M)


def _frontend_url_templates() -> set[str]:
    source = _TS_REGISTRY.read_text(encoding="utf-8")
    esri = _ESRI_CONST_RE.search(source)
    assert esri, "frontend registry no longer declares `const ESRI = '...'`"
    return {
        url.replace("${ESRI}", esri.group(1))
        for url in _URL_RE.findall(source)
    }


needs_frontend = pytest.mark.skipif(
    not _TS_REGISTRY.exists(),
    reason="frontend tree not present (running inside the backend image)",
)


@needs_frontend
def test_the_two_registries_hold_the_same_urls():
    frontend = _frontend_url_templates()
    backend = {layer.url_template for layer in LAYERS}

    assert frontend, "regex found no `url:` entries — the parser, not the registry, is broken"
    assert backend - frontend == set(), f"backend-only tile URLs: {backend - frontend}"
    assert frontend - backend == set(), f"frontend-only tile URLs: {frontend - backend}"


@needs_frontend
def test_the_parser_can_actually_fail():
    """Guard the guard: a regex that matched nothing would make the parity test
    above vacuously true in one direction, so prove it finds real entries."""
    assert len(_frontend_url_templates()) == len(LAYERS) >= 5


def test_no_registry_url_points_at_a_watermarked_provider():
    for layer in LAYERS:
        assert "cartocdn" not in layer.url_template
        assert "fastly.net" not in layer.url_template
        assert "{r}" not in layer.url_template
        assert "@2x" not in layer.url_template


def test_esri_and_osm_axis_orders_are_not_transposed():
    """The single easiest way to break this: Esri serves ``{z}/{y}/{x}``,
    OpenStreetMap serves ``{z}/{x}/{y}``. A transposed template returns a real
    tile of somewhere else entirely — HTTP 200, wrong place."""
    by_id = {layer.id: layer for layer in LAYERS}

    esri = by_id["esri_dark_base"]
    assert esri.url_template.endswith("/tile/{z}/{y}/{x}")
    assert tile_url(esri, 10, 163, 373).endswith("/tile/10/373/163")

    osm = by_id["osm_standard"]
    assert osm.url_template.endswith("/{z}/{x}/{y}.png")
    assert tile_url(osm, 10, 163, 373).endswith("/10/163/373.png")


def test_probe_tile_is_the_one_the_baseline_was_captured_on():
    assert (PROBE_Z, PROBE_X, PROBE_Y) == (10, 163, 373)
    assert probe_url({layer.id: layer for layer in LAYERS}["esri_imagery"]).endswith("/tile/10/373/163")


def test_every_layer_declares_a_measured_native_zoom():
    for layer in LAYERS:
        assert 0 < layer.max_native_zoom <= 22, layer.id
