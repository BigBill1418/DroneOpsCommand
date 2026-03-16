import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  RingProgress,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconClock,
  IconDrone,
  IconArrowsMaximize,
  IconBolt,
  IconMapPin,
  IconPlane,
  IconRefresh,
  IconRuler,
  IconSearch,
  IconRoute,
  IconTimeline,
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';

// --- Formatters ---

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '—';
  const s = Number(seconds);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return sec > 0 ? `${m}m ${sec}s` : `${m}m`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function formatDurationLong(seconds: number): string {
  if (!seconds) return '0s';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

function formatDistance(meters: number | null | undefined): string {
  if (!meters && meters !== 0) return '—';
  const m = Number(meters);
  if (m >= 1000) return `${(m / 1000).toFixed(2)} km`;
  return `${Math.round(m)} m`;
}

function formatAltitude(meters: number | null | undefined): string {
  if (!meters && meters !== 0) return '—';
  return `${Math.round(Number(meters))} m`;
}

function formatSpeed(ms: number | null | undefined): string {
  if (!ms && ms !== 0) return '—';
  return `${(Number(ms) * 3.6).toFixed(1)} km/h`;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return String(dateStr);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return String(dateStr);
  }
}

// --- Field accessors (handle both camelCase ODL and normalized snake_case) ---

function getDurationSecs(f: any): number {
  return Number(f.duration_secs || f.durationSecs || f.duration || f.duration_seconds || 0);
}
function getTotalDistance(f: any): number {
  return Number(f.total_distance || f.totalDistance || f.distance || f.distance_meters || 0);
}
function getMaxAltitude(f: any): number {
  return Number(f.max_altitude || f.maxAltitude || f.max_alt || 0);
}
function getMaxSpeed(f: any): number {
  return Number(f.max_speed || f.maxSpeed || 0);
}
function getDroneModel(f: any): string {
  return f.drone_model || f.droneModel || f.drone || f.aircraft || f.model || '';
}
function getStartTime(f: any): string {
  return f.start_time || f.startTime || f.date || f.created_at || '';
}
function getDisplayName(f: any): string {
  return f.display_name || f.displayName || f.name || f.title || f.file_name || f.fileName || `Flight ${f.id ?? ''}`;
}
function getPointCount(f: any): number {
  return Number(f.point_count || f.pointCount || 0);
}

// --- Stat Card ---

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };
const monoFont = { fontFamily: "'Share Tech Mono', monospace" };

function StatCard({ icon: Icon, label, value, sub, color = '#00d4ff' }: {
  icon: any; label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Group gap="sm" wrap="nowrap">
        <Icon size={22} color={color} style={{ flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
            {label}
          </Text>
          <Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '22px', lineHeight: 1.1 }}>
            {value}
          </Text>
          {sub && (
            <Text size="xs" c="#5a6478" style={monoFont}>{sub}</Text>
          )}
        </div>
      </Group>
    </Card>
  );
}

// --- Drone breakdown mini-chart ---

const DRONE_COLORS = ['#00d4ff', '#ff6b1a', '#2ecc40', '#ff6b6b', '#b57edc', '#ffd43b', '#20c997', '#ff8787'];

function DroneBreakdown({ flights }: { flights: any[] }) {
  const drones = useMemo(() => {
    const map: Record<string, { count: number; duration: number }> = {};
    for (const f of flights) {
      const name = getDroneModel(f) || 'Unknown';
      if (!map[name]) map[name] = { count: 0, duration: 0 };
      map[name].count++;
      map[name].duration += getDurationSecs(f);
    }
    return Object.entries(map)
      .sort((a, b) => b[1].duration - a[1].duration)
      .map(([name, data], i) => ({ name, ...data, color: DRONE_COLORS[i % DRONE_COLORS.length] }));
  }, [flights]);

  if (drones.length === 0) return null;

  const total = drones.reduce((s, d) => s + d.duration, 0);

  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Text size="10px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
        FLIGHT TIME BY DRONE
      </Text>
      <Stack gap={6}>
        {drones.map((d) => {
          const pct = total > 0 ? (d.duration / total) * 100 : 0;
          return (
            <div key={d.name}>
              <Group justify="space-between" mb={2}>
                <Text size="xs" c="#e8edf2" fw={500}>{d.name}</Text>
                <Group gap={6}>
                  <Text size="xs" c="#5a6478" style={monoFont}>{d.count} flights</Text>
                  <Text size="xs" c={d.color} style={monoFont}>{formatDurationLong(d.duration)}</Text>
                </Group>
              </Group>
              <div style={{ background: '#1a1f2e', borderRadius: 3, height: 6, overflow: 'hidden' }}>
                <div style={{ background: d.color, width: `${pct}%`, height: '100%', borderRadius: 3, transition: 'width 0.3s' }} />
              </div>
            </div>
          );
        })}
      </Stack>
    </Card>
  );
}

