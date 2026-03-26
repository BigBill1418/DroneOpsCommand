import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Group,
  Stack,
  Text,
  Title,
  Badge,
  Loader,
  Center,
  Slider,
  ActionIcon,
  Tooltip,
  Progress,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import {
  IconPlayerPlay,
  IconPlayerPause,
  IconPlayerSkipBack,
  IconPlayerSkipForward,
  IconArrowLeft,
  IconDrone,
  IconArrowUp,
  IconBolt,
  IconClock,
  IconMapPin,
  IconVideo,
} from '@tabler/icons-react';
import { MapContainer, TileLayer, Polyline, CircleMarker, Popup, LayersControl, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import api from '../api/client';
import { renderFlightVideo } from '../components/FlightVideoExporter';
import { cardStyle, monoFont } from '../components/shared/styles';

const heading = { fontFamily: "'Bebas Neue', sans-serif" };

interface GpsPoint {
  lat: number;
  lng: number;
  alt?: number;
  speed?: number;
  timestamp?: string;
}

interface FlightData {
  id: string;
  name: string;
  drone_model: string | null;
  drone_name: string | null;
  start_time: string | null;
  duration_secs: number;
  total_distance: number;
  max_altitude: number;
  max_speed: number;
  home_lat: number | null;
  home_lon: number | null;
  point_count: number;
  gps_track: GpsPoint[] | null;
  telemetry: Record<string, number[]> | null;
}

// Altitude color matching the airspace page
function altColor(altMeters: number): string {
  const ft = altMeters * 3.28084;
  if (ft <= 0) return '#5a6478';
  if (ft < 100) return '#ff6b6b';
  if (ft < 200) return '#ffd43b';
  if (ft < 400) return '#69db7c';
  return '#00d4ff';
}

// Create a drone icon for the animated marker
function createDroneIcon(hdg: number) {
  return L.divIcon({
    className: '',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    html: `<svg viewBox="0 0 24 24" width="28" height="28" style="transform:rotate(${hdg ?? 0}deg);filter:drop-shadow(0 0 4px rgba(0,212,255,0.6))">
      <path d="M12 2 L14 9 L21 11 L14 13 L14 20 L12 18 L10 20 L10 13 L3 11 L10 9 Z"
            fill="#00d4ff" stroke="#ffffff" stroke-width="0.8" opacity="0.95"/>
    </svg>`,
  });
}

// Component to pan the map to follow the drone
function FollowDrone({ lat, lon, follow }: { lat: number; lon: number; follow: boolean }) {
  const map = useMap();
  useEffect(() => {
    if (follow) {
      map.panTo([lat, lon], { animate: true, duration: 0.3 });
    }
  }, [lat, lon, follow, map]);
  return null;
}

// Component to fit map to full track on load
function FitTrack({ points }: { points: GpsPoint[] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length > 0) {
      const bounds = L.latLngBounds(points.map(p => [p.lat, p.lng]));
      map.fitBounds(bounds, { padding: [40, 40] });
    }
  }, [points, map]);
  return null;
}

