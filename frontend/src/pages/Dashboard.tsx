import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Group,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Badge,
  Button,
  Table,
  Loader,
  ScrollArea,
  Tooltip,
  Modal,
  TextInput,
  ActionIcon,
  Progress,
  ThemeIcon,
  RingProgress,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconDrone,
  IconUsers,
  IconFileText,
  IconPlane,
  IconPlus,
  IconWind,
  IconTemperature,
  IconCloud,
  IconEye,
  IconAlertTriangle,
  IconInfoCircle,
  IconClock,
  IconRuler,
  IconArrowUp,
  IconBolt,
  IconTool,
  IconBattery,
  IconBatteryOff,
  IconCalendarDue,
  IconSend,
  IconCheck,
  IconCopy,
  IconMail,
  IconRefresh,
  IconGauge,
  IconDroplet,
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Mission, Customer } from '../api/types';
import StatCard from '../components/shared/StatCard';
import { statusColors, inputStyles } from '../components/shared/styles';

interface WeatherData {
  temperature_f: number | null;
  humidity_pct: number | null;
  condition: string;
  weather_code: number;
  wind_speed_mph: number | null;
  wind_direction_deg: number | null;
  wind_direction: string | null;
  wind_gusts_mph: number | null;
  cloud_cover_pct: number | null;
  visibility_m: number | null;
  pressure_msl_hpa: number | null;
  error?: string;
}

interface NotamEntry {
  id?: string;
  type?: string;
  text?: string;
  effective?: string;
  expires?: string;
  status?: string;
}

interface TfrEntry {
  notam_id?: string;
  type?: string;
  status?: string;
}

interface MetarData {
  station?: string;
  station_name?: string;
  report_time?: string;
  raw_metar?: string;
  flight_category?: string;
  flight_category_color?: string;
  flight_category_desc?: string;
  temp_c?: number;
  dewpoint_c?: number;
  wind_dir_deg?: number;
  wind_speed_kt?: number;
  wind_gust_kt?: number;
  visibility?: string;
  altimeter_hpa?: number;
  clouds?: Array<{ cover: string; base: number }>;
  error?: string;
}

interface NwsAlert {
  event?: string;
  severity?: string;
  headline?: string;
  description?: string;
  expires?: string;
}

interface WeatherResponse {
  location: string;
  airport: string;
  weather: WeatherData;
  metar: MetarData;
  tfrs: TfrEntry[];
  notams: NotamEntry[];
  alerts: NwsAlert[];
  fetched_at: string;
}

interface FlightStats {
  total_flights: number;
  total_duration: number;
  total_distance: number;
  max_altitude: number;
  max_speed: number;
  avg_duration: number;
  avg_distance: number;
  longest_flight: { name: string; duration_secs: number; drone_model: string } | null;
  farthest_flight: { name: string; total_distance: number; drone_model: string } | null;
  recent_flights: Array<{
    id: string;
    name: string;
    start_time: string | null;
    duration_secs: number;
    total_distance: number;
    max_altitude: number;
    drone_model: string;
  }>;
}

interface MaintenanceAlert {
  schedule_id?: string;
  record_id?: string;
  aircraft_id: string;
  aircraft_name?: string;
  maintenance_type: string;
  description: string;
  next_due_date: string | null;
  days_until: number;
  overdue: boolean;
}

interface NextServiceDue {
  aircraft_id: string;
  aircraft_name: string;
  maintenance_type: string;
  description: string | null;
  next_due_date: string;
  days_until: number;
  overdue: boolean;
}

interface BatteryInfo {
  id: string;
  serial: string;
  model: string;
  health_pct: number;
  cycle_count: number;
  status: string;
  last_voltage: number | null;
}

function WindIndicator({ deg }: { deg: number }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" style={{ transform: `rotate(${deg + 180}deg)` }}>
      <path d="M12 2 L8 14 L12 11 L16 14 Z" fill="#00d4ff" />
    </svg>
  );
}

function getWindSeverity(speed: number | null, gusts: number | null): { color: string; label: string } {
  const max = Math.max(speed || 0, gusts || 0);
  if (max >= 25) return { color: '#ff4444', label: 'HAZARDOUS' };
  if (max >= 15) return { color: '#ff6b1a', label: 'CAUTION' };
  return { color: '#00ff88', label: 'FAVORABLE' };
}

