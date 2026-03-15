import math
import os
import uuid

from pyproj import Transformer
from shapely.geometry import MultiPoint, MultiLineString, LineString
from shapely.ops import unary_union

from app.config import settings


# Convert square meters to acres
SQ_METERS_TO_ACRES = 0.000247105


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
                if isinstance(point, dict):
                    lat = point.get("lat", point.get("latitude"))
                    lng = point.get("lng", point.get("lon", point.get("longitude")))
                    if lat is not None and lng is not None:
                        track.append((float(lat), float(lng)))
                elif isinstance(point, (list, tuple)) and len(point) >= 2:
                    track.append((float(point[0]), float(point[1])))

        if track:
            tracks.append(track)

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

    center_lat = sum(p[0] for p in all_points) / len(all_points)
    center_lng = sum(p[1] for p in all_points) / len(all_points)

    # Determine UTM zone
    utm_zone = int((center_lng + 180) / 6) + 1
    hemisphere = "north" if center_lat >= 0 else "south"
    epsg_code = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone

    # Transform to UTM for accurate area calculation
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg_code}", always_xy=True)

    lines = []
    for track in tracks:
        if len(track) < 2:
            continue
        utm_coords = [transformer.transform(lng, lat) for lat, lng in track]
        lines.append(LineString(utm_coords))

    if not lines:
        return 0.0

    multi_line = MultiLineString(lines)
    buffered = multi_line.buffer(buffer_meters)
    area_sq_meters = buffered.area
    return area_sq_meters * SQ_METERS_TO_ACRES


def calculate_convex_hull_geojson(tracks: list[list[tuple[float, float]]]) -> dict | None:
    """Calculate the convex hull of all flight paths as GeoJSON."""
    all_points = [p for track in tracks for p in track]
    if len(all_points) < 3:
        return None

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


def generate_map_geojson(flights: list[dict]) -> dict:
    """Generate GeoJSON FeatureCollection for all flight paths."""
    tracks = extract_gps_tracks(flights)

    features = []
    colors = ["#00d4ff", "#ff6b1a", "#00ff88", "#ff4444", "#ffaa00", "#aa44ff"]

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

    return {"type": "FeatureCollection", "features": features}


def render_static_map(flights: list[dict], width: int = 800, height: int = 600) -> str:
    """Render a static map image with flight paths using folium and save as PNG.

    Returns the file path of the saved image.
    """
    import folium

    tracks = extract_gps_tracks(flights)
    if not tracks:
        return ""

    # Calculate center and bounds
    all_points = [p for track in tracks for p in track]
    center_lat = sum(p[0] for p in all_points) / len(all_points)
    center_lng = sum(p[1] for p in all_points) / len(all_points)

    m = folium.Map(location=[center_lat, center_lng], zoom_start=14, tiles="OpenStreetMap")

    colors = ["#00d4ff", "#ff6b1a", "#00ff88", "#ff4444", "#ffaa00", "#aa44ff"]

    for i, track in enumerate(tracks):
        color = colors[i % len(colors)]
        folium.PolyLine(
            locations=track,
            color=color,
            weight=3,
            opacity=0.8,
        ).add_to(m)

        # Start marker
        if track:
            folium.CircleMarker(
                location=track[0],
                radius=6,
                color=color,
                fill=True,
                popup=f"Flight {i+1} Start",
            ).add_to(m)

    # Fit bounds
    if all_points:
        min_lat = min(p[0] for p in all_points)
        max_lat = max(p[0] for p in all_points)
        min_lng = min(p[1] for p in all_points)
        max_lng = max(p[1] for p in all_points)
        m.fit_bounds([[min_lat, min_lng], [max_lat, max_lng]], padding=[20, 20])

    # Save as HTML (we'll convert to image in PDF generation)
    os.makedirs(settings.reports_dir, exist_ok=True)
    map_filename = f"map_{uuid.uuid4().hex[:8]}.html"
    map_path = os.path.join(settings.reports_dir, map_filename)
    m.save(map_path)

    return map_path
