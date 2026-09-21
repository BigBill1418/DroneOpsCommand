"""Server-side mirror of the frontend basemap registry (ADR-0046).

The authoritative registry is ``frontend/src/lib/basemaps.ts`` — that is what
the browser actually requests. This module exists because the tile-health probe
runs in the backend container, and the frontend and backend images are built
from separate Docker contexts (``./frontend`` and ``./backend``), so no single
file can be present in both at runtime.

The copy is kept honest by ``tests/test_basemap_registry_parity.py``, which
reads the TypeScript registry out of the checkout and fails if the two URL sets
diverge. Add a provider on one side only and the suite goes red.

Probe tiles are a single fixed tile per layer over Eugene, OR — z10, x=163,
y=373 — chosen because every layer here has real (non-filler) content there.
Note the axis order difference: Esri serves ``{z}/{y}/{x}``, OSM ``{z}/{x}/{y}``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fixed probe tile. Eugene, OR at z10 — the app's home area, and the one tile
# the 2026-09-21 provider evaluation fingerprinted by hand.
PROBE_Z = 10
PROBE_X = 163
PROBE_Y = 373

_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services"


@dataclass(frozen=True)
class BasemapLayer:
    """One raster tile layer, mirroring the frontend ``TileSpec``."""

    id: str
    #: Leaflet URL template, verbatim from the frontend registry.
    url_template: str
    #: Deepest zoom with real data (measured, not advertised).
    max_native_zoom: int


LAYERS: tuple[BasemapLayer, ...] = (
    BasemapLayer(
        id="esri_dark_base",
        url_template=f"{_ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",
        max_native_zoom=16,
    ),
    BasemapLayer(
        id="esri_transportation",
        url_template=f"{_ESRI}/Reference/World_Transportation/MapServer/tile/{{z}}/{{y}}/{{x}}",
        max_native_zoom=19,
    ),
    BasemapLayer(
        id="esri_imagery",
        url_template=f"{_ESRI}/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}",
        max_native_zoom=19,
    ),
    BasemapLayer(
        id="esri_boundaries_places",
        url_template=f"{_ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}",
        max_native_zoom=16,
    ),
    BasemapLayer(
        id="osm_standard",
        url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        max_native_zoom=19,
    ),
)

LAYERS_BY_ID: dict[str, BasemapLayer] = {layer.id: layer for layer in LAYERS}


def tile_url(layer: BasemapLayer, z: int, x: int, y: int) -> str:
    """Expand a layer template. Handles both ``{z}/{y}/{x}`` and ``{z}/{x}/{y}``."""
    return layer.url_template.format(z=z, x=x, y=y)


def probe_url(layer: BasemapLayer) -> str:
    """The one fixed tile this layer is fingerprinted on."""
    return tile_url(layer, PROBE_Z, PROBE_X, PROBE_Y)
