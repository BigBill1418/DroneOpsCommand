/**
 * MissionFlightsEdit — Mission Hub redesign (v2.67.0, ADR-0014).
 *
 * Focused editor for the Flights facet. Mounted at
 * `/missions/:id/flights/edit`. Add or remove flights, assign an
 * aircraft per flight, manage which aircraft were used.
 *
 * Endpoints:
 * - GET /api/missions/{id}                        — load attached flights
 * - GET /api/flight-library                       — load available flights
 *   (falls back to GET /api/flights for legacy ODL data)
 * - GET /api/aircraft                             — load aircraft list
 * - POST /api/missions/{id}/flights               — attach flight
 * - DELETE /api/missions/{id}/flights/{flight_id} — detach flight
 * - PATCH /api/missions/{id}/flights/{flight_id}/aircraft — reassign
 *
 * NEVER calls POST /api/missions — see constraint comment on
 * `handleAddFlight()` below.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconRefresh, IconTrash } from '@tabler/icons-react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Aircraft, Mission } from '../api/types';
import { cardStyle } from '../components/shared/styles';

interface AttachedFlight {
  /** mission_flight row id */
  _flightId: string;
  /** assigned aircraft id (may be null) */
  _aircraftId: string | null;
  /** native flight UUID (may be null on legacy ODL rows) */
  flight_id: string | null;
  /** display fields lifted from flight_data_cache */
  display_name?: string;
  drone_model?: string;
  start_time?: string;
  duration_secs?: number;
  // ad-hoc raw shape
  [k: string]: unknown;
}

interface AvailableFlight {
  id?: string | number;
  flight_id?: string;
  display_name?: string;
  name?: string;
  drone_model?: string;
  drone?: string;
  start_time?: string;
  date?: string;
  duration_secs?: number;
  duration?: number;
  source?: string;
  aircraft_id?: string | null;
  [k: string]: unknown;
}