function formatDuration(secs: number): string {
  if (!secs) return '0m';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatDistance(meters: number): string {
  if (!meters) return '0';
  const feet = meters * 3.28084;
  if (feet >= 5280) return `${(feet / 5280).toFixed(2)} mi`;
  return `${Math.round(feet)} ft`;
}

const cardBase = { background: '#0e1117', border: '1px solid #1a1f2e' };
const panelStyle = { ...cardBase, display: 'flex' as const, flexDirection: 'column' as const, overflow: 'hidden' as const };
const monoXs = { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' };
const monoSm = { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '12px' };
const bebasFont = { fontFamily: "'Bebas Neue', sans-serif" };

export default function Dashboard() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [flightStats, setFlightStats] = useState<FlightStats | null>(null);
  const [wxData, setWxData] = useState<WeatherResponse | null>(null);
  const [wxLoading, setWxLoading] = useState(true);
  const [wxRefreshing, setWxRefreshing] = useState(false);
  const [wxLastRefresh, setWxLastRefresh] = useState<Date | null>(null);
  const [maintenanceAlerts, setMaintenanceAlerts] = useState<MaintenanceAlert[]>([]);
  const [nextServiceDue, setNextServiceDue] = useState<NextServiceDue | null>(null);
  const [batteries, setBatteries] = useState<BatteryInfo[]>([]);
  const [initiateModalOpen, setInitiateModalOpen] = useState(false);
  const [initiateEmail, setInitiateEmail] = useState('');
  const [initiateLoading, setInitiateLoading] = useState(false);
  const [intakeResult, setIntakeResult] = useState<{ intake_url: string } | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const navigate = useNavigate();

  const fetchWeather = useCallback((isRefresh = false) => {
    if (isRefresh) setWxRefreshing(true);
    api.get('/weather/current')
      .then((r) => { setWxData(r.data); setWxLastRefresh(new Date()); })
      .catch(() => setWxData(null))
      .finally(() => { setWxLoading(false); setWxRefreshing(false); });
  }, []);

  useEffect(() => {
    api.get('/missions').then((r) => setMissions(Array.isArray(r.data) ? r.data : [])).catch(() => setMissions([]));
    api.get('/customers').then((r) => setCustomers(Array.isArray(r.data) ? r.data : [])).catch(() => setCustomers([]));
    api.get('/flight-library/stats/summary').then((r) => setFlightStats(r.data)).catch(() => setFlightStats(null));
    fetchWeather();
    api.get('/maintenance/due').then((r) => setMaintenanceAlerts(Array.isArray(r.data) ? r.data : [])).catch(() => setMaintenanceAlerts([]));
    api.get('/maintenance/next-due').then((r) => setNextServiceDue(r.data)).catch(() => setNextServiceDue(null));
    api.get('/batteries').then((r) => setBatteries(Array.isArray(r.data) ? r.data : [])).catch(() => setBatteries([]));
  }, [fetchWeather]);

  // Auto-refresh weather every 5 minutes
  useEffect(() => {
    const interval = setInterval(() => fetchWeather(true), 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchWeather]);

  const recentMissions = missions.slice(0, 5);
  const draftCount = missions.filter((m) => m.status === 'draft').length;

  const wx = wxData?.weather;
  const windSeverity = wx ? getWindSeverity(wx.wind_speed_mph, wx.wind_gusts_mph) : null;

  // Battery alerts: low health or high cycles
  const batteryAlerts = batteries.filter(
    (b) => b.status === 'active' && (b.health_pct < 40 || b.cycle_count > 200)
  );
  const hasAlerts = maintenanceAlerts.length > 0 || batteryAlerts.length > 0;

  const handleInitiateServices = async () => {
    if (!initiateEmail.trim()) {
      notifications.show({ title: 'Required', message: 'Enter an email address', color: 'red' });
      return;
    }
    setInitiateLoading(true);
    try {
      const r = await api.post('/intake/initiate', { email: initiateEmail.trim() });
      setIntakeResult(r.data);
      notifications.show({ title: 'Link Generated', message: 'Intake form link ready', color: 'cyan' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to generate link', color: 'red' });
    } finally {
      setInitiateLoading(false);
    }
  };

  const handleSendIntakeEmail = async () => {
    if (!intakeResult) return;
    try {
      const customersResp = await api.get('/customers');
      const found = customersResp.data.find((c: Customer) => c.intake_token && intakeResult.intake_url.includes(c.intake_token));
      if (found) {
        await api.post(`/intake/${found.id}/send-email`);
        notifications.show({ title: 'Sent', message: `Intake email sent to ${found.email}`, color: 'green' });
      }
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to send email', color: 'red' });
    }
  };

  const copyIntakeLink = async () => {
    if (!intakeResult) return;
    const text = intakeResult.intake_url;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for non-HTTPS / older browsers
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      notifications.show({ title: 'Copy failed', message: 'Could not copy to clipboard', color: 'red' });
    }
  };

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Group justify="space-between" wrap="wrap" mb="sm">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          DASHBOARD
        </Title>
        <Group gap="xs">
          <Button
            leftSection={<IconSend size={16} />}
            color="cyan"
            variant="light"
            size="sm"
            onClick={() => { setInitiateEmail(''); setIntakeResult(null); setLinkCopied(false); setInitiateModalOpen(true); }}
            styles={{ root: { ...bebasFont, letterSpacing: '1px' } }}
          >
            INITIATE SERVICES
          </Button>
          <Button
            leftSection={<IconPlus size={16} />}
            color="cyan"
            size="sm"
            onClick={() => navigate('/missions/new')}
            styles={{ root: { ...bebasFont, letterSpacing: '1px' } }}
          >
            NEW MISSION
          </Button>
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 5 }} mb="sm" spacing="sm">
        <StatCard icon={IconClock} label="FLIGHT HOURS" value={flightStats ? (flightStats.total_duration / 3600).toFixed(1) : '—'} sub={flightStats ? `${flightStats.total_flights} flights logged` : undefined} color="#00d4ff" />
        <StatCard icon={IconPlane} label="TOTAL FLIGHTS" value={flightStats ? String(flightStats.total_flights) : '—'} color="#00d4ff" />
        <StatCard icon={IconDrone} label="TOTAL MISSIONS" value={String(missions.length)} color="#00d4ff" />
        <StatCard icon={IconFileText} label="DRAFTS PENDING" value={String(draftCount)} color="#ff6b1a" />
        <StatCard icon={IconUsers} label="CUSTOMERS" value={String(customers.length)} color="#00d4ff" />
      </SimpleGrid>

      {/* Main layout: two independent flex columns so cards stack tight */}
      <div style={{
        flex: 1, display: 'flex', gap: '10px', minHeight: 0,
        maxHeight: 'calc(100vh - 220px)',
      }}>

        {/* ═══ LEFT COLUMN — cards stack naturally, no dead space ═══ */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '10px', minHeight: 0 }}>

        {/* ═══ RECENT MISSIONS — compact ═══ */}
        <Card padding="sm" radius="md" style={{ ...panelStyle, maxHeight: '200px', flexShrink: 0 }}>
          <Title order={4} c="#e8edf2" mb={4} style={{ letterSpacing: '1px', fontSize: '14px' }}>
            RECENT MISSIONS
          </Title>
          {recentMissions.length === 0 ? (
            <Text c="#5a6478" ta="center" py="sm" size="sm">
              No missions yet. Create your first mission to get started.
            </Text>
          ) : (
            <ScrollArea style={{ flex: 1 }} type="auto">
              <Table
                highlightOnHover
                styles={{
                  table: { color: '#e8edf2', minWidth: 400 },
                  th: { color: '#00d4ff', ...monoXs, borderBottom: '1px solid #1a1f2e', padding: '4px 8px' },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '4px 8px', fontSize: '12px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>MISSION</Table.Th>
                    <Table.Th>TYPE</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>STATUS</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {recentMissions.slice(0, 4).map((mission) => (
                    <Table.Tr
                      key={mission.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/missions/${mission.id}`)}
                      aria-label={`View mission: ${mission.title}`}
                    >
                      <Table.Td>{mission.title}</Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" tt="capitalize">
                          {(mission.mission_type || '').replace(/_/g, ' ')}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                          {mission.mission_date || '—'}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={statusColors[mission.status] || 'gray'} variant="light" size="xs">
                          {mission.status}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          )}
        </Card>

        {/* ═══ NEXT SERVICE DUE — below recent missions ═══ */}
        <div style={{ flexShrink: 0 }}>
          {nextServiceDue ? (
            <Card
              padding="sm"
              radius="md"
              style={{
                ...panelStyle,
                cursor: 'pointer',
                borderColor: nextServiceDue.overdue
                  ? 'rgba(255, 68, 68, 0.3)'
                  : nextServiceDue.days_until <= 7
                    ? 'rgba(255, 107, 26, 0.3)'
                    : 'rgba(0, 212, 255, 0.2)',
              }}
              onClick={() => navigate('/maintenance')}
            >
              <Group justify="space-between" wrap="nowrap" align="center">
                <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                  <IconCalendarDue size={14} color={
                    nextServiceDue.overdue ? '#ff4444'
                      : nextServiceDue.days_until <= 7 ? '#ff6b1a' : '#00d4ff'
                  } style={{ flexShrink: 0 }} />
                  <Text size="xs" c="#00d4ff" fw={600} lineClamp={1} style={{ ...monoXs, fontSize: '12px' }}>
                    {nextServiceDue.aircraft_name}
                  </Text>
                  {nextServiceDue.next_due_date && (
                    <>
                      <Text size="xs" c="#5a6478">—</Text>
                      <Text size="xs" c="#5a6478" style={{ ...monoXs, fontSize: '11px', flexShrink: 0 }}>
                        {new Date(nextServiceDue.next_due_date).toLocaleDateString('en-US', {
                          month: 'short', day: 'numeric', year: 'numeric',
                        })}
                      </Text>
                    </>
                  )}
                </Group>
                <Badge
                  size="sm"
                  variant="light"
                  color={
                    nextServiceDue.overdue ? 'red'
                      : nextServiceDue.days_until <= 7 ? 'orange'
                        : 'cyan'
                  }
                  styles={{ root: { flexShrink: 0 } }}
                >
                  {nextServiceDue.overdue
                    ? `${Math.abs(nextServiceDue.days_until)}d OVERDUE`
                    : nextServiceDue.days_until === 0
                      ? 'DUE TODAY'
                      : `${nextServiceDue.days_until}d`
                  }
                </Badge>
              </Group>
            </Card>
          ) : (
            <Card padding="sm" radius="md" style={panelStyle}>
              <Group gap="xs">
                <IconTool size={14} color="#00ff88" />
                <Text size="xs" c="#00ff88" fw={600} style={monoXs}>
                  ALL MAINTENANCE CURRENT
                </Text>
              </Group>
            </Card>
          )}
        </div>

        {/* ═══ FLIGHT STATS ═══ */}
        <Card padding="sm" radius="md" style={{ ...panelStyle, flex: 1, minHeight: 0 }}>
          <Title order={4} c="#e8edf2" mb="xs" style={{ letterSpacing: '1px' }}>
            FLIGHT STATISTICS
          </Title>

          {!flightStats ? (
            <Text c="#5a6478" ta="center" py="xl">No flight data available.</Text>
          ) : flightStats.total_flights === 0 ? (
            <Text c="#5a6478" ta="center" py="xl">
              No flights in library. Upload flight logs or import from OpenDroneLog.
            </Text>
          ) : (
            <ScrollArea style={{ flex: 1 }} type="auto">
              <Stack gap="xs">
                {/* Aggregate stats row */}
                <SimpleGrid cols={2} spacing="xs">
                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconClock size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>TOTAL FLIGHT TIME</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '20px', lineHeight: 1.1 }}>
                      {formatDuration(flightStats.total_duration)}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                      avg {formatDuration(flightStats.avg_duration)} / flight
                    </Text>
                  </div>
                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconRuler size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>TOTAL DISTANCE</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '20px', lineHeight: 1.1 }}>
                      {formatDistance(flightStats.total_distance)}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                      avg {formatDistance(flightStats.avg_distance)} / flight
                    </Text>
                  </div>
                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconArrowUp size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>MAX ALTITUDE</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '20px', lineHeight: 1.1 }}>
                      {Math.round(flightStats.max_altitude * 3.28084)} ft
                    </Text>
                  </div>
                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconBolt size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>MAX SPEED</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '20px', lineHeight: 1.1 }}>
                      {(flightStats.max_speed * 2.23694).toFixed(1)} mph
                    </Text>
                  </div>
                </SimpleGrid>

                {/* Record holders */}
                {(flightStats.longest_flight || flightStats.farthest_flight) && (
                  <div style={{ padding: '10px', background: 'rgba(0, 212, 255, 0.04)', borderRadius: '6px', border: '1px solid rgba(0, 212, 255, 0.15)' }}>
                    <Text size="xs" c="#00d4ff" fw={700} mb={6} style={{ ...monoXs, letterSpacing: '2px' }}>
                      FLIGHT RECORDS
                    </Text>
                    <Stack gap={6}>
                      {flightStats.longest_flight && (
                        <Group gap="xs" wrap="nowrap">
                          <ThemeIcon size="sm" variant="light" color="cyan" radius="xl">
                            <IconClock size={12} />
                          </ThemeIcon>
                          <div style={{ minWidth: 0 }}>
                            <Text size="xs" c="#e8edf2" fw={600} lineClamp={1}>
                              LONGEST: {flightStats.longest_flight.name}
                            </Text>
                            <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                              {formatDuration(flightStats.longest_flight.duration_secs)}
                              {flightStats.longest_flight.drone_model ? ` — ${flightStats.longest_flight.drone_model}` : ''}
                            </Text>
                          </div>
                        </Group>
                      )}
                      {flightStats.farthest_flight && (
                        <Group gap="xs" wrap="nowrap">
                          <ThemeIcon size="sm" variant="light" color="cyan" radius="xl">
                            <IconRuler size={12} />
                          </ThemeIcon>
                          <div style={{ minWidth: 0 }}>
                            <Text size="xs" c="#e8edf2" fw={600} lineClamp={1}>
                              FARTHEST: {flightStats.farthest_flight.name}
                            </Text>
                            <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                              {formatDistance(flightStats.farthest_flight.total_distance)}
                              {flightStats.farthest_flight.drone_model ? ` — ${flightStats.farthest_flight.drone_model}` : ''}
                            </Text>
                          </div>
                        </Group>
                      )}
                    </Stack>
                  </div>
                )}

                {/* Recent flights list */}
                {flightStats.recent_flights.length > 0 && (
                  <div>
                    <Text size="xs" c="#5a6478" fw={700} mb={4} style={{ ...monoXs, letterSpacing: '2px' }}>
                      RECENT FLIGHTS
                    </Text>
                    <Stack gap={4}>
                      {flightStats.recent_flights.map((f) => (
                        <div key={f.id} style={{
                          padding: '6px 8px', background: '#050608', borderRadius: '4px',
                          border: '1px solid #1a1f2e', cursor: 'pointer',
                        }}
                          onClick={() => navigate(`/flights?detail=${f.id}`)}
                        >
                          <Group justify="space-between" wrap="nowrap">
                            <Text size="xs" c="#e8edf2" fw={600} lineClamp={1} style={{ flex: 1 }}>
                              {f.name}
                            </Text>
                            <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px', flexShrink: 0 }}>
                              {f.start_time ? new Date(f.start_time).toLocaleDateString() : '—'}
                            </Text>
                          </Group>
                          <Group gap="md" mt={2}>
                            <Text size="xs" c="#00d4ff" style={{ fontSize: '10px' }}>
                              {formatDuration(f.duration_secs)}
                            </Text>
                            <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                              {formatDistance(f.total_distance)}
                            </Text>
                            {f.drone_model && (
                              <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                                {f.drone_model}
                              </Text>
                            )}
                          </Group>
                        </div>
                      ))}
                    </Stack>
                  </div>
                )}
              </Stack>
            </ScrollArea>
          )}
        </Card>

        </div>{/* end left column */}

        {/* ═══ RIGHT COLUMN — weather + equipment ═══ */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '10px', minHeight: 0 }}>

        {/* ═══ FLIGHT CONDITIONS — live weather ═══ */}
        <Card padding="sm" radius="md" style={{ ...panelStyle, flex: 1, minHeight: 0 }}>
          <Group justify="space-between" mb="xs">
            <Group gap="xs">
              <Title order={4} c="#e8edf2" style={{ letterSpacing: '1px' }}>
                FLIGHT CONDITIONS
              </Title>
              {wxData && (
                <Text size="xs" c="#5a6478" style={{ ...monoXs }}>
                  {wxData.location}
                </Text>
              )}
            </Group>
            <Group gap={6}>
              {wxLastRefresh && (
                <Tooltip label="Auto-refreshes every 5 min" withArrow>
                  <Text size="xs" c="#5a6478" style={{ ...monoXs, fontSize: '9px', cursor: 'help' }}>
                    {wxLastRefresh.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </Text>
                </Tooltip>
              )}
              <Tooltip label="Refresh weather" withArrow>
                <ActionIcon
                  variant="subtle"
                  color="cyan"
                  size="sm"
                  onClick={() => fetchWeather(true)}
                  loading={wxRefreshing}
                >
                  <IconRefresh size={14} />
                </ActionIcon>
              </Tooltip>
            </Group>
          </Group>

          {wxLoading ? (
            <Group justify="center" py="xl">
              <Loader color="cyan" size="sm" />
              <Text c="#5a6478" size="sm">Loading weather data...</Text>
            </Group>
          ) : wx && !wx.error ? (
            <ScrollArea style={{ flex: 1 }} type="auto">
              <Stack gap="xs">
                {/* Status banners */}
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {wxData?.metar && !wxData.metar.error && wxData.metar.flight_category && (
                    <Tooltip label={wxData.metar.flight_category_desc || ''} withArrow>
                      <div style={{
                        padding: '6px 14px', borderRadius: '6px',
                        background: `${wxData.metar.flight_category_color}15`,
                        border: `1px solid ${wxData.metar.flight_category_color}40`,
                        display: 'flex', alignItems: 'center', gap: '8px', cursor: 'help',
                      }}>
                        <IconPlane size={16} color={wxData.metar.flight_category_color} />
                        <Text size="sm" c={wxData.metar.flight_category_color} fw={700}
                          style={{ ...monoSm, fontSize: '13px', letterSpacing: '2px' }}>
                          {wxData.metar.flight_category} — {wxData.airport}
                        </Text>
                      </div>
                    </Tooltip>
                  )}
                  {windSeverity && (
                    <div style={{
                      padding: '6px 14px', borderRadius: '6px',
                      background: `${windSeverity.color}15`,
                      border: `1px solid ${windSeverity.color}40`,
                      display: 'flex', alignItems: 'center', gap: '8px', flex: 1,
                    }}>
                      <IconWind size={16} color={windSeverity.color} />
                      <Text size="sm" c={windSeverity.color} fw={700}
                        style={{ ...monoSm, fontSize: '13px', letterSpacing: '2px' }}>
                        WIND: {windSeverity.label}
                      </Text>
                    </div>
                  )}
                </div>

                {/* NWS alerts */}
                {wxData?.alerts && wxData.alerts.length > 0 && (
                  <Stack gap={4}>
                    {wxData.alerts.slice(0, 2).map((alert, i) => (
                      <Tooltip key={i} label={alert.description || ''} multiline w={400} withArrow position="bottom">
                        <div style={{
                          padding: '6px 14px', borderRadius: '6px',
                          background: 'rgba(255,68,68,0.08)', border: '1px solid rgba(255,68,68,0.3)',
                          display: 'flex', alignItems: 'center', gap: '8px', cursor: 'help',
                        }}>
                          <IconAlertTriangle size={16} color="#ff4444" />
                          <Text size="xs" c="#ff4444" fw={700} style={{ ...monoSm, fontSize: '12px' }}>
                            NWS: {alert.event}
                          </Text>
                          <Text size="xs" c="#5a6478" style={{ flex: 1 }} lineClamp={1}>
                            {alert.headline}
                          </Text>
                        </div>
                      </Tooltip>
                    ))}
                  </Stack>
                )}

                {/* ── Primary weather: large temp + condition ── */}
                <div style={{
                  padding: '14px 16px', background: '#050608', borderRadius: '8px',
                  border: '1px solid #1a1f2e', display: 'flex', alignItems: 'center', gap: '16px',
                }}>
                  <div style={{ flex: 1 }}>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '36px', lineHeight: 1 }}>
                      {wx.temperature_f != null ? `${Math.round(wx.temperature_f)}°F` : '—'}
                    </Text>
                    <Text c="#5a6478" mt={2} style={{ ...monoSm, fontSize: '13px' }}>{wx.condition}</Text>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <Group gap={6} justify="flex-end" mb={4}>
                      <IconWind size={16} color="#00d4ff" />
                      <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '24px', lineHeight: 1 }}>
                        {wx.wind_speed_mph != null ? `${Math.round(wx.wind_speed_mph)}` : '—'}
                        <span style={{ fontSize: '14px', color: '#5a6478', marginLeft: '3px' }}>mph</span>
                      </Text>
                      {wx.wind_direction_deg != null && <WindIndicator deg={wx.wind_direction_deg} />}
                    </Group>
                    <Text c="#5a6478" style={{ ...monoXs, fontSize: '11px' }}>
                      {wx.wind_direction || '—'}{wx.wind_gusts_mph ? ` / Gusts ${Math.round(wx.wind_gusts_mph)} mph` : ''}
                    </Text>
                  </div>
                </div>

                {/* ── Secondary weather stats: 2x2 grid ── */}
                <SimpleGrid cols={2} spacing={8}>
                  <div style={{ padding: '10px 12px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={6} mb={4}>
                      <IconCloud size={14} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>CLOUDS</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '24px', lineHeight: 1.1 }}>
                      {wx.cloud_cover_pct != null ? `${wx.cloud_cover_pct}%` : '—'}
                    </Text>
                    <Text c="#5a6478" mt={2} style={{ fontSize: '11px' }}>
                      {wx.humidity_pct != null ? `Humidity ${wx.humidity_pct}%` : ''}
                    </Text>
                  </div>

                  <div style={{ padding: '10px 12px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={6} mb={4}>
                      <IconEye size={14} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>VISIBILITY</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '24px', lineHeight: 1.1 }}>
                      {wx.visibility_m != null ? `${(wx.visibility_m / 1609.344).toFixed(1)}` : '—'}
                      <span style={{ fontSize: '14px', color: '#5a6478', marginLeft: '3px' }}>mi</span>
                    </Text>
                  </div>

                  <div style={{ padding: '10px 12px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={6} mb={4}>
                      <IconGauge size={14} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>PRESSURE</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '24px', lineHeight: 1.1 }}>
                      {wx.pressure_msl_hpa != null ? `${(wx.pressure_msl_hpa * 0.02953).toFixed(2)}` : '—'}
                      <span style={{ fontSize: '14px', color: '#5a6478', marginLeft: '3px' }}>inHg</span>
                    </Text>
                  </div>

                  <div style={{ padding: '10px 12px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={6} mb={4}>
                      <IconDroplet size={14} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={monoXs}>DEW POINT</Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ ...bebasFont, fontSize: '24px', lineHeight: 1.1 }}>
                      {wxData?.metar?.dewpoint_c != null ? `${Math.round(wxData.metar.dewpoint_c * 9 / 5 + 32)}°F` : '—'}
                    </Text>
                  </div>
                </SimpleGrid>

                {/* Raw METAR */}
                {wxData?.metar?.raw_metar && (
                  <div style={{ padding: '8px 12px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Text size="xs" c="#5a6478" mb={2} style={{ ...monoXs, fontSize: '10px' }}>
                      METAR {wxData.metar.station}
                    </Text>
                    <Text size="xs" c="#e8edf2"
                      style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', wordBreak: 'break-all' }}>
                      {wxData.metar.raw_metar}
                    </Text>
                  </div>
                )}

                {/* FAA Airspace */}
                <div>
                  <Group gap="xs" mb={6}>
                    <IconAlertTriangle size={14} color="#ff6b1a" />
                    <Text size="xs" c="#e8edf2" fw={600} style={{ letterSpacing: '1px', fontSize: '12px' }}>
                      FAA AIRSPACE — {wxData?.airport || 'N/A'}
                    </Text>
                  </Group>
                  <SimpleGrid cols={2} spacing="xs">
                    <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                      <Text size="xs" c="#ff6b1a" fw={700} mb={4} style={{ ...monoXs }}>TFRs</Text>
                      {wxData?.tfrs && wxData.tfrs.length > 0 ? (
                        <Stack gap={2}>
                          {wxData.tfrs.slice(0, 3).map((tfr, i) => (
                            <Group key={i} gap="xs">
                              {tfr.status ? (
                                <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>{tfr.status}</Text>
                              ) : (
                                <>
                                  <Badge size="xs" color="orange" variant="light">TFR</Badge>
                                  <Text size="xs" c="#e8edf2" style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                                    {tfr.notam_id}
                                  </Text>
                                </>
                              )}
                            </Group>
                          ))}
                        </Stack>
                      ) : (
                        <Text size="xs" c="#00ff88" style={{ fontSize: '10px' }}>No active TFRs</Text>
                      )}
                    </div>
                    <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e', maxHeight: '120px', overflowY: 'auto' }}>
                      <Text size="xs" c="#00d4ff" fw={700} mb={4} style={{ ...monoXs }}>NOTAMS</Text>
                      {wxData?.notams && wxData.notams.length > 0 ? (
                        <Stack gap={2}>
                          {wxData.notams.map((notam, i) => (
                            <div key={i}>
                              {notam.status ? (
                                <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>{notam.status}</Text>
                              ) : (
                                <Tooltip label={notam.text || ''} multiline w={400} position="left" withArrow>
                                  <Group gap={4} style={{ cursor: 'help' }}>
                                    <IconInfoCircle size={10} color="#5a6478" />
                                    <Text size="xs" c="#e8edf2" lineClamp={1}
                                      style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                                      {notam.id || 'NOTAM'}: {(notam.text || '').slice(0, 60)}
                                    </Text>
                                  </Group>
                                </Tooltip>
                              )}
                            </div>
                          ))}
                        </Stack>
                      ) : (
                        <Text size="xs" c="#00ff88" style={{ fontSize: '10px' }}>No active NOTAMs</Text>
                      )}
                    </div>
                  </SimpleGrid>
                </div>
              </Stack>
            </ScrollArea>
          ) : (
            <Text c="#5a6478" ta="center" py="md">
              Weather data unavailable — check network connection.
            </Text>
          )}
        </Card>

        {/* ═══ EQUIPMENT STATUS — compact ═══ */}
        <Card padding="sm" radius="md" style={{ ...panelStyle, flexShrink: 0 }}>
          <Group justify="space-between" mb={4}>
            <Group gap="xs">
              <Title order={4} c="#e8edf2" style={{ letterSpacing: '1px', fontSize: '14px' }}>
                EQUIPMENT STATUS
              </Title>
              {hasAlerts && (
                <Badge color="red" variant="filled" size="xs" circle>
                  {maintenanceAlerts.length + batteryAlerts.length}
                </Badge>
              )}
            </Group>
            <Button
              size="xs"
              variant="subtle"
              color="cyan"
              onClick={() => navigate('/maintenance')}
              styles={{ root: { ...monoXs, padding: '0 6px' } }}
            >
              VIEW ALL
            </Button>
          </Group>

          <ScrollArea style={{ flex: 1 }} type="auto">
            <Stack gap={6}>
              {/* Maintenance alerts */}
              {maintenanceAlerts.length > 0 ? (
                <div>
                  <Group gap={4} mb={4}>
                    <IconTool size={12} color="#ff6b1a" />
                    <Text size="xs" c="#ff6b1a" fw={700} style={{ ...monoXs, letterSpacing: '2px' }}>
                      MAINTENANCE ALERTS
                    </Text>
                  </Group>
                  <Stack gap={3}>
                    {maintenanceAlerts.slice(0, 3).map((alert, i) => (
                      <div key={i} style={{
                        padding: '5px 8px', borderRadius: '4px',
                        background: alert.overdue ? 'rgba(255, 68, 68, 0.08)' : 'rgba(255, 107, 26, 0.08)',
                        border: `1px solid ${alert.overdue ? 'rgba(255, 68, 68, 0.3)' : 'rgba(255, 107, 26, 0.25)'}`,
                      }}>
                        <Group justify="space-between" wrap="nowrap">
                          <Group gap={6} wrap="nowrap">
                            <IconCalendarDue size={12} color={alert.overdue ? '#ff4444' : '#ff6b1a'} />
                            <Text size="xs" c="#e8edf2" fw={600} tt="capitalize" style={{ fontSize: '11px' }}>
                              {(alert.maintenance_type || '').replace(/_/g, ' ')}
                            </Text>
                            <Text size="xs" c="#00d4ff" style={{ fontSize: '10px' }}>
                              {alert.aircraft_name || ''}
                            </Text>
                          </Group>
                          <Badge color={alert.overdue ? 'red' : 'orange'} variant="light" size="xs">
                            {alert.overdue
                              ? `${Math.abs(alert.days_until)}d OVERDUE`
                              : alert.days_until === 0 ? 'TODAY' : `${alert.days_until}d`
                            }
                          </Badge>
                        </Group>
                      </div>
                    ))}
                  </Stack>
                </div>
              ) : (
                <div style={{
                  padding: '6px 8px', borderRadius: '4px',
                  background: 'rgba(0, 255, 136, 0.05)', border: '1px solid rgba(0, 255, 136, 0.15)',
                }}>
                  <Group gap="xs">
                    <IconTool size={12} color="#00ff88" />
                    <Text size="xs" c="#00ff88" fw={600} style={monoXs}>ALL MAINTENANCE CURRENT</Text>
                  </Group>
                </div>
              )}

              {/* Battery status — compact */}
              <div>
                <Group gap={4} mb={4}>
                  <IconBattery size={12} color="#00d4ff" />
                  <Text size="xs" c="#00d4ff" fw={700} style={{ ...monoXs, letterSpacing: '2px' }}>
                    BATTERY FLEET
                  </Text>
                </Group>

                {batteries.length === 0 ? (
                  <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                    No batteries tracked. Add batteries in Fleet settings.
                  </Text>
                ) : (
                  <Stack gap={3}>
                    {batteries
                      .filter((b) => b.status === 'active')
                      .sort((a, b) => a.health_pct - b.health_pct)
                      .slice(0, 4)
                      .map((b) => {
                        const healthColor = b.health_pct >= 70 ? '#00ff88' : b.health_pct >= 40 ? '#ff6b1a' : '#ff4444';
                        const needsAttention = b.health_pct < 40 || b.cycle_count > 200;
                        return (
                          <div key={b.id} style={{
                            padding: '4px 8px', borderRadius: '4px',
                            background: needsAttention ? 'rgba(255, 68, 68, 0.06)' : '#050608',
                            border: `1px solid ${needsAttention ? 'rgba(255, 68, 68, 0.2)' : '#1a1f2e'}`,
                          }}>
                            <Group justify="space-between" wrap="nowrap" mb={2}>
                              <Group gap={6} wrap="nowrap">
                                {needsAttention ? (
                                  <IconBatteryOff size={12} color="#ff4444" />
                                ) : (
                                  <IconBattery size={12} color={healthColor} />
                                )}
                                <Text size="xs" c="#e8edf2" fw={600} style={{ fontSize: '11px' }}>
                                  {b.serial}
                                </Text>
                              </Group>
                              <Group gap={6} wrap="nowrap">
                                <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '9px' }}>
                                  {b.cycle_count}cy
                                </Text>
                                <Text size="xs" c={healthColor} fw={700} style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                                  {b.health_pct}%
                                </Text>
                              </Group>
                            </Group>
                            <Progress
                              value={b.health_pct}
                              color={healthColor}
                              size={3}
                              radius="xl"
                              styles={{ root: { background: '#1a1f2e' } }}
                            />
                          </div>
                        );
                      })}
                  </Stack>
                )}
              </div>
            </Stack>
          </ScrollArea>
        </Card>

        </div>{/* end right column */}
      </div>

      {/* ═══ INITIATE SERVICES MODAL ═══ */}
      <Modal
        opened={initiateModalOpen}
        onClose={() => setInitiateModalOpen(false)}
        title="Initiate Services"
        styles={{
          header: { background: '#0e1117' },
          content: { background: '#0e1117' },
          title: { color: '#e8edf2', ...bebasFont, letterSpacing: '1px' },
        }}
      >
        {!intakeResult ? (
          <Stack gap="md">
            <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Enter the customer's email to send them an onboarding form with TOS.
            </Text>
            <TextInput
              label="Customer Email"
              placeholder="customer@example.com"
              value={initiateEmail}
              onChange={(e) => setInitiateEmail(e.target.value)}
              styles={inputStyles}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleInitiateServices(); } }}
            />
            <Button
              color="cyan"
              fullWidth
              loading={initiateLoading}
              onClick={handleInitiateServices}
              styles={{ root: { ...bebasFont, letterSpacing: '1px' } }}
            >
              GENERATE INTAKE LINK
            </Button>
          </Stack>
        ) : (
          <Stack gap="md">
            <Badge color="green" variant="light" size="lg" leftSection={<IconCheck size={12} />}>
              LINK GENERATED
            </Badge>
            <Text c="#5a6478" size="xs" style={{ ...monoSm }}>
              INTAKE LINK
            </Text>
            <Group gap="xs">
              <TextInput
                value={intakeResult.intake_url}
                readOnly
                onClick={(e) => (e.target as HTMLInputElement).select()}
                style={{ flex: 1 }}
                styles={inputStyles}
              />
              <Tooltip label={linkCopied ? 'Copied!' : 'Copy'}>
                <ActionIcon color={linkCopied ? 'green' : 'cyan'} variant="light" onClick={copyIntakeLink}>
                  {linkCopied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                </ActionIcon>
              </Tooltip>
            </Group>
            <Button
              leftSection={<IconMail size={16} />}
              color="cyan"
              variant="light"
              fullWidth
              onClick={handleSendIntakeEmail}
              styles={{ root: { ...bebasFont, letterSpacing: '1px' } }}
            >
              SEND VIA EMAIL
            </Button>
            <Text c="#5a6478" size="xs" ta="center" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Link expires in 7 days
            </Text>
          </Stack>
        )}
      </Modal>
    </div>
  );
}
