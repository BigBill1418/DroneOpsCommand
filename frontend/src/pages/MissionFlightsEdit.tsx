/**
 * MissionFlightsEdit — Mission Hub redesign (v2.67.0, ADR-0014).
 *
 * Focused editor for the Flights facet. Mounted at
 * `/missions/:id/flights/edit`. Add or remove flights — that's it.
 *
 * Aircraft attribution is NOT chosen by the operator here. Each flight
 * log already carries its drone (matched to the fleet at upload time
 * by serial/model), so the backend derives `aircraft_id` on attach.
 * The aircraft column in the attached table is read-only — it shows
 * what the flight log says.
 *
 * Endpoints:
 * - GET /api/missions/{id}                        — load attached flights
 * - GET /api/flight-library?limit=2000            — load available flights
 *   (falls back to GET /api/flights for legacy ODL data)
 * - POST /api/missions/{id}/flights/bulk          — attach many flights (ADR-0025)
 * - DELETE /api/missions/{id}/flights/{flight_id} — detach flight
 *
 * v2.73.0 (ADR-0025): adding flights is now multi-select. Tick the
 * checkboxes (or "select all") and press "ADD SELECTED (N)" to attach many
 * flights in ONE request — the old one-POST-per-click flow made building a
 * large mission ("savannah") painfully slow. The per-row ADD button is
 * preserved and routes through the same bulk endpoint with a single item.
 *
 * NEVER calls POST /api/missions — see constraint comment on
 * `attachFlights()` below.
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
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconRefresh, IconTrash } from '@tabler/icons-react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Aircraft, Mission, MissionFlight } from '../api/types';
import { cardStyle } from '../components/shared/styles';

/** Stable identity for an available flight row (native UUID or ODL id). */
function availKey(f: AvailableFlight): string {
  return String(f.id ?? f.flight_id ?? '');
}