function flightDate(f: { start_time?: string; date?: string; created_at?: string }): string {
  const raw = f.start_time || f.date || f.created_at || '';
  if (!raw) return '—';
  const d = new Date(raw);
  if (isNaN(d.getTime())) return String(raw);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function flightDuration(f: { duration_secs?: number; duration?: number }): string {
  const secs = f.duration_secs || f.duration || 0;
  if (!secs) return '—';
  const m = Math.floor(Number(secs) / 60);
  const s = Math.round(Number(secs) % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function flightName(f: { display_name?: string; name?: string; flight_id?: string; id?: string | number }): string {
  return f.display_name || f.name || `Flight ${f.flight_id ?? f.id ?? ''}`;
}

function flightDrone(f: { drone_model?: string; drone?: string }): string {
  return f.drone_model || f.drone || '—';
}

export default function MissionFlightsEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [flightsLoading, setFlightsLoading] = useState(false);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [missionAircraft, setMissionAircraft] = useState<string[]>([]);
  const [availableFlights, setAvailableFlights] = useState<AvailableFlight[]>([]);
  const [attached, setAttached] = useState<AttachedFlight[]>([]);

  const loadMission = useCallback(async () => {
    if (!id) return;
    try {
      const resp = await api.get<Mission>(`/missions/${id}`);
      const m = resp.data;
      const rows: AttachedFlight[] = m.flights.map((f) => ({
        ...(f.flight_data_cache || {}),
        _flightId: f.id,
        _aircraftId: f.aircraft_id ?? null,
        flight_id: f.opendronelog_flight_id || (f.flight_data_cache?.id as string | undefined) || null,
      }));
      setAttached(rows);
      const aircraftIds = [
        ...new Set(m.flights.filter((f) => f.aircraft_id).map((f) => f.aircraft_id!)),
      ];
      setMissionAircraft(aircraftIds);
    } catch (err) {
      console.error('[MissionFlightsEdit] mission load failed', err);
      notifications.show({
        title: 'Load failed',
        message: 'Could not load mission flights — returning to mission list.',
        color: 'red',
      });
      navigate('/missions');
    }
  }, [id, navigate]);

  const loadFlights = useCallback(async () => {
    setFlightsLoading(true);
    try {
      let flights: AvailableFlight[] = [];
      try {
        const resp = await api.get('/flight-library');
        flights = Array.isArray(resp.data) ? resp.data : [];
      } catch {
        const resp = await api.get('/flights');
        if (Array.isArray(resp.data)) {
          flights = resp.data;
        } else if (resp.data && typeof resp.data === 'object') {
          flights =
            resp.data.flights || resp.data.data || resp.data.results || resp.data.items || [];
        }
      }
      flights.sort((a, b) => {
        const dA = String(a.start_time || a.date || a.created_at || '');
        const dB = String(b.start_time || b.date || b.created_at || '');
        return dB.localeCompare(dA);
      });
      setAvailableFlights(flights);
    } catch (err) {
      console.error('[MissionFlightsEdit] flight library load failed', err);
      notifications.show({
        title: 'Flights',
        message: 'Could not fetch flight library.',
        color: 'yellow',
      });
    } finally {
      setFlightsLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      try {
        const aircraftResp = await api.get<Aircraft[]>('/aircraft').catch(() => ({ data: [] as Aircraft[] }));
        if (cancelled) return;
        setAircraft(aircraftResp.data);
        await Promise.all([loadMission(), loadFlights()]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    init();
    return () => {
      cancelled = true;
    };
  }, [loadMission, loadFlights]);

  const handleAddFlight = async (flight: AvailableFlight, aircraftId?: string | null) => {
    // CONSTRAINT: this page edits an EXISTING mission only.
    // POST /missions is forbidden here per ADR-0013 / spec §2.
    // Only the per-mission flights subresource is touched.
    if (!id) return;
    const isNativeFlight =
      typeof flight.id === 'string' && flight.id.includes('-') && Boolean(flight.source);
    try {
      const resp = await api.post(`/missions/${id}/flights`, {
        flight_id: isNativeFlight ? flight.id : null,
        opendronelog_flight_id: isNativeFlight
          ? null
          : String(flight.id ?? flight.flight_id ?? ''),
        aircraft_id: aircraftId || flight.aircraft_id || null,
        flight_data_cache: flight,
      });
      const newRow: AttachedFlight = {
        ...(flight as Record<string, unknown>),
        _flightId: resp.data.id,
        _aircraftId: aircraftId || flight.aircraft_id || null,
        flight_id:
          (typeof flight.id === 'string' || typeof flight.id === 'number')
            ? String(flight.id)
            : flight.flight_id ?? null,
      };
      setAttached((prev) => [...prev, newRow]);
      notifications.show({ title: 'Flight added', message: flightName(flight), color: 'cyan' });
    } catch (err) {
      console.error('[MissionFlightsEdit] add failed', err);
      notifications.show({
        title: 'Error',
        message: 'Failed to add flight',
        color: 'red',
      });
    }
  };

  const handleRemoveFlight = async (flightRowId: string) => {
    // CONSTRAINT: see handleAddFlight above — no POST /missions here either.
    if (!id) return;
    try {
      await api.delete(`/missions/${id}/flights/${flightRowId}`);
      setAttached((prev) => prev.filter((f) => f._flightId !== flightRowId));
    } catch (err) {
      console.error('[MissionFlightsEdit] remove failed', err);
      notifications.show({
        title: 'Error',
        message: 'Failed to remove flight',
        color: 'red',
      });
    }
  };

  const handleAssignAircraft = async (flightRowId: string, aircraftId: string | null) => {
    // CONSTRAINT: see handleAddFlight above — no POST /missions here either.
    if (!id) return;
    const previous = attached.find((f) => f._flightId === flightRowId)?._aircraftId ?? null;
    setAttached((prev) =>
      prev.map((f) => (f._flightId === flightRowId ? { ...f, _aircraftId: aircraftId } : f)),
    );
    try {
      await api.patch(`/missions/${id}/flights/${flightRowId}/aircraft`, {
        aircraft_id: aircraftId,
      });
    } catch (err) {
      console.error('[MissionFlightsEdit] aircraft assign failed', err);
      // revert
      setAttached((prev) =>
        prev.map((f) => (f._flightId === flightRowId ? { ...f, _aircraftId: previous } : f)),
      );
      notifications.show({
        title: 'Error',
        message: 'Failed to assign aircraft',
        color: 'red',
      });
    }
  };

  const handleDone = () => {
    navigate(`/missions/${id}`);
  };

  if (loading) {
    return (
      <Stack gap="lg" align="center" py="xl">
        <Loader color="cyan" size="lg" />
        <Text c="#5a6478">Loading flights...</Text>
      </Stack>
    );
  }

  // Build the available flights table, hiding rows already attached.
  const attachedKeys = new Set(
    attached
      .map((a) => (a.flight_id ? String(a.flight_id) : null))
      .filter((v): v is string => Boolean(v)),
  );
  const availableNotYetAttached = availableFlights.filter((f) => {
    const key = String(f.id ?? f.flight_id ?? '');
    return key && !attachedKeys.has(key);
  });

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          EDIT FLIGHTS
        </Title>
        <Button
          color="cyan"
          onClick={handleDone}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          DONE
        </Button>
      </Group>

      {/* Aircraft used (multi-select chip group) */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="sm">
          <Text
            c="#e8edf2"
            fw={600}
            style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}
          >
            AIRCRAFT USED
          </Text>
          <Checkbox.Group value={missionAircraft} onChange={setMissionAircraft}>
            <Group gap="xs">
              {aircraft.map((a) => (
                <Checkbox
                  key={a.id}
                  value={a.id}
                  label={a.model_name}
                  color="cyan"
                  size="sm"
                  styles={{ label: { color: '#e8edf2', fontSize: 13 } }}
                />
              ))}
              {aircraft.length === 0 && (
                <Text c="#5a6478" size="xs">
                  No aircraft on file. Add aircraft in Settings.
                </Text>
              )}
            </Group>
          </Checkbox.Group>
        </Stack>
      </Card>

      {/* Already-attached flights — remove + reassign */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="sm">
          <Group justify="space-between">
            <Text
              c="#e8edf2"
              fw={600}
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}
            >
              ATTACHED FLIGHTS
            </Text>
            <Badge color="cyan" variant="light" size="sm">
              {attached.length} attached
            </Badge>
          </Group>
          {attached.length === 0 ? (
            <Text c="#5a6478" size="sm">
              No flights attached yet — add some from the available list below.
            </Text>
          ) : (
            <ScrollArea h={280} type="auto" offsetScrollbars>
              <Table
                verticalSpacing={6}
                styles={{
                  table: { color: '#e8edf2', fontSize: 12 },
                  th: {
                    color: '#00d4ff',
                    fontFamily: "'Share Tech Mono', monospace",
                    fontSize: 12,
                    borderBottom: '1px solid #1a1f2e',
                    padding: '6px 8px',
                  },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>NAME</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>DRONE</Table.Th>
                    <Table.Th>DURATION</Table.Th>
                    <Table.Th>ASSIGN AIRCRAFT</Table.Th>
                    <Table.Th w={48}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {attached.map((f) => (
                    <Table.Tr key={f._flightId}>
                      <Table.Td>{flightName(f as { display_name?: string; flight_id?: string })}</Table.Td>
                      <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {flightDate(f as { start_time?: string })}
                      </Table.Td>
                      <Table.Td>{flightDrone(f as { drone_model?: string })}</Table.Td>
                      <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {flightDuration(f as { duration_secs?: number })}
                      </Table.Td>
                      <Table.Td>
                        <Select
                          size="xs"
                          placeholder="Assign..."
                          data={aircraft.map((a) => ({ value: a.id, label: a.model_name }))}
                          value={f._aircraftId}
                          onChange={(val) => handleAssignAircraft(f._flightId, val)}
                          clearable
                          aria-label={`Assign aircraft to ${flightName(f as { display_name?: string })}`}
                          styles={{
                            input: {
                              background: '#050608',
                              borderColor: '#1a1f2e',
                              color: '#e8edf2',
                              minWidth: 130,
                              height: 28,
                              minHeight: 28,
                              fontSize: 13,
                            },
                          }}
                        />
                      </Table.Td>
                      <Table.Td>
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          size="sm"
                          onClick={() => handleRemoveFlight(f._flightId)}
                          aria-label={`Remove ${flightName(f as { display_name?: string })}`}
                          title="Remove flight"
                        >
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          )}
        </Stack>
      </Card>

      {/* Available flights — add */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="sm">
          <Group justify="space-between">
            <Group gap="xs">
              <Text
                c="#e8edf2"
                fw={600}
                style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}
              >
                AVAILABLE FLIGHTS
              </Text>
              <ActionIcon
                variant="subtle"
                color="cyan"
                size="sm"
                onClick={loadFlights}
                loading={flightsLoading}
                aria-label="Reload flights"
                title="Reload flights"
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Group>
            <Badge color="gray" variant="light" size="sm">
              {availableNotYetAttached.length} available
            </Badge>
          </Group>
          {flightsLoading ? (
            <Group justify="center" py="md">
              <Loader color="cyan" />
            </Group>
          ) : availableNotYetAttached.length === 0 ? (
            <Text c="#5a6478" size="sm">
              No more flights to add. Upload flight logs on the Flights page.
            </Text>
          ) : (
            <ScrollArea h={280} type="auto" offsetScrollbars>
              <Table
                verticalSpacing={6}
                styles={{
                  table: { color: '#e8edf2', fontSize: 12 },
                  th: {
                    color: '#00d4ff',
                    fontFamily: "'Share Tech Mono', monospace",
                    fontSize: 12,
                    borderBottom: '1px solid #1a1f2e',
                    padding: '6px 8px',
                  },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>NAME</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>DRONE</Table.Th>
                    <Table.Th>DURATION</Table.Th>
                    <Table.Th w={120}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {availableNotYetAttached.map((f, i) => (
                    <Table.Tr key={String(f.id ?? f.flight_id ?? i)}>
                      <Table.Td>{flightName(f)}</Table.Td>
                      <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {flightDate(f)}
                      </Table.Td>
                      <Table.Td>{flightDrone(f)}</Table.Td>
                      <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {flightDuration(f)}
                      </Table.Td>
                      <Table.Td>
                        <Button
                          size="xs"
                          color="cyan"
                          variant="light"
                          onClick={() => handleAddFlight(f, missionAircraft[0])}
                          aria-label={`Add ${flightName(f)}`}
                        >
                          ADD
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          )}
        </Stack>
      </Card>

      <Group justify="flex-end">
        <Button
          variant="default"
          onClick={handleDone}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          BACK TO MISSION
        </Button>
      </Group>
    </Stack>
  );
}
