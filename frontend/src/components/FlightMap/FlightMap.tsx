import { memo, useEffect, useState } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, Polygon, Popup, useMap } from 'react-leaflet';
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

function FlightMap({ geojson, coverage, height = '400px' }: FlightMapProps) {
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
      <Box style={{ height, borderRadius: 8, overflow: 'hidden', border: '2px solid #1a1f2e' }}>
        <MapContainer
          center={center}
          zoom={14}
          style={{ height: '100%', width: '100%' }}
          attributionControl={false}
        >
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <FitBounds features={geojson.features} />

          {/* Flight paths */}
          {lines.map((feature, i) => (
            <Polyline
              key={`line-${i}`}
              positions={(feature.geometry.coordinates as number[][]).map((c) => [c[1], c[0]] as [number, number])}
              pathOptions={{
                color: feature.properties.color || '#00d4ff',
                weight: 3,
                opacity: 0.8,
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
