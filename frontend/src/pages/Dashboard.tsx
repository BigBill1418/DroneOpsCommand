import { useEffect, useState } from 'react';
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
} from '@mantine/core';
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
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Mission, Customer } from '../api/types';

function StatCard({ icon: Icon, label, value, color }: { icon: any; label: string; value: string; color: string }) {
  return (
    <Card
      padding="sm"
      radius="md"
      style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}
    >
      <Group gap="sm">
        <Icon size={24} color={color} />
        <div>
          <Text
            size="xs"
            c="#5a6478"
            style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}
          >
            {label}
          </Text>
          <Text
            fw={700}
            c="#e8edf2"
            style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '22px', lineHeight: 1.1 }}
          >
            {value}
          </Text>
        </div>
      </Group>
    </Card>
  );
}

const statusColors: Record<string, string> = {
  draft: 'yellow',
  completed: 'cyan',
  sent: 'green',
};

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
  wind_dir_deg?: number;
  wind_speed_kt?: number;
  wind_gust_kt?: number;
  visibility?: string;
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

export default function Dashboard() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [flightCount, setFlightCount] = useState<number | null>(null);
  const [wxData, setWxData] = useState<WeatherResponse | null>(null);
  const [wxLoading, setWxLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    api.get('/missions').then((r) => setMissions(r.data)).catch(() => {});
    api.get('/customers').then((r) => setCustomers(r.data)).catch(() => {});
    api.get('/flights').then((r) => {
      const data = Array.isArray(r.data) ? r.data : r.data?.flights || r.data?.data || r.data?.results || r.data?.items || [];
      setFlightCount(data.length);
    }).catch(() => setFlightCount(0));
    api.get('/weather/current').then((r) => setWxData(r.data)).catch(() => {}).finally(() => setWxLoading(false));
  }, []);

  const recentMissions = missions.slice(0, 5);
  const draftCount = missions.filter((m) => m.status === 'draft').length;

  const wx = wxData?.weather;
  const windSeverity = wx ? getWindSeverity(wx.wind_speed_mph, wx.wind_gusts_mph) : null;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Group justify="space-between" wrap="wrap" mb="sm">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          DASHBOARD
        </Title>
        <Button
          leftSection={<IconPlus size={16} />}
          color="cyan"
          size="sm"
          onClick={() => navigate('/missions/new')}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          NEW MISSION
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }} mb="sm" spacing="sm">
        <StatCard icon={IconPlane} label="FLIGHTS (ODL)" value={flightCount !== null ? String(flightCount) : '—'} color="#00d4ff" />
        <StatCard icon={IconDrone} label="TOTAL MISSIONS" value={String(missions.length)} color="#00d4ff" />
        <StatCard icon={IconFileText} label="DRAFTS PENDING" value={String(draftCount)} color="#ff6b1a" />
        <StatCard icon={IconUsers} label="CUSTOMERS" value={String(customers.length)} color="#00d4ff" />
      </SimpleGrid>

      {/* Two-column layout: Recent Missions + Flight Conditions */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', minHeight: 0 }}>
        {/* Recent Missions */}
        <Card
          padding="sm"
          radius="md"
          style={{ background: '#0e1117', border: '1px solid #1a1f2e', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        >
          <Title order={4} c="#e8edf2" mb="xs" style={{ letterSpacing: '1px' }}>
            RECENT MISSIONS
          </Title>

          {recentMissions.length === 0 ? (
            <Text c="#5a6478" ta="center" py="xl">
              No missions yet. Create your first mission to get started.
            </Text>
          ) : (
            <ScrollArea style={{ flex: 1 }} type="auto">
            <Table
              highlightOnHover
              styles={{
                table: { color: '#e8edf2', minWidth: 400 },
                th: {
                  color: '#00d4ff',
                  fontFamily: "'Share Tech Mono', monospace",
                  fontSize: '11px',
                  letterSpacing: '1px',
                  borderBottom: '1px solid #1a1f2e',
                  padding: '6px 8px',
                },
                td: { borderBottom: '1px solid #1a1f2e', padding: '6px 8px', fontSize: '13px' },
                tr: { '&:hover': { backgroundColor: 'rgba(0, 212, 255, 0.05)' } },
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
                {recentMissions.map((mission) => (
                  <Table.Tr
                    key={mission.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/missions/${mission.id}`)}
                  >
                    <Table.Td>{mission.title}</Table.Td>
                    <Table.Td>
                      <Text size="xs" c="#5a6478" tt="capitalize">
                        {mission.mission_type.replace('_', ' ')}
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

        {/* Flight Conditions */}
        <Card
          padding="sm"
          radius="md"
          style={{ background: '#0e1117', border: '1px solid #1a1f2e', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        >
          <Group justify="space-between" mb="xs">
            <Title order={4} c="#e8edf2" style={{ letterSpacing: '1px' }}>
              FLIGHT CONDITIONS
            </Title>
            {wxData && (
              <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                {wxData.location}
              </Text>
            )}
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
                        padding: '5px 12px',
                        borderRadius: '6px',
                        background: `${wxData.metar.flight_category_color}15`,
                        border: `1px solid ${wxData.metar.flight_category_color}40`,
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        cursor: 'help',
                      }}>
                        <IconPlane size={14} color={wxData.metar.flight_category_color} />
                        <Text size="xs" c={wxData.metar.flight_category_color} fw={700}
                          style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '2px', fontSize: '11px' }}>
                          {wxData.metar.flight_category} — {wxData.airport}
                        </Text>
                      </div>
                    </Tooltip>
                  )}

                  {windSeverity && (
                    <div style={{
                      padding: '5px 12px',
                      borderRadius: '6px',
                      background: `${windSeverity.color}15`,
                      border: `1px solid ${windSeverity.color}40`,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      flex: 1,
                    }}>
                      <IconWind size={14} color={windSeverity.color} />
                      <Text size="xs" c={windSeverity.color} fw={700}
                        style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '2px', fontSize: '11px' }}>
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
                          padding: '5px 12px',
                          borderRadius: '6px',
                          background: 'rgba(255,68,68,0.08)',
                          border: '1px solid rgba(255,68,68,0.3)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          cursor: 'help',
                        }}>
                          <IconAlertTriangle size={14} color="#ff4444" />
                          <Text size="xs" c="#ff4444" fw={700}
                            style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '11px' }}>
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

                {/* Weather stats 2x2 grid */}
                <SimpleGrid cols={2} spacing="xs">
                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconTemperature size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}>
                        TEMP
                      </Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px', lineHeight: 1.1 }}>
                      {wx.temperature_f != null ? `${Math.round(wx.temperature_f)}°F` : '—'}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>{wx.condition}</Text>
                  </div>

                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconWind size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}>
                        WIND
                      </Text>
                    </Group>
                    <Group gap={4} align="baseline">
                      <Text c="#e8edf2" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px', lineHeight: 1.1 }}>
                        {wx.wind_speed_mph != null ? `${Math.round(wx.wind_speed_mph)}` : '—'}
                      </Text>
                      <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>MPH</Text>
                      {wx.wind_direction_deg != null && <WindIndicator deg={wx.wind_direction_deg} />}
                    </Group>
                    <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                      {wx.wind_direction || '—'}{wx.wind_gusts_mph ? ` / G ${Math.round(wx.wind_gusts_mph)}` : ''}
                    </Text>
                  </div>

                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconCloud size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}>
                        CLOUDS
                      </Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px', lineHeight: 1.1 }}>
                      {wx.cloud_cover_pct != null ? `${wx.cloud_cover_pct}%` : '—'}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>
                      {wx.humidity_pct != null ? `Humidity ${wx.humidity_pct}%` : ''}
                    </Text>
                  </div>

                  <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                    <Group gap={4} mb={2}>
                      <IconEye size={12} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}>
                        VISIBILITY
                      </Text>
                    </Group>
                    <Text c="#e8edf2" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px', lineHeight: 1.1 }}>
                      {wx.visibility_m != null ? `${(wx.visibility_m / 1609.344).toFixed(1)}` : '—'}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontSize: '10px' }}>statute mi</Text>
                  </div>
                </SimpleGrid>

                {/* Raw METAR */}
                {wxData?.metar?.raw_metar && (
                  <div style={{ padding: '6px 10px', background: '#050608', borderRadius: '4px', border: '1px solid #1a1f2e' }}>
                    <Text size="xs" c="#5a6478" mb={2}
                      style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '9px' }}>
                      METAR {wxData.metar.station}
                    </Text>
                    <Text size="xs" c="#e8edf2"
                      style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px', wordBreak: 'break-all' }}>
                      {wxData.metar.raw_metar}
                    </Text>
                  </div>
                )}

                {/* FAA Airspace — TFRs and NOTAMs */}
                <div>
                  <Group gap="xs" mb={6}>
                    <IconAlertTriangle size={14} color="#ff6b1a" />
                    <Text size="xs" c="#e8edf2" fw={600} style={{ letterSpacing: '1px', fontSize: '12px' }}>
                      FAA AIRSPACE — {wxData?.airport || 'N/A'}
                    </Text>
                  </Group>

                  <SimpleGrid cols={2} spacing="xs">
                    <div style={{ padding: '8px', background: '#050608', borderRadius: '6px', border: '1px solid #1a1f2e' }}>
                      <Text size="xs" c="#ff6b1a" fw={700} mb={4}
                        style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}>
                        TFRs
                      </Text>
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
                      <Text size="xs" c="#00d4ff" fw={700} mb={4}
                        style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', fontSize: '10px' }}>
                        NOTAMS
                      </Text>
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
      </div>
    </div>
  );
}
