import { useEffect, useState, useRef, useCallback } from 'react';
import {
  Card,
  Group,
  Stack,
  Text,
  Title,
  Badge,
  Loader,
  Center,
  Slider,
  Switch,
  Tooltip,
  ActionIcon,
  Collapse,
  Transition,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconRadar2,
  IconCurrentLocation,
  IconHome,
  IconRefresh,
  IconPlane,
  IconSettings,
  IconChevronUp,
  IconChevronDown,
} from '@tabler/icons-react';
import { MapContainer, TileLayer, CircleMarker, Circle, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import api from '../api/client';
import { cardStyle, monoFont } from '../components/shared/styles';

const heading = { fontFamily: "'Bebas Neue', sans-serif" };

interface Aircraft {
  icao24: string;
  callsign: string;
  origin_country: string;
  lat: number;
  lon: number;
  alt_ft: number;
  heading: number;
  speed_kts: number;
  vertical_rate_fpm: number;
  on_ground: boolean;
  squawk: string;
}

interface AirspaceData {
  aircraft: Aircraft[];
  timestamp: number;
  count: number;
  authenticated: boolean;
  error?: string;
}

// Create a rotated plane icon using a div with SVG
function createPlaneIcon(hdg: number, onGround: boolean) {
  const color = onGround ? '#5a6478' : '#00d4ff';
  return L.divIcon({
    className: '',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    html: `<svg viewBox="0 0 24 24" width="24" height="24" style="transform:rotate(${hdg ?? 0}deg)">
      <path d="M12 2 L14 9 L21 11 L14 13 L14 20 L12 18 L10 20 L10 13 L3 11 L10 9 Z"
            fill="${color}" stroke="#050608" stroke-width="0.5" opacity="0.9"/>
    </svg>`,
  });
}

// Component to re-center map
function RecenterMap({ lat, lon }: { lat: number; lon: number }) {
  const map = useMap();
  useEffect(() => {
    map.setView([lat, lon], map.getZoom());
  }, [lat, lon, map]);
  return null;
}

function altColor(alt: number): string {
  if (alt <= 0) return '#5a6478';
  if (alt < 1000) return '#ff6b6b';
  if (alt < 5000) return '#ffd43b';
  if (alt < 15000) return '#69db7c';
  if (alt < 30000) return '#74c0fc';
  return '#da77f2';
}

export default function Airspace() {
  const [data, setData] = useState<AirspaceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [polling, setPolling] = useState(true);
  const [radiusNm, setRadiusNm] = useState(25);
  const [useGps, setUseGps] = useState(true);
  const [gpsLat, setGpsLat] = useState<number | null>(null);
  const [gpsLon, setGpsLon] = useState<number | null>(null);
  const [homeLat, setHomeLat] = useState<number | null>(null);
  const [homeLon, setHomeLon] = useState<number | null>(null);
  const [gpsError, setGpsError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [controlsOpen, setControlsOpen] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isMobile = useMediaQuery('(max-width: 768px)');

  // Load home location from Settings > Home Location
  useEffect(() => {
    api.get('/settings/weather').then((r) => {
      const lat = parseFloat(r.data.weather_lat);
      const lon = parseFloat(r.data.weather_lon);
      if (!isNaN(lat) && !isNaN(lon) && lat !== 0) {
        setHomeLat(lat);
        setHomeLon(lon);
      }
    }).catch(() => {});
  }, []);

  // GPS tracking
  useEffect(() => {
    if (!useGps) return;
    if (!navigator.geolocation) {
      setGpsError('Geolocation not supported');
      setUseGps(false);
      return;
    }

    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        setGpsLat(pos.coords.latitude);
        setGpsLon(pos.coords.longitude);
        setGpsError(null);
      },
      (err) => {
        setGpsError(err.message);
        // Fall back to home if GPS denied
        if (err.code === 1 && homeLat) setUseGps(false);
      },
      { enableHighAccuracy: true, maximumAge: 30000, timeout: 10000 }
    );

    return () => navigator.geolocation.clearWatch(watchId);
  }, [useGps, homeLat]);

  // Determine active center
  const activeLat = useGps && gpsLat != null ? gpsLat : homeLat;
  const activeLon = useGps && gpsLon != null ? gpsLon : homeLon;
  const hasLocation = activeLat != null && activeLon != null;

  // Fetch aircraft data
  const fetchAircraft = useCallback(async () => {
    if (!hasLocation) return;
    try {
      const r = await api.get('/flight-library/airspace/aircraft', {
        params: { lat: activeLat, lon: activeLon, radius_nm: radiusNm },
      });
      setData(r.data);
      setLastUpdate(new Date());
      if (r.data.error) {
        console.error('Airspace API error:', r.data.error);
      }
    } catch (err) {
      console.error('Airspace fetch failed:', err);
    } finally {
      setLoading(false);
    }
  }, [hasLocation, activeLat, activeLon, radiusNm]);

  // Initial fetch + polling
  useEffect(() => {
    if (!hasLocation) {
      setLoading(false);
      return;
    }
    fetchAircraft();

    if (polling) {
      intervalRef.current = setInterval(fetchAircraft, 10000);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchAircraft, polling, hasLocation]);

  // No location at all
  if (!loading && !hasLocation) {
    return (
      <Center h="60vh">
        <Stack align="center" gap="sm">
          <IconRadar2 size={48} color="#1a1f2e" />
          <Title order={3} c="#5a6478" style={heading}>NO LOCATION SET</Title>
          <Text c="#5a6478" size="sm" style={monoFont}>
            {gpsError
              ? `GPS error: ${gpsError}. Set a home location in Settings > Home Location.`
              : 'Allow GPS access or set a home location in Settings > Home Location.'}
          </Text>
        </Stack>
      </Center>
    );
  }

  const aircraftCount = data?.aircraft?.length ?? 0;
  const airborne = data?.aircraft?.filter(a => !a.on_ground).length ?? 0;
  const onGround = aircraftCount - airborne;
  const radiusMeters = radiusNm * 1852;

  return (
    <Stack gap="md">
      {/* Header */}
      <Group justify="space-between" align="flex-end">
        <Group gap="sm">
          <IconRadar2 size={28} color="#00d4ff" />
          <Title order={2} c="#e8edf2" style={{ ...heading, letterSpacing: '2px' }}>
            AIRSPACE AWARENESS
          </Title>
        </Group>
        <Group gap="xs">
          {data && !data.authenticated && (
            <Tooltip label="Anonymous mode — add OpenSky credentials in Settings for better rate limits" withArrow>
              <Badge color="yellow" variant="light" size="sm" style={monoFont}>ANONYMOUS</Badge>
            </Tooltip>
          )}
          {data?.authenticated && (
            <Badge color="green" variant="light" size="sm" style={monoFont}>AUTHENTICATED</Badge>
          )}
          {lastUpdate && (
            <Text size="xs" c="#5a6478" style={monoFont}>
              Updated {lastUpdate.toLocaleTimeString()}
            </Text>
          )}
        </Group>
      </Group>

      {/* Stats bar */}
      <Group gap="sm">
        <Badge size="lg" color="cyan" variant="light" style={monoFont}>
          {aircraftCount} aircraft
        </Badge>
        <Badge size="lg" color="blue" variant="light" style={monoFont}>
          {airborne} airborne
        </Badge>
        {onGround > 0 && (
          <Badge size="lg" color="gray" variant="light" style={monoFont}>
            {onGround} on ground
          </Badge>
        )}
        {data?.error && (
          <Badge size="lg" color="red" variant="light" style={monoFont}>
            {data.error}
          </Badge>
        )}
      </Group>

      {/* Mobile controls toggle */}
      {isMobile && (
        <Card
          padding="xs"
          radius="md"
          style={{ ...cardStyle, cursor: 'pointer' }}
          onClick={() => setControlsOpen(o => !o)}
        >
          <Group justify="space-between">
            <Group gap="xs">
              <IconSettings size={16} color="#00d4ff" />
              <Text c="#e8edf2" fw={700} style={heading} size="sm">CONTROLS</Text>
              <Badge color="cyan" variant="light" size="xs" style={monoFont}>{radiusNm} NM</Badge>
              {polling && <Badge color="green" variant="dot" size="xs" style={monoFont}>LIVE</Badge>}
            </Group>
            {controlsOpen ? <IconChevronUp size={16} color="#5a6478" /> : <IconChevronDown size={16} color="#5a6478" />}
          </Group>
        </Card>
      )}

      {/* Controls panel — collapsible on mobile, always visible on desktop */}
      {isMobile ? (
        <Collapse in={controlsOpen}>
          <Stack gap="sm">
            {/* Location + Radius + Refresh in a compact row on mobile */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <Card padding="sm" radius="md" style={cardStyle}>
                <Group gap="xs" mb={4}>
                  <IconCurrentLocation size={14} color="#00d4ff" />
                  <Text c="#e8edf2" fw={700} style={heading} size="sm">LOCATION</Text>
                </Group>
                <Switch
                  label="Use GPS"
                  checked={useGps}
                  onChange={(e) => setUseGps(e.currentTarget.checked)}
                  color="cyan"
                  size="xs"
                  styles={{ label: { color: '#e8edf2', ...monoFont, fontSize: 11 } }}
                  mb={4}
                />
                {useGps && gpsLat != null && (
                  <Text size="xs" c="#5a6478" style={monoFont}>{gpsLat.toFixed(4)}, {gpsLon!.toFixed(4)}</Text>
                )}
                {useGps && gpsError && <Text size="xs" c="#ff6b6b" style={monoFont}>{gpsError}</Text>}
                {!useGps && homeLat != null && (
                  <Group gap={4}><IconHome size={11} color="#5a6478" />
                    <Text size="xs" c="#5a6478" style={monoFont}>{homeLat.toFixed(4)}, {homeLon!.toFixed(4)}</Text>
                  </Group>
                )}
                {!useGps && !homeLat && <Text size="xs" c="#ff6b6b" style={monoFont}>Set in Settings</Text>}
              </Card>

              <Card padding="sm" radius="md" style={cardStyle}>
                <Group gap="xs" mb={4}>
                  <IconRefresh size={14} color="#00d4ff" />
                  <Text c="#e8edf2" fw={700} style={heading} size="sm">REFRESH</Text>
                </Group>
                <Switch
                  label="Auto 10s"
                  checked={polling}
                  onChange={(e) => setPolling(e.currentTarget.checked)}
                  color="cyan"
                  size="xs"
                  styles={{ label: { color: '#e8edf2', ...monoFont, fontSize: 11 } }}
                  mb={4}
                />
                <ActionIcon variant="subtle" color="cyan" size="sm" onClick={fetchAircraft}>
                  <IconRefresh size={14} />
                </ActionIcon>
              </Card>
            </div>

            <Card padding="sm" radius="md" style={cardStyle}>
              <Group justify="space-between" mb={4}>
                <Text c="#e8edf2" fw={700} style={heading} size="sm">RADIUS</Text>
                <Badge color="cyan" variant="light" size="xs" style={monoFont}>{radiusNm} NM</Badge>
              </Group>
              <Slider
                value={radiusNm}
                onChange={setRadiusNm}
                min={5} max={100} step={5} color="cyan"
                marks={[{ value: 5, label: '5' }, { value: 25, label: '25' }, { value: 50, label: '50' }, { value: 100, label: '100' }]}
                styles={{ markLabel: { color: '#5a6478', ...monoFont, fontSize: 10 }, track: { background: '#1a1f2e' } }}
              />
            </Card>
          </Stack>
        </Collapse>
      ) : null}

      {/* Map + Desktop sidebar */}
      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 260px', gap: 12 }}>
        {/* Map */}
        <Card padding={0} radius="md" style={cardStyle}>
          {loading ? (
            <Center h={isMobile ? 'calc(100vh - 240px)' : 540}>
              <Loader color="cyan" />
            </Center>
          ) : (
            <MapContainer
              center={[activeLat!, activeLon!]}
              zoom={9}
              style={{ height: isMobile ? 'calc(100vh - 240px)' : 540, minHeight: 320, borderRadius: 8 }}
              scrollWheelZoom={true}
              zoomControl={false}
            >
              <TileLayer
                attribution=""
                url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              />
              <RecenterMap lat={activeLat!} lon={activeLon!} />

              {/* Radius circle */}
              <Circle
                center={[activeLat!, activeLon!]}
                radius={radiusMeters}
                pathOptions={{
                  color: '#00d4ff',
                  fillColor: '#00d4ff',
                  fillOpacity: 0.03,
                  weight: 1,
                  dashArray: '6 4',
                }}
              />

              {/* Center marker (your position) */}
              <CircleMarker
                center={[activeLat!, activeLon!]}
                radius={6}
                pathOptions={{ color: '#ff6b6b', fillColor: '#ff6b6b', fillOpacity: 1, weight: 2 }}
              >
                <Popup>
                  <div style={{ color: '#000', fontSize: 12 }}>
                    <strong>{useGps ? 'GPS Position' : 'Home Location'}</strong><br />
                    {activeLat!.toFixed(4)}, {activeLon!.toFixed(4)}
                  </div>
                </Popup>
              </CircleMarker>

              {/* Aircraft markers */}
              {data?.aircraft?.map((ac) => (
                ac.lat && ac.lon ? (
                  <Marker
                    key={ac.icao24}
                    position={[ac.lat, ac.lon]}
                    icon={createPlaneIcon(ac.heading, ac.on_ground)}
                  >
                    <Popup>
                      <div style={{ color: '#000', fontSize: 12, minWidth: 160 }}>
                        <strong style={{ fontSize: 14 }}>{ac.callsign?.trim() || ac.icao24}</strong>
                        <br />
                        <table style={{ fontSize: 11, borderCollapse: 'collapse' }}>
                          <tbody>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>ICAO</td><td>{ac.icao24}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>Country</td><td>{ac.origin_country}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>Altitude</td><td>{ac.alt_ft != null ? `${Math.round(ac.alt_ft).toLocaleString()} ft` : '—'}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>Speed</td><td>{ac.speed_kts != null ? `${Math.round(ac.speed_kts)} kts` : '—'}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>Heading</td><td>{ac.heading != null ? `${Math.round(ac.heading)}°` : '—'}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>V/S</td><td>{ac.vertical_rate_fpm != null ? `${Math.round(ac.vertical_rate_fpm)} fpm` : '—'}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>Squawk</td><td>{ac.squawk || '—'}</td></tr>
                            <tr><td style={{ paddingRight: 8, color: '#666' }}>Status</td><td>{ac.on_ground ? 'On Ground' : 'Airborne'}</td></tr>
                          </tbody>
                        </table>
                      </div>
                    </Popup>
                  </Marker>
                ) : null
              ))}
            </MapContainer>
          )}
        </Card>

        {/* Desktop controls sidebar */}
        {!isMobile && (
          <Stack gap="sm">
            {/* Location source */}
            <Card padding="md" radius="md" style={cardStyle}>
              <Group gap="xs" mb="xs">
                <IconCurrentLocation size={16} color="#00d4ff" />
                <Text c="#e8edf2" fw={700} style={heading} size="md">LOCATION</Text>
              </Group>
              <Switch
                label="Use GPS"
                checked={useGps}
                onChange={(e) => setUseGps(e.currentTarget.checked)}
                color="cyan"
                size="sm"
                styles={{ label: { color: '#e8edf2', ...monoFont, fontSize: 12 } }}
                mb="xs"
              />
              {useGps && gpsLat != null && (
                <Text size="xs" c="#5a6478" style={monoFont}>
                  {gpsLat.toFixed(4)}, {gpsLon!.toFixed(4)}
                </Text>
              )}
              {useGps && gpsError && (
                <Text size="xs" c="#ff6b6b" style={monoFont}>{gpsError}</Text>
              )}
              {!useGps && homeLat != null && (
                <Group gap={4}>
                  <IconHome size={12} color="#5a6478" />
                  <Text size="xs" c="#5a6478" style={monoFont}>
                    {homeLat.toFixed(4)}, {homeLon!.toFixed(4)}
                  </Text>
                </Group>
              )}
              {!useGps && !homeLat && (
                <Text size="xs" c="#ff6b6b" style={monoFont}>No home set — Settings &gt; Home Location</Text>
              )}
            </Card>

            {/* Radius */}
            <Card padding="md" radius="md" style={cardStyle}>
              <Group justify="space-between" mb="xs">
                <Text c="#e8edf2" fw={700} style={heading} size="md">RADIUS</Text>
                <Badge color="cyan" variant="light" size="sm" style={monoFont}>{radiusNm} NM</Badge>
              </Group>
              <Slider
                value={radiusNm}
                onChange={setRadiusNm}
                min={5}
                max={100}
                step={5}
                color="cyan"
                marks={[
                  { value: 5, label: '5' },
                  { value: 25, label: '25' },
                  { value: 50, label: '50' },
                  { value: 100, label: '100' },
                ]}
                styles={{
                  markLabel: { color: '#5a6478', ...monoFont, fontSize: 10 },
                  track: { background: '#1a1f2e' },
                }}
              />
            </Card>

            {/* Polling control */}
            <Card padding="md" radius="md" style={cardStyle}>
              <Group gap="xs" mb="xs">
                <IconRefresh size={16} color="#00d4ff" />
                <Text c="#e8edf2" fw={700} style={heading} size="md">AUTO-REFRESH</Text>
              </Group>
              <Switch
                label="Poll every 10s"
                checked={polling}
                onChange={(e) => setPolling(e.currentTarget.checked)}
                color="cyan"
                size="sm"
                styles={{ label: { color: '#e8edf2', ...monoFont, fontSize: 12 } }}
              />
              <ActionIcon
                variant="subtle"
                color="cyan"
                size="sm"
                onClick={fetchAircraft}
                mt="xs"
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Card>

            {/* Altitude legend */}
            <Card padding="md" radius="md" style={cardStyle}>
              <Text c="#e8edf2" fw={700} style={heading} size="md" mb="xs">ALTITUDE KEY</Text>
              <Stack gap={2}>
                {[
                  { label: 'Ground', color: '#5a6478' },
                  { label: '< 1,000 ft', color: '#ff6b6b' },
                  { label: '1k–5k ft', color: '#ffd43b' },
                  { label: '5k–15k ft', color: '#69db7c' },
                  { label: '15k–30k ft', color: '#74c0fc' },
                  { label: '30k+ ft', color: '#da77f2' },
                ].map((item) => (
                  <Group gap="xs" key={item.label}>
                    <div style={{ width: 10, height: 10, borderRadius: '50%', background: item.color }} />
                    <Text size="xs" c="#5a6478" style={monoFont}>{item.label}</Text>
                  </Group>
                ))}
              </Stack>
            </Card>
          </Stack>
        )}
      </div>
    </Stack>
  );
}
