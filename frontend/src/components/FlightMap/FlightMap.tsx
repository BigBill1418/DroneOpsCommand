import { memo, useEffect, useState, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, Polygon, Popup, useMap, useMapEvents } from 'react-leaflet';
import { Box, Text, Group, Badge, Stack } from '@mantine/core';
import 'leaflet/dist/leaflet.css';

interface GeoJSONFeature {
  type: string;
  geometry: {
    type: string;
    coordinates: number[][] | number[][][] | number[];
  };
  properties: Record<string, any>;
}

interface FlightMapProps {
  geojson: { type: string; features: GeoJSONFeature[] } | null;
  coverage?: { acres: number; square_yards: number | null };
  height?: string;
}

function FitBounds({ features }: { features: GeoJSONFeature[] }) {
  const map = useMap();

  useEffect(() => {
    const allCoords: [number, number][] = [];
    features.forEach((f) => {
      if (f.geometry.type === 'LineString') {
        (f.geometry.coordinates as number[][]).forEach((c) => allCoords.push([c[1], c[0]]));
      } else if (f.geometry.type === 'Point') {
        const c = f.geometry.coordinates as number[];
        allCoords.push([c[1], c[0]]);
      }
    });
    if (allCoords.length > 0) {
      map.fitBounds(allCoords, { padding: [30, 30] });
    }
  }, [features, map]);

  return null;
}

function ScrollHint({ onShow }: { onShow: (show: boolean) => void }) {
  useMapEvents({
    // Show hint when user scrolls without modifier key
  });

  useEffect(() => {
    const el = document.querySelector('.leaflet-container') as HTMLElement | null;
    if (!el) return;

    const handleWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) return; // allow zoom
      onShow(true);
      setTimeout(() => onShow(false), 1500);
    };
    el.addEventListener('wheel', handleWheel, { passive: true });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [onShow]);

  return null;
}

function FlightMap({ geojson, coverage, height = '400px' }: FlightMapProps) {
  const [showScrollHint, setShowScrollHint] = useState(false);
  const handleShowHint = useCallback((show: boolean) => setShowScrollHint(show), []);
  if (!geojson || !geojson.features || geojson.features.length === 0) {
    return (
      <Box
        style={{
          height,
          background: '#050608',
          border: '1px solid #1a1f2e',
          borderRadius: 8,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Text c="#5a6478">No flight path data available. Add flights with GPS data to see the map.</Text>
      </Box>
    );
  }

  const lines = geojson.features.filter((f) => f.geometry.type === 'LineString');
  const points = geojson.features.filter((f) => f.geometry.type === 'Point');
  const polygons = geojson.features.filter((f) => f.geometry.type === 'Polygon');

  // Default center
  let center: [number, number] = [44.05, -123.09]; // Eugene, OR area
  if (lines.length > 0) {
    const firstCoord = (lines[0].geometry.coordinates as number[][])[0];
    center = [firstCoord[1], firstCoord[0]];
  }

  return (
    <Stack gap="xs">
      <Box style={{ height, borderRadius: 8, overflow: 'hidden', border: '2px solid #1a1f2e', position: 'relative' }}>
        <MapContainer
          center={center}
          zoom={14}
          style={{ height: '100%', width: '100%' }}
          attributionControl={false}
          scrollWheelZoom={false}
        >
          <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
          <FitBounds features={geojson.features} />
          <ScrollHint onShow={handleShowHint} />

          {/* Flight paths */}
          {lines.map((feature, i) => (
            <Polyline
              key={`line-${i}`}
              positions={(feature.geometry.coordinates as number[][]).map((c) => [c[1], c[0]] as [number, number])}
              pathOptions={{
                color: feature.properties.color || '#00d4ff',
                weight: 4,
                opacity: 0.9,
              }}
            >
              <Popup>
                <div style={{ fontFamily: "'Rajdhani', sans-serif" }}>
                  <strong>Flight {feature.properties.flight_index + 1}</strong>
                  {feature.properties.aircraft && <div>Aircraft: {feature.properties.aircraft}</div>}
                </div>
              </Popup>
            </Polyline>
          ))}

          {/* Coverage polygon */}
          {polygons.map((feature, i) => (
            <Polygon
              key={`poly-${i}`}
              positions={(feature.geometry.coordinates as number[][][])[0].map((c) => [c[1], c[0]] as [number, number])}
              pathOptions={{
                color: '#00d4ff',
                fillColor: '#00d4ff',
                fillOpacity: 0.1,
                weight: 2,
                dashArray: '5, 5',
              }}
            />
          ))}

          {/* Start/end markers */}
          {points.map((feature, i) => (
            <CircleMarker
              key={`point-${i}`}
              center={[
                (feature.geometry.coordinates as number[])[1],
                (feature.geometry.coordinates as number[])[0],
              ]}
              radius={feature.properties.type === 'start' ? 6 : 4}
              pathOptions={{
                color: feature.properties.color || '#00d4ff',
                fillColor: feature.properties.type === 'start' ? feature.properties.color : '#ffffff',
                fillOpacity: 1,
              }}
            >
              <Popup>{feature.properties.type === 'start' ? 'Start' : 'End'} - Flight {feature.properties.flight_index + 1}</Popup>
            </CircleMarker>
          ))}
        </MapContainer>
        {showScrollHint && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(5,6,8,0.7)', zIndex: 1000, borderRadius: 8, pointerEvents: 'none',
            transition: 'opacity 0.3s',
          }}>
            <Text c="#e8edf2" size="sm" fw={600} style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Use Ctrl + Scroll to zoom the map
            </Text>
          </div>
        )}
      </Box>

      {/* Legend and coverage info */}
      <Group justify="space-between">
        <Group gap="xs">
          {lines.map((feature, i) => (
            <Badge
              key={i}
              variant="dot"
              color={feature.properties.color}
              size="sm"
              styles={{ root: { background: '#0e1117', border: '1px solid #1a1f2e' } }}
            >
              Flight {feature.properties.flight_index + 1}
              {feature.properties.aircraft && ` (${feature.properties.aircraft})`}
            </Badge>
          ))}
        </Group>
        {coverage && (
          <Text
            size="sm"
            c="#00d4ff"
            fw={600}
            style={{ fontFamily: "'Share Tech Mono', monospace" }}
          >
            {coverage.acres >= 1
              ? `${coverage.acres.toFixed(2)} acres`
              : `${coverage.square_yards?.toFixed(0) || 0} sq yards`}
          </Text>
        )}
      </Group>
    </Stack>
  );
}

export default memo(FlightMap);
