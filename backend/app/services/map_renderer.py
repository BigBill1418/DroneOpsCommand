import logging
import math
import os
import uuid

from pyproj import Transformer
from shapely.geometry import MultiPoint, LineString
from shapely.ops import unary_union

from app.config import settings

logger = logging.getLogger("doc.map_renderer")

# Convert square meters to acres
SQ_METERS_TO_ACRES = 0.000247105

# Douglas-Peucker tolerance (in projected UTM metres) applied to every flight
# track before any geometry work. Survey patterns log a GPS point roughly every
# few centimetres of travel — a single 30-minute mapping flight is ~19k points.
# Buffering a MultiLineString of tens of thousands of near-collinear vertices
# makes GEOS allocate >900 MB and OOM-kills the 1 GiB backend worker mid-request
# (the 2026-06-11 "Generate Report → Cloudflare 520" incident, ADR-0020).
# At 2 m the computed coverage acreage is stable to <1% (convergence verified
# live: 68.51 ac @5 m → 69.11 ac @0.5 m) while collapsing ~33.8k points to
# ~170 vertices, so the buffer stays cheap and bounded.
GEO_SIMPLIFY_TOLERANCE_M = 2.0

# Hard ceiling on the number of vertices fed to the static-map rasteriser, as a
# defence-in-depth backstop so a single pathological track can never blow memory
# even if simplification under-reduces. Tracks above this are decimated by
# strided sampling (keeps endpoints).
MAX_RENDER_VERTICES_PER_TRACK = 2000


def _decimate(track: list, max_points: int) -> list:
    """Strided downsample preserving first and last point. Cheap O(n) backstop."""
    n = len(track)
    if n <= max_points:
        return track
    step = math.ceil(n / max_points)
    sampled = track[::step]
    if sampled[-1] != track[-1]:
        sampled.append(track[-1])
    return sampled


def extract_gps_tracks(flights: list[dict]) -> list[list[tuple[float, float]]]:
    """Extract GPS coordinate tracks from flight data caches.

    Returns list of tracks, each track is a list of (lat, lng) tuples.
    """
    tracks = []
    for flight in flights:
        cache = flight.get("flight_data_cache") or {}
        track = []

        # Try different data formats from OpenDroneLog
        gps_data = cache.get("track", cache.get("gps_data", cache.get("coordinates", [])))

        if isinstance(gps_data, list):
            for point in gps_data:
                try:
                    if isinstance(point, dict):
                        lat = point.get("lat", point.get("latitude"))
                        lng = point.get("lng", point.get("lon", point.get("longitude")))
                        if lat is not None and lng is not None:
                            track.append((float(lat), float(lng)))
                    elif isinstance(point, (list, tuple)) and len(point) >= 2:
                        track.append((float(point[0]), float(point[1])))
                except (ValueError, TypeError) as exc:
                    logger.debug("Skipping invalid GPS point %s: %s", point, exc)
                    continue

        if track:
            tracks.append(track)

    logger.info("Extracted %d GPS tracks (%d total points)",
                len(tracks), sum(len(t) for t in tracks))
    return tracks


def calculate_area_acres(tracks: list[list[tuple[float, float]]], buffer_meters: float = 30.0) -> float:
    """Calculate the approximate area covered by flight paths in acres.

    Uses a buffer around each flight path to approximate camera coverage.
    Default buffer of 30m assumes ~60m swath width at typical survey altitudes.
    """
    if not tracks:
        return 0.0

    # Find center point for UTM projection
    all_points = [p for track in tracks for p in track]
    if not all_points:
        return 0.0

    try:
        center_lat = sum(p[0] for p in all_points) / len(all_points)
        center_lng = sum(p[1] for p in all_points) / len(all_points)

        # Determine UTM zone
        utm_zone = int((center_lng + 180) / 6) + 1
        hemisphere = "north" if center_lat >= 0 else "south"
        epsg_code = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone

        # Transform to UTM for accurate area calculation
        transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg_code}", always_xy=True)

        # Simplify EACH track in projected space before buffering. A raw survey
        # track is tens of thousands of near-collinear points; buffering that
        # MultiLineString makes GEOS allocate >900 MB and OOM-kills the worker
        # (ADR-0020). Douglas-Peucker at GEO_SIMPLIFY_TOLERANCE_M removes the
        # redundant vertices with sub-1% area impact. We buffer each simplified
        # line independently and union the resulting polygons, which keeps every
        # intermediate geometry small and bounded.
        raw_vertices = 0
        kept_vertices = 0
        buffered_polys = []
        for track in tracks:
            if len(track) < 2:
                continue
            raw_vertices += len(track)
            line = LineString([transformer.transform(lng, lat) for lat, lng in track])
            line = line.simplify(GEO_SIMPLIFY_TOLERANCE_M, preserve_topology=False)
            kept_vertices += len(line.coords)
            buffered_polys.append(line.buffer(buffer_meters))

        if not buffered_polys:
            return 0.0

        coverage = unary_union(buffered_polys)
        area_sq_meters = coverage.area
        acres = area_sq_meters * SQ_METERS_TO_ACRES
        logger.info(
            "Area calculation: %.2f acres (%.0f sq m, buffer=%.0fm); "
            "simplified %d→%d vertices @ %.1fm tolerance",
            acres, area_sq_meters, buffer_meters,
            raw_vertices, kept_vertices, GEO_SIMPLIFY_TOLERANCE_M,
        )
        return acres
    except Exception as exc:
        logger.error("Area calculation failed: %s", exc, exc_info=True)
        return 0.0


