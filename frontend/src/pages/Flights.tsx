import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPlane, IconRefresh, IconSearch } from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatDistance(meters: number | null | undefined): string {
  if (!meters && meters !== 0) return '—';
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`;
  return `${Math.round(meters)} m`;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return dateStr;
  }
}

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

  const filtered = flights.filter((f) => {
    if (!search) return true;
    const q = search.toLowerCase();
    const searchable = [
      f.name, f.title, f.filename, f.pilot,
      f.aircraft, f.drone, f.model,
      f.id?.toString(), f.flight_id?.toString(),
      f.date, f.created_at, f.start_time,
      f.location, f.notes,
    ].filter(Boolean).join(' ').toLowerCase();
    return searchable.includes(q);
  });

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          FLIGHTS
        </Title>
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

      <TextInput
        placeholder="Search flights..."
        leftSection={<IconSearch size={16} />}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        styles={{
          input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
        }}
      />

      <Card padding="lg" radius="md" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
        {loading ? (
          <Group justify="center" py="xl">
            <Loader color="cyan" size="md" />
            <Text c="#5a6478">Loading flights from OpenDroneLog...</Text>
          </Group>
        ) : error && flights.length === 0 ? (
          <Stack align="center" gap="md" py="xl">
            <Text c="#5a6478" ta="center">{error}</Text>
            <Button variant="light" color="cyan" size="xs" onClick={() => navigate('/settings')}>
              Check Settings
            </Button>
          </Stack>
        ) : (
          <>
            <Group justify="space-between" mb="md">
              <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                {filtered.length} FLIGHT{filtered.length !== 1 ? 'S' : ''} FROM OPENDRONELOG
              </Text>
            </Group>
            <Table
              highlightOnHover
              styles={{
                table: { color: '#e8edf2' },
                th: {
                  color: '#00d4ff',
                  fontFamily: "'Share Tech Mono', monospace",
                  fontSize: '11px',
                  letterSpacing: '1px',
                  borderBottom: '1px solid #1a1f2e',
                },
                td: { borderBottom: '1px solid #1a1f2e' },
              }}
            >
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>ID</Table.Th>
                  <Table.Th>NAME / FILE</Table.Th>
                  <Table.Th>DATE</Table.Th>
                  <Table.Th>DURATION</Table.Th>
                  <Table.Th>DISTANCE</Table.Th>
                  <Table.Th>MAX ALT</Table.Th>
                  <Table.Th>AIRCRAFT</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((f, idx) => (
                  <Table.Tr key={f.id ?? idx}>
                    <Table.Td>
                      <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {f.id ?? f.flight_id ?? idx + 1}
                      </Text>
                    </Table.Td>
                    <Table.Td fw={500}>
                      {f.name || f.title || f.filename || `Flight ${f.id ?? f.flight_id ?? idx + 1}`}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {formatDate(f.date || f.created_at || f.start_time)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {formatDuration(f.duration || f.duration_seconds || f.flight_duration)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {formatDistance(f.distance || f.total_distance || f.distance_meters)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {f.max_altitude || f.max_alt || f.altitude_max
                          ? `${Math.round(f.max_altitude || f.max_alt || f.altitude_max)} m`
                          : '—'}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="#5a6478">
                        {f.aircraft || f.drone || f.model || f.aircraft_name || '—'}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </>
        )}
      </Card>
    </Stack>
  );
}