function formatDuration(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function calcHeading(p1: GpsPoint, p2: GpsPoint): number {
  const dLon = ((p2.lng - p1.lng) * Math.PI) / 180;
  const lat1 = (p1.lat * Math.PI) / 180;
  const lat2 = (p2.lat * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export default function FlightReplay() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isMobile = useMediaQuery('(max-width: 768px)');

  const [flight, setFlight] = useState<FlightData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Playback state
  const [playing, setPlaying] = useState(false);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [followDrone, setFollowDrone] = useState(true);
  const [exporting, setExporting] = useState(false);
  const animRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch flight data
  useEffect(() => {
    if (!id) return;
    setLoading(true);
    api.get(`/flight-library/${id}`)
      .then((r) => {
        setFlight(r.data);
        if (!r.data.gps_track || r.data.gps_track.length < 2) {
          setError('This flight has no GPS track data for replay.');
        }
      })
      .catch(() => setError('Failed to load flight data.'))
      .finally(() => setLoading(false));
  }, [id]);

  const track = useMemo(() => flight?.gps_track ?? [], [flight]);
  const totalPoints = track.length;

  // Compute elapsed time array from timestamps or evenly distribute duration
  const timeOffsets = useMemo(() => {
    if (totalPoints === 0) return [];
    // Try using timestamps
    if (track[0]?.timestamp && track[track.length - 1]?.timestamp) {
      const t0 = new Date(track[0].timestamp).getTime();
      return track.map(p => {
        const t = p.timestamp ? new Date(p.timestamp).getTime() : t0;
        return (t - t0) / 1000;
      });
    }
    // Fall back to evenly spaced based on duration
    const dur = flight?.duration_secs || totalPoints;
    return track.map((_, i) => (i / (totalPoints - 1)) * dur);
  }, [track, totalPoints, flight]);

  const currentPoint = track[currentIdx] ?? null;
  const prevPoint = currentIdx > 0 ? track[currentIdx - 1] : currentPoint;
  const currentHeading = currentPoint && prevPoint ? calcHeading(prevPoint, currentPoint) : 0;
  const currentTime = timeOffsets[currentIdx] ?? 0;
  const totalTime = timeOffsets[totalPoints - 1] ?? 0;
  const progressPct = totalPoints > 1 ? (currentIdx / (totalPoints - 1)) * 100 : 0;

  // Build the trail up to currentIdx with altitude coloring (segments of same color)
  const trailSegments = useMemo(() => {
    if (currentIdx < 1) return [];
    const segments: { positions: [number, number][]; color: string }[] = [];
    let currentColor = altColor(track[0]?.alt ?? 0);
    let currentPositions: [number, number][] = [[track[0].lat, track[0].lng]];

    for (let i = 1; i <= currentIdx; i++) {
      const color = altColor(track[i]?.alt ?? 0);
      if (color !== currentColor) {
        // Close current segment and start new one
        currentPositions.push([track[i].lat, track[i].lng]);
        segments.push({ positions: [...currentPositions], color: currentColor });
        currentColor = color;
        currentPositions = [[track[i].lat, track[i].lng]];
      } else {
        currentPositions.push([track[i].lat, track[i].lng]);
      }
    }
    if (currentPositions.length > 1) {
      segments.push({ positions: currentPositions, color: currentColor });
    }
    return segments;
  }, [track, currentIdx]);

  // Ghost trail (full path, dimmed)
  const ghostTrail = useMemo(() => {
    if (totalPoints < 2) return [];
    return track.map(p => [p.lat, p.lng] as [number, number]);
  }, [track, totalPoints]);

  // Playback engine
  const tick = useCallback(() => {
    setCurrentIdx(prev => {
      if (prev >= totalPoints - 1) {
        setPlaying(false);
        return prev;
      }
      return prev + 1;
    });
  }, [totalPoints]);

  useEffect(() => {
    if (playing) {
      // Base interval: ~60fps feel, but advance faster at higher speeds
      // With timestamps: compute real interval. Without: fixed interval
      const baseMs = totalTime > 0
        ? Math.max(10, (totalTime / totalPoints) * 1000 / speed)
        : Math.max(10, 50 / speed);
      animRef.current = setInterval(tick, baseMs);
    } else if (animRef.current) {
      clearInterval(animRef.current);
      animRef.current = null;
    }
    return () => {
      if (animRef.current) clearInterval(animRef.current);
    };
  }, [playing, speed, tick, totalTime, totalPoints]);

  // Keyboard controls
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault();
        setPlaying(p => !p);
      } else if (e.key === 'ArrowRight') {
        setCurrentIdx(prev => Math.min(prev + Math.ceil(totalPoints / 100), totalPoints - 1));
      } else if (e.key === 'ArrowLeft') {
        setCurrentIdx(prev => Math.max(prev - Math.ceil(totalPoints / 100), 0));
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [totalPoints]);

  if (loading) {
    return (
      <Center h="60vh">
        <Loader color="cyan" size="lg" />
      </Center>
    );
  }

  if (error || !flight || !currentPoint) {
    return (
      <Center h="60vh">
        <Stack align="center" gap="sm">
          <IconDrone size={48} color="#1a1f2e" />
          <Title order={3} c="#5a6478" style={heading}>NO REPLAY DATA</Title>
          <Text c="#5a6478" size="sm" style={monoFont}>{error || 'No GPS track data available.'}</Text>
          <ActionIcon variant="subtle" color="cyan" onClick={() => navigate('/flights')}>
            <IconArrowLeft size={20} />
          </ActionIcon>
        </Stack>
      </Center>
    );
  }

  const altFt = (currentPoint.alt ?? 0) * 3.28084;
  const speedMph = (currentPoint.speed ?? 0) * 2.23694;
  const maxAltFt = flight.max_altitude * 3.28084;
  const maxSpeedMph = flight.max_speed * 2.23694;
  const distanceMiles = flight.total_distance * 0.000621371;

  return (
    <Stack gap="sm">
      {/* Header */}
      <Group justify="space-between" align="center">
        <Group gap="sm">
          <ActionIcon variant="subtle" color="cyan" onClick={() => navigate('/flights')}>
            <IconArrowLeft size={20} />
          </ActionIcon>
          <IconDrone size={24} color="#00d4ff" />
          <Title order={2} c="#e8edf2" style={{ ...heading, letterSpacing: '2px' }}>
            FLIGHT REPLAY
          </Title>
        </Group>
        <Group gap="xs">
          <Button
            leftSection={<IconVideo size={16} />}
            color="cyan"
            variant="light"
            size="xs"
            loading={exporting}
            onClick={() => {
              if (exporting) return;
              setPlaying(false);
              setExporting(true);
              renderFlightVideo(flight, track, timeOffsets).finally(() => setExporting(false));
            }}
            styles={{ root: { ...heading, letterSpacing: '1px' } }}
          >
            {exporting ? 'RENDERING...' : 'EXPORT VIDEO'}
          </Button>
          <Badge color="cyan" variant="light" size="sm" style={monoFont}>
            {flight.drone_name || flight.drone_model || 'Unknown'}
          </Badge>
          <Badge color="gray" variant="light" size="sm" style={monoFont}>
            {flight.point_count} pts
          </Badge>
        </Group>
      </Group>

      {/* Flight name */}
      <Text c="#5a6478" size="xs" style={monoFont} lineClamp={1}>
        {flight.name}
        {flight.start_time && ` — ${new Date(flight.start_time).toLocaleDateString()}`}
      </Text>

      {/* Map + Sidebar layout */}
      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 240px', gap: 10 }}>
        {/* Map */}
        <Card padding={0} radius="md" style={cardStyle}>
          <MapContainer
            center={[track[0].lat, track[0].lng]}
            zoom={15}
            style={{ height: isMobile ? 'calc(100vh - 340px)' : 'calc(100vh - 280px)', minHeight: 300, borderRadius: 8 }}
            scrollWheelZoom={true}
            zoomControl={false}
          >
            <LayersControl position="topright">
              <LayersControl.BaseLayer checked name="Dark">
                <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Satellite">
                <TileLayer
                  url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                  attribution="Esri"
                  maxZoom={19}
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Street">
                <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="OSM" />
              </LayersControl.BaseLayer>
            </LayersControl>
            <FitTrack points={track} />
            <FollowDrone lat={currentPoint.lat} lon={currentPoint.lng} follow={followDrone} />

            {/* Ghost trail (full path, dimmed) */}
            {ghostTrail.length > 1 && (
              <Polyline
                positions={ghostTrail}
                pathOptions={{ color: '#1a1f2e', weight: 2, opacity: 0.5, dashArray: '4 4' }}
              />
            )}

            {/* Colored trail (traveled path) */}
            {trailSegments.map((seg, i) => (
              <Polyline
                key={`trail-${i}`}
                positions={seg.positions}
                pathOptions={{ color: seg.color, weight: 4, opacity: 0.85 }}
              />
            ))}

            {/* Home point */}
            {flight.home_lat && flight.home_lon && (
              <CircleMarker
                center={[flight.home_lat, flight.home_lon]}
                radius={5}
                pathOptions={{ color: '#ff6b6b', fillColor: '#ff6b6b', fillOpacity: 1, weight: 2 }}
              >
                <Popup><div style={{ color: '#000', fontSize: 12 }}><strong>Home Point</strong></div></Popup>
              </CircleMarker>
            )}

            {/* Start marker */}
            <CircleMarker
              center={[track[0].lat, track[0].lng]}
              radius={5}
              pathOptions={{ color: '#2ecc40', fillColor: '#2ecc40', fillOpacity: 1, weight: 2 }}
            >
              <Popup><div style={{ color: '#000', fontSize: 12 }}><strong>Start</strong></div></Popup>
            </CircleMarker>

            {/* End marker (only if we've reached it or replay done) */}
            {currentIdx >= totalPoints - 1 && (
              <CircleMarker
                center={[track[totalPoints - 1].lat, track[totalPoints - 1].lng]}
                radius={5}
                pathOptions={{ color: '#ff6b1a', fillColor: '#ff6b1a', fillOpacity: 1, weight: 2 }}
              >
                <Popup><div style={{ color: '#000', fontSize: 12 }}><strong>End</strong></div></Popup>
              </CircleMarker>
            )}

            {/* Animated drone marker */}
            <DroneMarker lat={currentPoint.lat} lng={currentPoint.lng} heading={currentHeading} />
          </MapContainer>
        </Card>

        {/* Telemetry sidebar (desktop) / below map (mobile) */}
        <Stack gap="xs">
          {/* Current telemetry */}
          <Card padding="sm" radius="md" style={cardStyle}>
            <Text c="#e8edf2" fw={700} style={heading} size="sm" mb="xs">LIVE TELEMETRY</Text>
            <Stack gap={6}>
              <Group justify="space-between">
                <Group gap={4}>
                  <IconArrowUp size={13} color="#69db7c" />
                  <Text size="xs" c="#5a6478" style={monoFont}>ALT</Text>
                </Group>
                <Text size="xs" c="#e8edf2" fw={600} style={monoFont}>
                  {altFt.toFixed(0)} ft
                </Text>
              </Group>
              <Progress value={maxAltFt > 0 ? (altFt / maxAltFt) * 100 : 0} color={altColor(currentPoint.alt ?? 0)} size="xs" />

              <Group justify="space-between">
                <Group gap={4}>
                  <IconBolt size={13} color="#ffd43b" />
                  <Text size="xs" c="#5a6478" style={monoFont}>SPD</Text>
                </Group>
                <Text size="xs" c="#e8edf2" fw={600} style={monoFont}>
                  {speedMph.toFixed(1)} mph
                </Text>
              </Group>
              <Progress value={maxSpeedMph > 0 ? (speedMph / maxSpeedMph) * 100 : 0} color="#ffd43b" size="xs" />

              <Group justify="space-between">
                <Group gap={4}>
                  <IconMapPin size={13} color="#74c0fc" />
                  <Text size="xs" c="#5a6478" style={monoFont}>POS</Text>
                </Group>
                <Text size="xs" c="#e8edf2" style={monoFont}>
                  {currentPoint.lat.toFixed(5)}, {currentPoint.lng.toFixed(5)}
                </Text>
              </Group>

              <Group justify="space-between">
                <Group gap={4}>
                  <IconClock size={13} color="#da77f2" />
                  <Text size="xs" c="#5a6478" style={monoFont}>TIME</Text>
                </Group>
                <Text size="xs" c="#e8edf2" style={monoFont}>
                  {formatDuration(currentTime)} / {formatDuration(totalTime)}
                </Text>
              </Group>
            </Stack>
          </Card>

          {/* Flight stats */}
          <Card padding="sm" radius="md" style={cardStyle}>
            <Text c="#e8edf2" fw={700} style={heading} size="sm" mb="xs">FLIGHT STATS</Text>
            <Stack gap={4}>
              <Group justify="space-between">
                <Text size="xs" c="#5a6478" style={monoFont}>Duration</Text>
                <Text size="xs" c="#e8edf2" style={monoFont}>{formatDuration(flight.duration_secs)}</Text>
              </Group>
              <Group justify="space-between">
                <Text size="xs" c="#5a6478" style={monoFont}>Distance</Text>
                <Text size="xs" c="#e8edf2" style={monoFont}>{distanceMiles.toFixed(2)} mi</Text>
              </Group>
              <Group justify="space-between">
                <Text size="xs" c="#5a6478" style={monoFont}>Max Alt</Text>
                <Text size="xs" c="#e8edf2" style={monoFont}>{maxAltFt.toFixed(0)} ft</Text>
              </Group>
              <Group justify="space-between">
                <Text size="xs" c="#5a6478" style={monoFont}>Max Speed</Text>
                <Text size="xs" c="#e8edf2" style={monoFont}>{maxSpeedMph.toFixed(1)} mph</Text>
              </Group>
            </Stack>
          </Card>

          {/* Altitude legend */}
          <Card padding="sm" radius="md" style={cardStyle}>
            <Text c="#e8edf2" fw={700} style={heading} size="sm" mb="xs">ALTITUDE COLOR</Text>
            <Stack gap={2}>
              {[
                { label: 'Ground', color: '#5a6478' },
                { label: '< 100 ft', color: '#ff6b6b' },
                { label: '100–200 ft', color: '#ffd43b' },
                { label: '200–400 ft', color: '#69db7c' },
                { label: '400+ ft', color: '#00d4ff' },
              ].map((item) => (
                <Group gap="xs" key={item.label}>
                  <div style={{ width: 10, height: 10, borderRadius: '50%', background: item.color }} />
                  <Text size="xs" c="#5a6478" style={monoFont}>{item.label}</Text>
                </Group>
              ))}
            </Stack>
          </Card>
        </Stack>
      </div>

      {/* Playback controls bar */}
      <Card padding="sm" radius="md" style={cardStyle}>
        <Stack gap="xs">
          {/* Scrub bar */}
          <Slider
            value={currentIdx}
            onChange={(v) => { setCurrentIdx(v); setPlaying(false); }}
            min={0}
            max={Math.max(totalPoints - 1, 1)}
            step={1}
            color="cyan"
            size="sm"
            label={null}
            styles={{
              track: { background: '#1a1f2e' },
              bar: { background: 'linear-gradient(90deg, #2ecc40, #00d4ff, #ff6b1a)' },
            }}
          />

          {/* Controls row */}
          <Group justify="space-between" align="center">
            <Group gap="xs">
              {/* Reset */}
              <Tooltip label="Restart (Home)" withArrow>
                <ActionIcon
                  variant="subtle"
                  color="cyan"
                  size="md"
                  onClick={() => { setCurrentIdx(0); setPlaying(false); }}
                >
                  <IconPlayerSkipBack size={18} />
                </ActionIcon>
              </Tooltip>

              {/* Play/Pause */}
              <Tooltip label={playing ? 'Pause (Space)' : 'Play (Space)'} withArrow>
                <ActionIcon
                  variant="filled"
                  color="cyan"
                  size="lg"
                  radius="xl"
                  onClick={() => {
                    if (currentIdx >= totalPoints - 1) setCurrentIdx(0);
                    setPlaying(p => !p);
                  }}
                >
                  {playing ? <IconPlayerPause size={20} /> : <IconPlayerPlay size={20} />}
                </ActionIcon>
              </Tooltip>

              {/* Skip forward */}
              <Tooltip label="Skip +5%" withArrow>
                <ActionIcon
                  variant="subtle"
                  color="cyan"
                  size="md"
                  onClick={() => setCurrentIdx(prev => Math.min(prev + Math.ceil(totalPoints / 20), totalPoints - 1))}
                >
                  <IconPlayerSkipForward size={18} />
                </ActionIcon>
              </Tooltip>
            </Group>

            {/* Speed control */}
            <Group gap="xs">
              {[0.5, 1, 2, 5, 10].map(s => (
                <Badge
                  key={s}
                  color={speed === s ? 'cyan' : 'gray'}
                  variant={speed === s ? 'filled' : 'light'}
                  size="sm"
                  style={{ ...monoFont, cursor: 'pointer' }}
                  onClick={() => setSpeed(s)}
                >
                  {s}x
                </Badge>
              ))}
            </Group>

            {/* Progress text */}
            <Text size="xs" c="#5a6478" style={monoFont}>
              {formatDuration(currentTime)} / {formatDuration(totalTime)}
              {' '}({Math.round(progressPct)}%)
            </Text>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}

// Separate component for the animated drone marker (uses Leaflet directly for performance)
function DroneMarker({ lat, lng, heading }: { lat: number; lng: number; heading: number }) {
  const map = useMap();
  const markerRef = useRef<L.Marker | null>(null);

  useEffect(() => {
    if (!markerRef.current) {
      markerRef.current = L.marker([lat, lng], { icon: createDroneIcon(heading), zIndexOffset: 1000 });
      markerRef.current.addTo(map);
    } else {
      markerRef.current.setLatLng([lat, lng]);
      markerRef.current.setIcon(createDroneIcon(heading));
    }
  }, [lat, lng, heading, map]);

  useEffect(() => {
    return () => {
      if (markerRef.current) {
        markerRef.current.remove();
      }
    };
  }, []);

  return null;
}