def calculate_convex_hull_geojson(tracks: list[list[tuple[float, float]]]) -> dict | None:
    """Calculate the convex hull of all flight paths as GeoJSON."""
    all_points = [p for track in tracks for p in track]
    if len(all_points) < 3:
        return None

    try:
        mp = MultiPoint([(lng, lat) for lat, lng in all_points])
        hull = mp.convex_hull

        if hull.is_empty:
            return None

        coords = list(hull.exterior.coords) if hasattr(hull, 'exterior') else []
        return {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[c[0], c[1]] for c in coords]],
            },
            "properties": {"type": "coverage_area"},
        }
    except Exception as exc:
        logger.error("Convex hull calculation failed: %s", exc)
        return None


def generate_map_geojson(flights: list[dict]) -> dict:
    """Generate GeoJSON FeatureCollection for all flight paths."""
    tracks = extract_gps_tracks(flights)

    features = []
    colors = ["#003d99", "#ff6b1a", "#00ff88", "#ff4444", "#ffaa00", "#aa44ff"]

    for i, track in enumerate(tracks):
        flight = flights[i] if i < len(flights) else {}
        aircraft_name = ""
        if flight.get("aircraft"):
            aircraft_name = flight["aircraft"].get("model_name", "")

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lng, lat] for lat, lng in track],
            },
            "properties": {
                "flight_index": i,
                "color": colors[i % len(colors)],
                "aircraft": aircraft_name,
                "opendronelog_id": flight.get("opendronelog_flight_id", ""),
            },
        }
        features.append(feature)

        # Add start/end markers
        if track:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [track[0][1], track[0][0]]},
                "properties": {"type": "start", "flight_index": i, "color": colors[i % len(colors)]},
            })
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [track[-1][1], track[-1][0]]},
                "properties": {"type": "end", "flight_index": i, "color": colors[i % len(colors)]},
            })

    # Add convex hull
    hull_feature = calculate_convex_hull_geojson(tracks)
    if hull_feature:
        features.append(hull_feature)

    logger.info("Generated GeoJSON: %d features from %d tracks", len(features), len(tracks))
    return {"type": "FeatureCollection", "features": features}


def render_static_map(flights: list[dict], width: int = 800, height: int = 600) -> str:
    """Render a static map PNG with flight paths scaled to show only relevant area.

    Uses the staticmap library to generate a real PNG image that can be embedded
    directly in the PDF report. The map is auto-zoomed to fit all flight paths
    with padding so only the relevant area is shown.

    Returns the file path of the saved PNG image.
    """
    from staticmap import StaticMap, Line, CircleMarker

    tracks = extract_gps_tracks(flights)
    if not tracks:
        logger.info("No tracks for static map, skipping render")
        return ""

    colors = ["#003d99", "#ff6b1a", "#00ff88", "#ff4444", "#ffaa00", "#aa44ff"]

    try:
        # Bound the per-tile fetch. staticmap defaults to NO timeout, so a slow
        # or unreachable tile.openstreetmap.org would hang render() — and the
        # report request with it — past Cloudflare's ~100s edge window → 524.
        # 10s/tile is generous for OSM; on failure the map is skipped (caught
        # below), the report still generates without it (ADR-0020).
        m = StaticMap(
            width, height,
            url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            tile_request_timeout=10,
        )

        for i, track in enumerate(tracks):
            color = colors[i % len(colors)]

            # Cap the vertices drawn per track. On an 800x600 PNG the full ~19k
            # raw points are vastly sub-pixel; staticmap still iterates every
            # segment, wasting CPU/memory for no visual gain. Decimating to
            # MAX_RENDER_VERTICES_PER_TRACK keeps the rendered path identical to
            # the eye and bounds the cost (ADR-0020).
            render_track = _decimate(track, MAX_RENDER_VERTICES_PER_TRACK)

            # Draw the flight path line — staticmap uses (lng, lat) order
            if len(render_track) >= 2:
                line_coords = [(lng, lat) for lat, lng in render_track]
                m.add_line(Line(line_coords, color, 3))

            # Start marker (green tint)
            if track:
                start_lat, start_lng = track[0]
                m.add_marker(CircleMarker((start_lng, start_lat), color, 8))

            # End marker (smaller)
            if len(track) > 1:
                end_lat, end_lng = track[-1]
                m.add_marker(CircleMarker((end_lng, end_lat), color, 5))

        # Render the image — staticmap auto-calculates zoom and center to fit all elements
        image = m.render()

        os.makedirs(settings.reports_dir, exist_ok=True)
        map_filename = f"map_{uuid.uuid4().hex[:8]}.png"
        map_path = os.path.join(settings.reports_dir, map_filename)
        image.save(map_path)

        logger.info("Static map rendered: %s (%d tracks)", map_path, len(tracks))
        return map_path
    except Exception as exc:
        logger.error("Static map render failed: %s", exc, exc_info=True)
        return ""