// --- Top Flights ---

function TopFlights({ flights, label, accessor, formatter }: {
  flights: any[]; label: string; accessor: (f: any) => number; formatter: (v: number) => string;
}) {
  const top = useMemo(() => {
    return [...flights]
      .sort((a, b) => accessor(b) - accessor(a))
      .slice(0, 3)
      .filter((f) => accessor(f) > 0);
  }, [flights]);

  if (top.length === 0) return null;

  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Text size="10px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
        {label}
      </Text>
      <Stack gap={4}>
        {top.map((f, i) => (
          <Group key={f.id ?? i} justify="space-between">
            <Group gap="xs">
              <Badge size="xs" color={i === 0 ? 'yellow' : 'gray'} variant="filled" w={20} style={{ textAlign: 'center' }}>
                {i + 1}
              </Badge>
              <Text size="xs" c="#e8edf2" lineClamp={1}>{getDisplayName(f)}</Text>
            </Group>
            <Text size="xs" c="#00d4ff" style={monoFont} fw={600}>{formatter(accessor(f))}</Text>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}

// === Main Component ===

export default function Flights() {
  const [flights, setFlights] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const loadFlights = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.get('/flights');
      let data: any[] = [];
      if (Array.isArray(resp.data)) {
        data = resp.data;
      } else if (resp.data && typeof resp.data === 'object') {
        data = resp.data.flights || resp.data.data || resp.data.results || resp.data.items || [];
      }
      setFlights(data);
      if (data.length === 0) {
        setError('Connected to OpenDroneLog but no flights found. Upload flight logs to OpenDroneLog first.');
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail || 'Could not fetch flights. Check the OpenDroneLog URL in Settings.';
      setError(detail);
      notifications.show({ title: 'OpenDroneLog', message: detail, color: 'red' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFlights();
  }, []);

  // --- Computed stats ---
  const stats = useMemo(() => {
    const totalFlights = flights.length;
    let totalDuration = 0;
    let totalDistance = 0;
    let maxAlt = 0;
    let maxSpd = 0;
    let totalPoints = 0;

    for (const f of flights) {
      totalDuration += getDurationSecs(f);
      totalDistance += getTotalDistance(f);
      const alt = getMaxAltitude(f);
      if (alt > maxAlt) maxAlt = alt;
      const spd = getMaxSpeed(f);
      if (spd > maxSpd) maxSpd = spd;
      totalPoints += getPointCount(f);
    }

    const avgDuration = totalFlights > 0 ? totalDuration / totalFlights : 0;
    const avgDistance = totalFlights > 0 ? totalDistance / totalFlights : 0;

    return { totalFlights, totalDuration, totalDistance, maxAlt, maxSpd, avgDuration, avgDistance, totalPoints };
  }, [flights]);

  const filtered = useMemo(() => {
    if (!search) return flights;
    const q = search.toLowerCase();
    return flights.filter((f) => {
      const searchable = [
        getDisplayName(f), getDroneModel(f), getStartTime(f),
        f.notes, f.drone_serial, f.droneSerial,
        f.id?.toString(),
      ].filter(Boolean).join(' ').toLowerCase();
      return searchable.includes(q);
    });
  }, [flights, search]);

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>FLIGHTS</Title>
        <Group>
          <Button
            leftSection={<IconRefresh size={16} />}
            variant="light"
            color="cyan"
            onClick={loadFlights}
            loading={loading}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            REFRESH
          </Button>
          <Button
            leftSection={<IconPlane size={16} />}
            color="cyan"
            onClick={() => navigate('/missions/new')}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            NEW MISSION
          </Button>
        </Group>
      </Group>

      {loading ? (
        <Group justify="center" py="xl">
          <Loader color="cyan" size="lg" />
          <Text c="#5a6478">Loading flights from OpenDroneLog...</Text>
        </Group>
      ) : error && flights.length === 0 ? (
        <Card padding="xl" radius="md" style={cardStyle}>
          <Stack align="center" gap="md">
            <IconDrone size={48} color="#5a6478" />
            <Text c="#5a6478" ta="center">{error}</Text>
            <Button variant="light" color="cyan" size="sm" onClick={() => navigate('/settings')}>
              Check Settings
            </Button>
          </Stack>
        </Card>
      ) : (
        <>
          {/* ===== Summary Stats (OpenDroneLog-style) ===== */}
          <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }}>
            <StatCard icon={IconPlane} label="Total Flights" value={stats.totalFlights.toLocaleString()} />
            <StatCard icon={IconRuler} label="Total Distance" value={formatDistance(stats.totalDistance)} />
            <StatCard icon={IconClock} label="Total Flight Time" value={formatDurationLong(stats.totalDuration)} />
            <StatCard icon={IconArrowsMaximize} label="Max Altitude" value={formatAltitude(stats.maxAlt)} color="#ff6b1a" />
            <StatCard icon={IconBolt} label="Max Speed" value={formatSpeed(stats.maxSpd)} color="#ff6b1a" />
          </SimpleGrid>

          <SimpleGrid cols={{ base: 2, sm: 3, md: 3 }}>
            <StatCard
              icon={IconRoute}
              label="Avg Distance / Flight"
              value={formatDistance(stats.avgDistance)}
            />
            <StatCard
              icon={IconTimeline}
              label="Avg Duration / Flight"
              value={formatDurationLong(stats.avgDuration)}
            />
            <StatCard
              icon={IconMapPin}
              label="Total Data Points"
              value={stats.totalPoints > 0 ? stats.totalPoints.toLocaleString() : '—'}
            />
          </SimpleGrid>

          {/* ===== Drone Breakdown & Top Flights ===== */}
          <SimpleGrid cols={{ base: 1, md: 3 }}>
            <DroneBreakdown flights={flights} />
            <TopFlights
              flights={flights}
              label="Longest Flights"
              accessor={getDurationSecs}
              formatter={(v) => formatDurationLong(v)}
            />
            <TopFlights
              flights={flights}
              label="Furthest Flights"
              accessor={getTotalDistance}
              formatter={(v) => formatDistance(v)}
            />
          </SimpleGrid>

          {/* ===== Flight Table ===== */}
          <Card padding="lg" radius="md" style={cardStyle}>
            <Group justify="space-between" mb="md">
              <Text size="sm" c="#5a6478" style={monoFont}>
                {filtered.length} FLIGHT{filtered.length !== 1 ? 'S' : ''}
              </Text>
              <TextInput
                placeholder="Search flights..."
                leftSection={<IconSearch size={14} />}
                size="xs"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                styles={{
                  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', width: 260 },
                }}
              />
            </Group>

            <ScrollArea>
              <Table
                highlightOnHover
                styles={{
                  table: { color: '#e8edf2' },
                  th: {
                    color: '#00d4ff',
                    fontFamily: "'Share Tech Mono', monospace",
                    fontSize: '10px',
                    letterSpacing: '1px',
                    borderBottom: '1px solid #1a1f2e',
                    padding: '8px 10px',
                    whiteSpace: 'nowrap',
                  },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '6px 10px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>#</Table.Th>
                    <Table.Th>NAME</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>DRONE</Table.Th>
                    <Table.Th>DURATION</Table.Th>
                    <Table.Th>DISTANCE</Table.Th>
                    <Table.Th>MAX ALT</Table.Th>
                    <Table.Th>MAX SPEED</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {filtered.map((f, idx) => (
                    <Table.Tr key={f.id ?? idx}>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{f.id ?? idx + 1}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" fw={500} lineClamp={1}>{getDisplayName(f)}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatDate(getStartTime(f))}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#e8edf2">{getDroneModel(f) || '—'}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatDuration(getDurationSecs(f))}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatDistance(getTotalDistance(f))}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatAltitude(getMaxAltitude(f))}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatSpeed(getMaxSpeed(f))}</Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </>
      )}
    </Stack>
  );
}