interface AttachedFlight {
  /** mission_flight row id */
  _flightId: string;
  /** fleet aircraft (derived server-side from the flight log) */
  _aircraft: Aircraft | null;
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

function aircraftLabel(a: Aircraft | null, cacheDrone?: string): string {
  if (a?.model_name) return a.model_name;
  return cacheDrone || '—';
}

export default function MissionFlightsEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [flightsLoading, setFlightsLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [availableFlights, setAvailableFlights] = useState<AvailableFlight[]>([]);
  const [attached, setAttached] = useState<AttachedFlight[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const loadMission = useCallback(async () => {
    if (!id) return;
    try {
      const resp = await api.get<Mission>(`/missions/${id}`);
      const m = resp.data;
      const rows: AttachedFlight[] = m.flights.map((f) => ({
        ...(f.flight_data_cache || {}),
        _flightId: f.id,
        _aircraft: f.aircraft ?? null,
        flight_id: f.opendronelog_flight_id || (f.flight_data_cache?.id as string | undefined) || null,
      }));
      setAttached(rows);
    } catch (err) {
      // Surface the REAL HTTP status per the repo logging standard — the old
      // generic "Could not load mission flights" masked the 502/520 that the
      // detail-GET OOM produced (ADR-0025), making the failure undiagnosable
      // from the operator's screen alone.
      const status = (err as { response?: { status?: number } })?.response?.status;
      console.error('[MissionFlightsEdit] mission load failed', { status, err });
      notifications.show({
        title: 'Load failed',
        message: status
          ? `Could not load mission flights — server returned HTTP ${status}. Returning to mission list.`
          : 'Could not load mission flights — network error or timeout. Returning to mission list.',
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
        // Request the full library (backend caps at 2000) so >500 flights are
        // reachable from the editor — the old call passed no limit and the
        // backend default of 500 silently hid the rest (ADR-0025 §C).
        const resp = await api.get('/flight-library', { params: { limit: 2000 } });
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

  const attachFlights = async (flightsToAdd: AvailableFlight[]) => {
    // CONSTRAINT: this page edits an EXISTING mission only.
    // POST /missions is forbidden here per ADR-0013 / spec §2.
    // Only the per-mission flights subresource is touched — and now via the
    // single-request bulk endpoint (ADR-0025) for both the per-row ADD and
    // the multi-select "Add selected" action.
    if (!id || flightsToAdd.length === 0) return;
    const items = flightsToAdd.map((flight) => {
      const isNativeFlight =
        typeof flight.id === 'string' && flight.id.includes('-') && Boolean(flight.source);
      return {
        flight_id: isNativeFlight ? (flight.id as string) : null,
        opendronelog_flight_id: isNativeFlight
          ? null
          : String(flight.id ?? flight.flight_id ?? ''),
        flight_data_cache: flight,
      };
    });
    setAdding(true);
    try {
      const resp = await api.post(`/missions/${id}/flights/bulk`, { flights: items });
      const created: MissionFlight[] = Array.isArray(resp.data) ? resp.data : [];
      const newRows: AttachedFlight[] = created.map((f) => ({
        ...((f.flight_data_cache as Record<string, unknown>) || {}),
        _flightId: f.id,
        _aircraft: f.aircraft ?? null,
        flight_id:
          f.opendronelog_flight_id || (f.flight_data_cache?.id as string | undefined) || null,
      }));
      setAttached((prev) => [...prev, ...newRows]);
      setSelected(new Set());
      const n = newRows.length;
      const req = items.length;
      notifications.show({
        title: n > 0 ? 'Flights added' : 'Nothing to add',
        message:
          n === req
            ? `${n} flight${n === 1 ? '' : 's'} added.`
            : `${n} added · ${req - n} already attached (skipped).`,
        color: n > 0 ? 'cyan' : 'yellow',
      });
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      console.error('[MissionFlightsEdit] bulk add failed', { status, count: items.length, err });
      notifications.show({
        title: 'Error',
        message: `Failed to add flight${items.length === 1 ? '' : 's'}${
          status ? ` — server returned HTTP ${status}` : ' — network error or timeout'
        }.`,
        color: 'red',
      });
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveFlight = async (flightRowId: string) => {
    // CONSTRAINT: see attachFlights above — no POST /missions here either.
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
    const key = availKey(f);
    return key && !attachedKeys.has(key);
  });

  // Multi-select state (ADR-0025). Keys are availKey() of available rows.
  const visibleKeys = availableNotYetAttached.map(availKey).filter(Boolean);
  const selectedCount = visibleKeys.filter((k) => selected.has(k)).length;
  const allSelected = visibleKeys.length > 0 && selectedCount === visibleKeys.length;
  const someSelected = selectedCount > 0 && !allSelected;

  const toggleOne = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const toggleAll = () => setSelected(() => (allSelected ? new Set() : new Set(visibleKeys)));
  const handleAddSelected = () =>
    attachFlights(availableNotYetAttached.filter((f) => selected.has(availKey(f))));

  // Distinct aircraft summary, derived from attached flights. The operator
  // doesn't pick this; it's a read-only "what's on this mission" tally.
  const aircraftSummary = (() => {
    const seen = new Map<string, string>();
    const unmatched = new Set<string>();
    for (const f of attached) {
      if (f._aircraft) {
        seen.set(f._aircraft.id, f._aircraft.model_name);
      } else if (f.drone_model) {
        unmatched.add(String(f.drone_model));
      }
    }
    return {
      matched: Array.from(seen.values()),
      unmatched: Array.from(unmatched),
    };
  })();

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

      {/* Already-attached flights */}
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
            <Group gap="xs">
              {aircraftSummary.matched.map((name) => (
                <Badge key={name} color="cyan" variant="light" size="sm">
                  {name}
                </Badge>
              ))}
              {aircraftSummary.unmatched.map((name) => (
                <Badge
                  key={`u-${name}`}
                  color="yellow"
                  variant="light"
                  size="sm"
                  title="Not matched to a fleet aircraft — check serial/model on the Flights page."
                >
                  {name} (unmatched)
                </Badge>
              ))}
              <Badge color="gray" variant="light" size="sm">
                {attached.length} flight{attached.length === 1 ? '' : 's'}
              </Badge>
            </Group>
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
                    <Table.Th>AIRCRAFT</Table.Th>
                    <Table.Th>DURATION</Table.Th>
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
                      <Table.Td>
                        {aircraftLabel(f._aircraft, f.drone_model)}
                      </Table.Td>
                      <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {flightDuration(f as { duration_secs?: number })}
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
            <Group gap="xs">
              <Button
                size="xs"
                color="cyan"
                onClick={handleAddSelected}
                disabled={selectedCount === 0}
                loading={adding}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                ADD SELECTED ({selectedCount})
              </Button>
              <Badge color="gray" variant="light" size="sm">
                {availableNotYetAttached.length} available
              </Badge>
            </Group>
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
                    <Table.Th w={40}>
                      <Checkbox
                        size="xs"
                        color="cyan"
                        checked={allSelected}
                        indeterminate={someSelected}
                        onChange={toggleAll}
                        aria-label="Select all flights"
                      />
                    </Table.Th>
                    <Table.Th>NAME</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>DRONE</Table.Th>
                    <Table.Th>DURATION</Table.Th>
                    <Table.Th w={120}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {availableNotYetAttached.map((f, i) => {
                    const key = availKey(f) || String(i);
                    return (
                    <Table.Tr key={key}>
                      <Table.Td>
                        <Checkbox
                          size="xs"
                          color="cyan"
                          checked={selected.has(key)}
                          onChange={() => toggleOne(key)}
                          aria-label={`Select ${flightName(f)}`}
                        />
                      </Table.Td>
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
                          onClick={() => attachFlights([f])}
                          aria-label={`Add ${flightName(f)}`}
                        >
                          ADD
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                    );
                  })}
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
