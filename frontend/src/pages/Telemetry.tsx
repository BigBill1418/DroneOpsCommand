import { useEffect, useState } from 'react';
import {
  Card,
  Group,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Badge,
  Loader,
  Center,
  Progress,
  RingProgress,
  Tooltip,
} from '@mantine/core';
import {
  IconMap2,
  IconClock,
  IconRuler,
  IconArrowUp,
  IconBolt,
  IconDrone,
  IconCalendar,
  IconMapPin,
  IconWorld,
  IconTimeline,
  IconPlane,
  IconBattery3,
  IconTrophy,
  IconChartBar,
} from '@tabler/icons-react';
import { MapContainer, TileLayer, CircleMarker, Popup, LayersControl, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import api from '../api/client';
import StatCard from '../components/shared/StatCard';
import { cardStyle } from '../components/shared/styles';

interface FlightLocation {
  lat: number;
  lon: number;
  name: string;
  date: string;
  drone: string | null;
}

interface DroneBreakdown {
  drone: string;
  flights: number;
  hours: number;
}

interface MonthData {
  month: string;
  count: number;
}

interface TelemetryStats {
  total_flights: number;
  total_flight_hours: number;
  total_distance_miles: number;
  max_altitude_ft: number;
  max_speed_mph: number;
  unique_drones: number;
  flight_locations: FlightLocation[];
  states_flown: string[];
  time_zones_flown: string[];
  earliest_flight: string | null;
  latest_flight: string | null;
  busiest_month: string | null;
  avg_flight_duration_mins: number;
  total_battery_cycles: number;
  longest_flight_secs: number;
  farthest_flight_miles: number;
  drone_breakdown: DroneBreakdown[];
  source_breakdown: Record<string, number>;
  flights_by_month: MonthData[];
  flights_needing_reprocess: number;
}

const mono = { fontFamily: "'Share Tech Mono', monospace" };
const heading = { fontFamily: "'Bebas Neue', sans-serif" };

// Auto-fit map to US bounds or to data points
function FitToLocations({ locations }: { locations: FlightLocation[] }) {
  const map = useMap();
  useEffect(() => {
    if (locations.length > 0) {
      const lats = locations.map(l => l.lat);
      const lons = locations.map(l => l.lon);
      const pad = 1.5;
      map.fitBounds([
        [Math.min(...lats) - pad, Math.min(...lons) - pad],
        [Math.max(...lats) + pad, Math.max(...lons) + pad],
      ]);
    } else {
      // Default: continental US
      map.fitBounds([[24, -125], [50, -66]]);
    }
  }, [locations, map]);
  return null;
}

// Simple bar chart using pure CSS
function MiniBarChart({ data, maxBars = 24 }: { data: MonthData[]; maxBars?: number }) {
  const sliced = data.slice(-maxBars);
  const max = Math.max(...sliced.map(d => d.count), 1);
  return (
    <div style={{ display: 'flex', alignItems: 'end', gap: 2, height: 120, padding: '0 4px' }}>
      {sliced.map((d, i) => (
        <Tooltip key={i} label={`${d.month}: ${d.count} flights`} withArrow>
          <div
            style={{
              flex: 1,
              minWidth: 6,
              height: `${(d.count / max) * 100}%`,
              background: 'linear-gradient(180deg, #00d4ff 0%, #003d99 100%)',
              borderRadius: '3px 3px 0 0',
              cursor: 'default',
              minHeight: d.count > 0 ? 4 : 0,
            }}
          />
        </Tooltip>
      ))}
    </div>
  );
}

export default function Telemetry() {
  const [stats, setStats] = useState<TelemetryStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/flight-library/telemetry-stats')
      .then(r => setStats(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Center h="60vh">
        <Stack align="center" gap="sm">
          <Loader color="cyan" size="lg" />
          <Text c="#5a6478" style={mono}>Loading telemetry data...</Text>
        </Stack>
      </Center>
    );
  }

  if (!stats || stats.total_flights === 0) {
    return (
      <Center h="60vh">
        <Stack align="center" gap="sm">
          <IconTimeline size={48} color="#1a1f2e" />
          <Title order={3} c="#5a6478" style={heading}>NO FLIGHT DATA</Title>
          <Text c="#5a6478" size="sm" style={mono}>Upload flight logs to see telemetry stats</Text>
        </Stack>
      </Center>
    );
  }

  const totalTimeZones = 6; // Eastern, Central, Mountain, Pacific, Alaska, Hawaii
  const totalStates = 50;

  // Fun stats
  const daysActive = stats.earliest_flight && stats.latest_flight
    ? Math.max(1, Math.ceil((new Date(stats.latest_flight).getTime() - new Date(stats.earliest_flight).getTime()) / 86400000))
    : 0;
  const flightsPerWeek = daysActive > 7 ? (stats.total_flights / (daysActive / 7)).toFixed(1) : stats.total_flights.toString();
  const milesPerFlight = stats.total_flights > 0 ? (stats.total_distance_miles / stats.total_flights).toFixed(1) : '0';

  return (
    <Stack gap="md">
      {/* Header */}
      <Group gap="sm">
        <IconTimeline size={28} color="#00d4ff" />
        <Title order={2} c="#e8edf2" style={{ ...heading, letterSpacing: '2px' }}>
          TELEMETRY DATA
        </Title>
        <Badge color="cyan" variant="light" size="sm" style={mono}>
          {stats.total_flights} flights
        </Badge>
      </Group>

      {/* ═══ PRIMARY STATS ROW ═══ */}
      <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }} spacing="sm">
        <StatCard icon={IconPlane} label="Total Flights" value={stats.total_flights.toLocaleString()} sub={`${flightsPerWeek}/week avg`} />
        <StatCard icon={IconClock} label="Flight Hours" value={stats.total_flight_hours.toFixed(1)} sub={`${stats.avg_flight_duration_mins.toFixed(0)} min avg`} />
        <StatCard icon={IconRuler} label="Total Distance" value={`${stats.total_distance_miles.toFixed(0)} mi`} sub={`${milesPerFlight} mi/flight`} />
        <StatCard icon={IconArrowUp} label="Max Altitude" value={`${stats.max_altitude_ft.toFixed(0)} ft`} color="#ff6b6b" />
        <StatCard icon={IconBolt} label="Top Speed" value={`${stats.max_speed_mph.toFixed(0)} mph`} color="#ffd43b" />
      </SimpleGrid>

      {/* ═══ MAP + GEOGRAPHY SIDEBAR ═══ */}
      <SimpleGrid cols={{ base: 1, md: 3 }} spacing="sm">
        {/* Map — spans 2 cols on desktop */}
        <Card padding={0} radius="md" style={{ ...cardStyle, gridColumn: 'span 2' }}>
          <div style={{ position: 'relative' }}>
            <div style={{
              position: 'absolute', top: 12, left: 12, zIndex: 1000,
              background: 'rgba(5,6,8,0.85)', padding: '6px 12px', borderRadius: 6,
              border: '1px solid #1a1f2e',
            }}>
              <Text size="xs" c="#00d4ff" style={mono}>
                {stats.flight_locations.length} FLIGHT LOCATIONS
              </Text>
            </div>
            <MapContainer
              center={[39, -98]}
              zoom={4}
              style={{ height: 480, borderRadius: 8 }}
              scrollWheelZoom={false}
              zoomControl={false}
            >
              <LayersControl position="topright">
                <LayersControl.BaseLayer checked name="Dark">
                  <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
                </LayersControl.BaseLayer>
                <LayersControl.BaseLayer name="Satellite">
                  <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" attribution="Esri" maxZoom={19} />
                </LayersControl.BaseLayer>
                <LayersControl.BaseLayer name="Street">
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="OSM" />
                </LayersControl.BaseLayer>
              </LayersControl>
              <FitToLocations locations={stats.flight_locations} />
              {stats.flight_locations.map((loc, i) => (
                <CircleMarker
                  key={i}
                  center={[loc.lat, loc.lon]}
                  radius={6}
                  pathOptions={{
                    color: '#00d4ff',
                    fillColor: '#00d4ff',
                    fillOpacity: 0.7,
                    weight: 1,
                  }}
                >
                  <Popup>
                    <div style={{ color: '#000', fontSize: 12 }}>
                      <strong>{loc.name}</strong><br />
                      {loc.date && <span>{new Date(loc.date).toLocaleDateString()}<br /></span>}
                      {loc.drone && <span>{loc.drone}</span>}
                    </div>
                  </Popup>
                </CircleMarker>
              ))}
            </MapContainer>
          </div>
        </Card>

        {/* Geography sidebar */}
        <Stack gap="sm">
          {/* States Flown */}
          <Card padding="md" radius="md" style={cardStyle}>
            <Group gap="xs" mb="xs">
              <IconMapPin size={18} color="#00d4ff" />
              <Text c="#e8edf2" fw={700} style={heading} size="lg">STATES FLOWN</Text>
            </Group>
            <Group gap="xs" mb="sm">
              <Text c="#00d4ff" fw={700} style={{ ...heading, fontSize: 42, lineHeight: 1 }}>
                {stats.states_flown.length}
              </Text>
              <Text c="#5a6478" style={mono} size="sm">/ {totalStates}</Text>
            </Group>
            <Progress
              value={(stats.states_flown.length / totalStates) * 100}
              color="cyan"
              size="sm"
              mb="xs"
              styles={{ root: { background: '#1a1f2e' } }}
            />
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {stats.states_flown.sort().map(s => (
                <Badge key={s} size="xs" variant="light" color="cyan" style={mono}>{s}</Badge>
              ))}
            </div>
          </Card>

          {/* Time Zones */}
          <Card padding="md" radius="md" style={cardStyle}>
            <Group gap="xs" mb="xs">
              <IconWorld size={18} color="#00d4ff" />
              <Text c="#e8edf2" fw={700} style={heading} size="lg">TIME ZONES</Text>
            </Group>
            <Group gap="xs" mb="sm">
              <Text c="#00d4ff" fw={700} style={{ ...heading, fontSize: 42, lineHeight: 1 }}>
                {stats.time_zones_flown.length}
              </Text>
              <Text c="#5a6478" style={mono} size="sm">/ {totalTimeZones}</Text>
            </Group>
            <Progress
              value={(stats.time_zones_flown.length / totalTimeZones) * 100}
              color="cyan"
              size="sm"
              mb="xs"
              styles={{ root: { background: '#1a1f2e' } }}
            />
            <Stack gap={4}>
              {stats.time_zones_flown.sort().map(tz => (
                <Badge key={tz} size="sm" variant="light" color="cyan" style={mono}>{tz}</Badge>
              ))}
            </Stack>
          </Card>

          {/* Date Range */}
          <Card padding="md" radius="md" style={cardStyle}>
            <Group gap="xs" mb="xs">
              <IconCalendar size={18} color="#00d4ff" />
              <Text c="#e8edf2" fw={700} style={heading} size="lg">DATE RANGE</Text>
            </Group>
            <Text c="#e8edf2" size="sm" style={mono}>
              {stats.earliest_flight ? new Date(stats.earliest_flight).toLocaleDateString() : '—'}
              {' → '}
              {stats.latest_flight ? new Date(stats.latest_flight).toLocaleDateString() : '—'}
            </Text>
            <Text c="#5a6478" size="xs" style={mono}>
              {daysActive > 0 ? `${daysActive} days active` : ''}
            </Text>
          </Card>
        </Stack>
      </SimpleGrid>

      {/* ═══ RECORDS & FUN STATS ═══ */}
      <SimpleGrid cols={{ base: 2, sm: 3, md: 6 }} spacing="sm">
        <StatCard icon={IconDrone} label="Unique Drones" value={stats.unique_drones.toString()} />
        <StatCard icon={IconTrophy} label="Longest Flight" value={`${(stats.longest_flight_secs / 60).toFixed(0)} min`} color="#ffd43b" />
        <StatCard icon={IconMap2} label="Farthest Flight" value={`${stats.farthest_flight_miles.toFixed(1)} mi`} color="#69db7c" />
        <StatCard icon={IconBattery3} label="Battery Cycles" value={stats.total_battery_cycles.toLocaleString()} />
        <StatCard icon={IconCalendar} label="Busiest Month" value={stats.busiest_month || '—'} color="#da77f2" />
        <StatCard
          icon={IconChartBar}
          label="Need Reprocess"
          value={stats.flights_needing_reprocess.toString()}
          sub={stats.flights_needing_reprocess > 0 ? 'missing GPS data' : 'all good'}
          color={stats.flights_needing_reprocess > 0 ? '#ff6b6b' : '#69db7c'}
        />
      </SimpleGrid>

      {/* ═══ FLIGHTS BY MONTH + DRONE BREAKDOWN ═══ */}
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
        {/* Flights by Month chart */}
        <Card padding="md" radius="md" style={cardStyle}>
          <Group gap="xs" mb="md">
            <IconChartBar size={18} color="#00d4ff" />
            <Text c="#e8edf2" fw={700} style={heading} size="lg">FLIGHTS BY MONTH</Text>
          </Group>
          {stats.flights_by_month.length > 0 ? (
            <>
              <MiniBarChart data={stats.flights_by_month} />
              <Group justify="space-between" mt={4} px={4}>
                <Text size="10px" c="#5a6478" style={mono}>
                  {stats.flights_by_month[Math.max(0, stats.flights_by_month.length - 24)]?.month}
                </Text>
                <Text size="10px" c="#5a6478" style={mono}>
                  {stats.flights_by_month[stats.flights_by_month.length - 1]?.month}
                </Text>
              </Group>
            </>
          ) : (
            <Text c="#5a6478" style={mono} size="sm">No monthly data yet</Text>
          )}
        </Card>

        {/* Drone Breakdown */}
        <Card padding="md" radius="md" style={cardStyle}>
          <Group gap="xs" mb="md">
            <IconDrone size={18} color="#00d4ff" />
            <Text c="#e8edf2" fw={700} style={heading} size="lg">FLEET BREAKDOWN</Text>
          </Group>
          <Stack gap="xs">
            {stats.drone_breakdown.length > 0 ? (
              stats.drone_breakdown.slice(0, 8).map((d, i) => {
                const pct = stats.total_flights > 0 ? (d.flights / stats.total_flights) * 100 : 0;
                const colors = ['#00d4ff', '#69db7c', '#ffd43b', '#da77f2', '#ff6b6b', '#74c0fc', '#f783ac', '#a9e34b'];
                return (
                  <div key={i}>
                    <Group justify="space-between" mb={2}>
                      <Text size="xs" c="#e8edf2" style={mono}>{d.drone}</Text>
                      <Text size="xs" c="#5a6478" style={mono}>
                        {d.flights} flights · {d.hours.toFixed(1)}h
                      </Text>
                    </Group>
                    <Progress
                      value={pct}
                      color={colors[i % colors.length]}
                      size="xs"
                      styles={{ root: { background: '#1a1f2e' } }}
                    />
                  </div>
                );
              })
            ) : (
              <Text c="#5a6478" style={mono} size="sm">No drone data</Text>
            )}
          </Stack>
        </Card>
      </SimpleGrid>

      {/* ═══ SOURCE BREAKDOWN ═══ */}
      <Card padding="md" radius="md" style={cardStyle}>
        <Group gap="xs" mb="sm">
          <IconTimeline size={18} color="#00d4ff" />
          <Text c="#e8edf2" fw={700} style={heading} size="lg">DATA SOURCES</Text>
        </Group>
        <Group gap="sm">
          {Object.entries(stats.source_breakdown).map(([source, count]) => {
            const labels: Record<string, string> = {
              dji_txt: 'DJI Flight Logs',
              litchi_csv: 'Litchi CSV',
              airdata_csv: 'Airdata CSV',
              manual: 'Manual Entry',
              opendronelog_import: 'OpenDroneLog',
            };
            return (
              <Badge key={source} size="lg" variant="light" color="cyan" style={mono}>
                {labels[source] || source}: {count}
              </Badge>
            );
          })}
        </Group>
      </Card>
    </Stack>
  );
}
