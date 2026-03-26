import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Drawer,
  FileButton,
  Group,
  Loader,
  Menu,
  Modal,
  NumberInput,
  Progress,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import { DateTimePicker, DateInput } from '@mantine/dates';
import { notifications } from '@mantine/notifications';
import {
  IconArrowsMaximize,
  IconBattery,
  IconBolt,
  IconChevronDown,
  IconChevronUp,
  IconClock,
  IconCloudUpload,
  IconDatabase,
  IconDotsVertical,
  IconDownload,
  IconDrone,
  IconEdit,
  IconMapPin,
  IconPlane,
  IconPlus,
  IconRefresh,
  IconRoute,
  IconRuler,
  IconSearch,
  IconSelector,
  IconTimeline,
  IconCalendar,
  IconFilter,
  IconFilterOff,
  IconTrash,
  IconUpload,
  IconX,
  IconPlayerPlay,
} from '@tabler/icons-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '../api/client';
import { Aircraft, FlightRecord } from '../api/types';
import FlightMap from '../components/FlightMap/FlightMap';
import StatCard from '../components/shared/StatCard';
import { cardStyle, inputStyles, monoFont } from '../components/shared/styles';

// ── Formatters ───────────────────────────────────────────────────────

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
  const feet = Number(meters) * 3.28084;
  if (feet >= 5280) return `${(feet / 5280).toFixed(2)} mi`;
  return `${Math.round(feet)} ft`;
}

function formatAltitude(meters: number | null | undefined): string {
  if (!meters && meters !== 0) return '—';
  return `${Math.round(Number(meters) * 3.28084)} ft`;
}

function formatSpeed(ms: number | null | undefined): string {
  if (!ms && ms !== 0) return '—';
  return `${(Number(ms) * 2.23694).toFixed(1)} mph`;
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

// ── Field accessors (handle both legacy ODL + native formats) ────────

function getDurationSecs(f: FlightRecord): number {
  return Number(f.duration_secs || f.durationSecs || f.duration || f.duration_seconds || 0);
}
function getTotalDistance(f: FlightRecord): number {
  return Number(f.total_distance || f.totalDistance || f.distance || f.distance_meters || 0);
}
function getMaxAltitude(f: FlightRecord): number {
  return Number(f.max_altitude || f.maxAltitude || f.max_alt || 0);
}
function getMaxSpeed(f: FlightRecord): number {
  return Number(f.max_speed || f.maxSpeed || 0);
}
function getDroneModel(f: FlightRecord): string {
  return f.drone_model || f.droneModel || f.drone || f.model || '';
}
function getDroneDisplay(f: FlightRecord): string {
  return getDroneModel(f) || '';
}
function getStartTime(f: FlightRecord): string {
  return f.start_time || f.startTime || f.date || f.created_at || '';
}
function getDisplayName(f: FlightRecord): string {
  return f.display_name || f.displayName || f.name || f.title || f.file_name || f.fileName || `Flight ${f.id ?? ''}`;
}
function getPointCount(f: FlightRecord): number {
  return Number(f.point_count || f.pointCount || 0);
}

const SOURCE_COLORS: Record<string, string> = {
  dji_txt: 'cyan',
  litchi_csv: 'green',
  airdata_csv: 'orange',
  manual: 'grape',
  opendronelog_import: 'blue',
};

const SOURCE_LABELS: Record<string, string> = {
  dji_txt: 'DJI',
  litchi_csv: 'Litchi',
  airdata_csv: 'Airdata',
  manual: 'Manual',
  opendronelog_import: 'ODL Import',
};

// ── Drone Breakdown ──────────────────────────────────────────────────

const DRONE_COLORS = ['#00d4ff', '#ff6b1a', '#2ecc40', '#ff6b6b', '#b57edc', '#ffd43b', '#20c997', '#ff8787'];

function DroneBreakdown({ flights }: { flights: FlightRecord[] }) {
  const drones = useMemo(() => {
    const map: Record<string, { count: number; duration: number }> = {};
    for (const f of flights) {
      const name = getDroneDisplay(f) || getDroneModel(f) || 'Unknown';
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
      <Text size="11px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
        FLIGHT TIME BY DRONE
      </Text>
      <Stack gap={8}>
        {drones.map((d) => {
          const pct = total > 0 ? (d.duration / total) * 100 : 0;
          return (
            <div key={d.name}>
              <Group justify="space-between" mb={2}>
                <Text size="xs" c="#e8edf2" fw={500}>{d.name}</Text>
                <Group gap={8}>
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

// ── Top Flights ──────────────────────────────────────────────────────

function TopFlights({ flights, label, accessor, formatter }: {
  flights: FlightRecord[]; label: string; accessor: (f: FlightRecord) => number; formatter: (v: number) => string;
}) {
  const top = useMemo(() => {
    return [...flights].sort((a, b) => accessor(b) - accessor(a)).slice(0, 3).filter((f) => accessor(f) > 0);
  }, [flights]);

  if (top.length === 0) return null;

  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Text size="11px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">{label}</Text>
      <Stack gap={8}>
        {top.map((f, i) => (
          <Group key={f.id ?? i} justify="space-between">
            <Group gap="xs">
              <Badge size="xs" color={i === 0 ? 'yellow' : 'gray'} variant="filled" w={20} style={{ textAlign: 'center' }}>{i + 1}</Badge>
              <Text size="xs" c="#e8edf2" lineClamp={1}>{getDisplayName(f)}</Text>
            </Group>
            <Text size="xs" c="#00d4ff" style={monoFont} fw={600}>{formatter(accessor(f))}</Text>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ══════════════════════════════════════════════════════════════════════

export default function Flights() {
  const [flights, setFlights] = useState<FlightRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [dateFrom, setDateFrom] = useState<Date | null>(null);
  const [dateTo, setDateTo] = useState<Date | null>(null);
  const [droneFilter, setDroneFilter] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [detailFlight, setDetailFlight] = useState<FlightRecord | null>(null);
  const [detailTrack, setDetailTrack] = useState<FlightRecord | null>(null);
  const [trackLoading, setTrackLoading] = useState(false);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Sort state
  const [sortBy, setSortBy] = useState<string>('date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Edit modal state (full flight edit including drone reassignment)
  const [editId, setEditId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ name: '', drone_model: '', aircraft_id: '' as string | null, notes: '' });

  // Manual flight form state
  const [manualForm, setManualForm] = useState({
    name: '', drone_model: '', duration_secs: 0, total_distance: 0,
    max_altitude: 0, max_speed: 0, notes: '', start_time: null as Date | null,
  });

  const loadFlights = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.get('/flight-library?limit=2000');
      setFlights(Array.isArray(resp.data) ? resp.data : []);
    } catch (err: any) {
      // Fallback: try legacy OpenDroneLog endpoint
      try {
        const resp = await api.get('/flights');
        let data: FlightRecord[] = [];
        if (Array.isArray(resp.data)) data = resp.data;
        else if (resp.data && typeof resp.data === 'object') {
          data = resp.data.flights || resp.data.data || resp.data.results || resp.data.items || [];
        }
        setFlights(data);
      } catch {
        setFlights([]);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadFlights(); }, [loadFlights]);

  // Open detail drawer if ?detail=<id> is in the URL (e.g. from dashboard recent flights)
  useEffect(() => {
    const detailId = searchParams.get('detail');
    if (detailId && flights.length > 0 && !detailFlight) {
      const match = flights.find((f) => String(f.id) === detailId);
      if (match) {
        setDetailFlight(match);
        // Clean up the URL param
        searchParams.delete('detail');
        setSearchParams(searchParams, { replace: true });
      }
    }
  }, [flights, searchParams, detailFlight, setSearchParams]);

  // Load fleet aircraft for reassignment
  useEffect(() => {
    api.get('/aircraft').then((r) => setAircraft(Array.isArray(r.data) ? r.data : [])).catch(() => {});
  }, []);

  // Fetch full flight detail (with gps_track) when drawer opens
  useEffect(() => {
    if (!detailFlight?.id) { setDetailTrack(null); return; }
    let cancelled = false;
    setTrackLoading(true);
    api.get(`/flight-library/${detailFlight.id}`)
      .then((r) => { if (!cancelled) setDetailTrack(r.data); })
      .catch(() => { if (!cancelled) setDetailTrack(null); })
      .finally(() => { if (!cancelled) setTrackLoading(false); });
    return () => { cancelled = true; };
  }, [detailFlight?.id]);

  // ── Upload handler ─────────────────────────────────────────────────
  const handleUpload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append('files', f));
      const resp = await api.post('/flight-library/upload', formData);
      const { imported, skipped, errors } = resp.data;
      notifications.show({
        title: 'Upload Complete',
        message: `${imported} imported, ${skipped} duplicates skipped${errors.length ? `, ${errors.length} errors` : ''}`,
        color: imported > 0 ? 'cyan' : 'yellow',
      });
      if (errors.length > 0) {
        errors.forEach((e: string) => notifications.show({ title: 'Parse Error', message: e, color: 'red' }));
      }
      loadFlights();
    } catch (err: any) {
      const msg = err.response?.data?.detail || 'Upload failed — is the flight-parser service running?';
      notifications.show({ title: 'Upload Error', message: msg, color: 'red' });
    } finally {
      setUploading(false);
    }
  };

  // ── Manual entry handler ───────────────────────────────────────────
  const handleManualSave = async () => {
    if (!manualForm.name.trim()) {
      notifications.show({ title: 'Error', message: 'Flight name is required', color: 'red' });
      return;
    }
    try {
      await api.post('/flight-library/manual', {
        ...manualForm,
        start_time: manualForm.start_time?.toISOString() || null,
      });
      notifications.show({ title: 'Flight Added', message: manualForm.name, color: 'cyan' });
      setManualOpen(false);
      setManualForm({ name: '', drone_model: '', duration_secs: 0, total_distance: 0, max_altitude: 0, max_speed: 0, notes: '', start_time: null });
      loadFlights();
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to save flight', color: 'red' });
    }
  };

  // ── Delete handler ─────────────────────────────────────────────────
  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/flight-library/${id}`);
      notifications.show({ title: 'Deleted', message: 'Flight removed', color: 'orange' });
      setFlights((prev) => prev.filter((f) => String(f.id) !== id));
      if (detailFlight && String(detailFlight.id) === id) setDetailFlight(null);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete flight', color: 'red' });
    }
  };

  // ── Edit handler (name, drone, aircraft assignment) ─────────────────
  const openEdit = (f: FlightRecord) => {
    setEditId(String(f.id));
    setEditForm({
      name: getDisplayName(f),
      drone_model: getDroneModel(f),
      aircraft_id: f.aircraft_id || null,
      notes: f.notes || '',
    });
  };

  const handleEditSave = async () => {
    if (!editId) return;
    try {
      const payload: Record<string, any> = {};
      if (editForm.name.trim()) payload.name = editForm.name.trim();
      payload.drone_model = editForm.drone_model.trim() || null;
      payload.aircraft_id = editForm.aircraft_id || null;
      payload.notes = editForm.notes.trim() || null;

      // If aircraft selected but no drone_model set, inherit from aircraft
      if (payload.aircraft_id && !payload.drone_model) {
        const ac = aircraft.find((a) => a.id === payload.aircraft_id);
        if (ac) payload.drone_model = ac.model_name;
      }

      await api.put(`/flight-library/${editId}`, payload);
      notifications.show({ title: 'Updated', message: 'Flight updated', color: 'cyan' });
      setFlights((prev) => prev.map((f) => String(f.id) === editId ? { ...f, ...payload } : f));
      if (detailFlight && String(detailFlight.id) === editId) {
        setDetailFlight({ ...detailFlight, ...payload });
      }
      setEditId(null);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to update flight', color: 'red' });
    }
  };

  // ── Export handler ─────────────────────────────────────────────────
  const handleExport = async (id: string, format: string, name: string) => {
    try {
      const resp = await api.get(`/flight-library/${id}/export/${format}`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([resp.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${name}.${format}`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {
      notifications.show({ title: 'Export Error', message: `Failed to export as ${format}`, color: 'red' });
    }
  };

  // ── Computed stats ─────────────────────────────────────────────────
  const stats = useMemo(() => {
    let totalDuration = 0, totalDistance = 0, maxAlt = 0, maxSpd = 0, totalPoints = 0;
    for (const f of flights) {
      totalDuration += getDurationSecs(f);
      totalDistance += getTotalDistance(f);
      const alt = getMaxAltitude(f);
      if (alt > maxAlt) maxAlt = alt;
      const spd = getMaxSpeed(f);
      if (spd > maxSpd) maxSpd = spd;
      totalPoints += getPointCount(f);
    }
    const n = flights.length;
    return { totalFlights: n, totalDuration, totalDistance, maxAlt, maxSpd, totalPoints, avgDuration: n ? totalDuration / n : 0, avgDistance: n ? totalDistance / n : 0 };
  }, [flights]);

  // Unique drone models for filter dropdown
  const droneOptions = useMemo(() => {
    const models = new Set<string>();
    for (const f of flights) {
      const model = getDroneModel(f);
      if (model) models.add(model);
    }
    return [...models].sort().map((m) => ({ value: m, label: m }));
  }, [flights]);

  const hasActiveFilters = !!search || !!dateFrom || !!dateTo || !!droneFilter;

  const clearAllFilters = () => {
    setSearch('');
    setDateFrom(null);
    setDateTo(null);
    setDroneFilter(null);
  };

  const filtered = useMemo(() => {
    let result = flights;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter((f) => {
        return [getDisplayName(f), getDroneModel(f), getStartTime(f), f.notes, f.drone_serial, f.source, f.original_filename]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
    }
    if (dateFrom) {
      const from = dateFrom.getTime();
      result = result.filter((f) => {
        const t = getStartTime(f);
        return t && new Date(t).getTime() >= from;
      });
    }
    if (dateTo) {
      // Include the entire "to" day
      const to = new Date(dateTo);
      to.setHours(23, 59, 59, 999);
      const toMs = to.getTime();
      result = result.filter((f) => {
        const t = getStartTime(f);
        return t && new Date(t).getTime() <= toMs;
      });
    }
    if (droneFilter) {
      result = result.filter((f) => getDroneModel(f) === droneFilter);
    }
    // Sort
    const sorted = [...result].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case 'source': cmp = (a.source || '').localeCompare(b.source || ''); break;
        case 'name': cmp = getDisplayName(a).localeCompare(getDisplayName(b)); break;
        case 'date': {
          const da = getStartTime(a) ? new Date(getStartTime(a)).getTime() : 0;
          const db2 = getStartTime(b) ? new Date(getStartTime(b)).getTime() : 0;
          cmp = da - db2; break;
        }
        case 'drone': cmp = getDroneDisplay(a).localeCompare(getDroneDisplay(b)); break;
        case 'duration': cmp = getDurationSecs(a) - getDurationSecs(b); break;
        case 'distance': cmp = getTotalDistance(a) - getTotalDistance(b); break;
        case 'altitude': cmp = getMaxAltitude(a) - getMaxAltitude(b); break;
        case 'speed': cmp = getMaxSpeed(a) - getMaxSpeed(b); break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [flights, search, dateFrom, dateTo, droneFilter, sortBy, sortDir]);

  const toggleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(col);
      setSortDir(col === 'name' || col === 'source' || col === 'drone' ? 'asc' : 'desc');
    }
  };

  const SortIcon = ({ col }: { col: string }) => {
    if (sortBy !== col) return <IconSelector size={12} style={{ opacity: 0.3 }} />;
    return sortDir === 'asc' ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />;
  };

  return (
    <Stack gap="lg">
      {/* ── Header ──────────────────────────────────────────────────── */}
      <Group justify="space-between" wrap="wrap">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>FLIGHTS</Title>
        <Group wrap="wrap">
          <FileButton onChange={(files) => handleUpload(files)} accept=".txt,.csv" multiple>
            {(props) => (
              <Button
                {...props}
                leftSection={<IconUpload size={16} />}
                variant="light"
                color="cyan"
                loading={uploading}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                UPLOAD LOGS
              </Button>
            )}
          </FileButton>
          <Button
            leftSection={<IconPlus size={16} />}
            variant="light"
            color="grape"
            onClick={() => setManualOpen(true)}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            MANUAL ENTRY
          </Button>
          <Button
            leftSection={<IconRefresh size={16} />}
            variant="subtle"
            color="gray"
            onClick={loadFlights}
            loading={loading}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            REFRESH
          </Button>
        </Group>
      </Group>

      {loading ? (
        <Group justify="center" py="xl">
          <Loader color="cyan" size="lg" />
          <Text c="#5a6478">Loading flight library...</Text>
        </Group>
      ) : flights.length === 0 ? (
        <Card padding="xl" radius="md" style={cardStyle}>
          <Stack align="center" gap="md">
            <IconDrone size={48} color="#5a6478" />
            <Title order={3} c="#e8edf2">No Flights Yet</Title>
            <Text c="#5a6478" ta="center" maw={400}>
              Upload DJI flight logs (.txt), Litchi/Airdata CSV files, or add flights manually.
              You can also import existing flights from OpenDroneLog in Settings.
            </Text>
            <Group>
              <FileButton onChange={(files) => handleUpload(files)} accept=".txt,.csv" multiple>
                {(props) => (
                  <Button {...props} leftSection={<IconCloudUpload size={16} />} color="cyan">
                    Upload Flight Logs
                  </Button>
                )}
              </FileButton>
              <Button leftSection={<IconPlus size={16} />} variant="light" color="grape" onClick={() => setManualOpen(true)}>
                Add Manual Flight
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : (
        <>
          {/* ── Summary Stats ───────────────────────────────────────── */}
          <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }}>
            <StatCard icon={IconPlane} label="Total Flights" value={stats.totalFlights.toLocaleString()} />
            <StatCard icon={IconRuler} label="Total Distance" value={formatDistance(stats.totalDistance)} />
            <StatCard icon={IconClock} label="Total Flight Time" value={formatDurationLong(stats.totalDuration)} />
            <StatCard icon={IconArrowsMaximize} label="Max Altitude" value={formatAltitude(stats.maxAlt)} color="#ff6b1a" />
            <StatCard icon={IconBolt} label="Max Speed" value={formatSpeed(stats.maxSpd)} color="#ff6b1a" />
          </SimpleGrid>

          <SimpleGrid cols={{ base: 2, sm: 3, md: 3 }}>
            <StatCard icon={IconRoute} label="Avg Distance / Flight" value={formatDistance(stats.avgDistance)} />
            <StatCard icon={IconTimeline} label="Avg Duration / Flight" value={formatDurationLong(stats.avgDuration)} />
            <StatCard icon={IconMapPin} label="Total Data Points" value={stats.totalPoints > 0 ? stats.totalPoints.toLocaleString() : '—'} />
          </SimpleGrid>

          {/* ── Drone Breakdown & Top Flights ───────────────────────── */}
          <SimpleGrid cols={{ base: 1, md: 3 }}>
            <DroneBreakdown flights={flights} />
            <TopFlights flights={flights} label="Longest Flights" accessor={getDurationSecs} formatter={(v) => formatDurationLong(v)} />
            <TopFlights flights={flights} label="Furthest Flights" accessor={getTotalDistance} formatter={(v) => formatDistance(v)} />
          </SimpleGrid>

          {/* ── Flight Table ────────────────────────────────────────── */}
          <Card padding="lg" radius="md" style={cardStyle}>
            <Stack gap="xs" mb="md">
              <Group justify="space-between" wrap="wrap">
                <Group gap="xs">
                  <Text size="sm" c="#5a6478" style={monoFont}>
                    {filtered.length} FLIGHT{filtered.length !== 1 ? 'S' : ''}
                  </Text>
                  {hasActiveFilters && (
                    <Badge size="xs" color="cyan" variant="light">{flights.length - filtered.length} filtered out</Badge>
                  )}
                </Group>
                <Group gap="xs" wrap="wrap">
                  <TextInput
                    placeholder="Search name, drone, notes..."
                    leftSection={<IconSearch size={14} />}
                    size="xs"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    rightSection={search ? <ActionIcon size="xs" variant="subtle" color="gray" onClick={() => setSearch('')}><IconX size={12} /></ActionIcon> : undefined}
                    styles={{ input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' }, root: { flex: '1 1 200px', maxWidth: 280 } }}
                  />
                  <Tooltip label={filtersOpen ? 'Hide filters' : 'Show filters'}>
                    <ActionIcon
                      variant={hasActiveFilters ? 'filled' : 'light'}
                      color="cyan"
                      size="md"
                      onClick={() => setFiltersOpen((v) => !v)}
                    >
                      <IconFilter size={16} />
                    </ActionIcon>
                  </Tooltip>
                  {hasActiveFilters && (
                    <Tooltip label="Clear all filters">
                      <ActionIcon variant="light" color="red" size="md" onClick={clearAllFilters}>
                        <IconFilterOff size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Group>
              </Group>
              {filtersOpen && (
                <Group gap="sm" wrap="wrap">
                  <DateInput
                    placeholder="From date"
                    value={dateFrom}
                    onChange={setDateFrom}
                    clearable
                    size="xs"
                    leftSection={<IconCalendar size={14} />}
                    maxDate={dateTo || undefined}
                    valueFormat="MMM D, YYYY"
                    styles={{ input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', width: 160 } }}
                    popoverProps={{ styles: { dropdown: { background: '#0e1117', borderColor: '#1a1f2e' } } }}
                  />
                  <DateInput
                    placeholder="To date"
                    value={dateTo}
                    onChange={setDateTo}
                    clearable
                    size="xs"
                    leftSection={<IconCalendar size={14} />}
                    minDate={dateFrom || undefined}
                    valueFormat="MMM D, YYYY"
                    styles={{ input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', width: 160 } }}
                    popoverProps={{ styles: { dropdown: { background: '#0e1117', borderColor: '#1a1f2e' } } }}
                  />
                  <Select
                    placeholder="All aircraft"
                    value={droneFilter}
                    onChange={setDroneFilter}
                    data={droneOptions}
                    clearable
                    searchable
                    size="xs"
                    leftSection={<IconDrone size={14} />}
                    styles={{ input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', width: 180 }, dropdown: { background: '#0e1117', borderColor: '#1a1f2e' } }}
                    nothingFoundMessage="No aircraft found"
                  />
                </Group>
              )}
            </Stack>

            <ScrollArea>
              <Table
                highlightOnHover
                styles={{
                  table: { color: '#e8edf2' },
                  th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e', padding: '10px 12px', whiteSpace: 'nowrap' },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    {([['source', 'SOURCE', false], ['name', 'NAME', false], ['date', 'DATE', false], ['drone', 'DRONE', false], ['duration', 'DURATION', false], ['distance', 'DISTANCE', true], ['altitude', 'MAX ALT', true], ['speed', 'MAX SPEED', true]] as const).map(([col, label, hideMobile]) => (
                      <Table.Th key={col} onClick={() => toggleSort(col)} style={{ cursor: 'pointer', userSelect: 'none' }} className={hideMobile ? 'hide-mobile' : ''}>
                        <Group gap={4} wrap="nowrap">
                          {label}<SortIcon col={col} />
                        </Group>
                      </Table.Th>
                    ))}
                    <Table.Th w={50}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {filtered.map((f, idx) => (
                    <Table.Tr
                      key={f.id ?? idx}
                      style={{ cursor: 'pointer' }}
                      onClick={() => setDetailFlight(f)}
                    >
                      <Table.Td>
                        <Badge size="xs" color={SOURCE_COLORS[f.source || ''] || 'gray'} variant="light">
                          {SOURCE_LABELS[f.source || ''] || f.source || '—'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" fw={500} lineClamp={1}>{getDisplayName(f)}</Text>
                        {f.original_filename && f.original_filename !== getDisplayName(f) && (
                          <Text size="10px" c="#5a6478" style={monoFont} lineClamp={1}>{f.original_filename}</Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatDate(getStartTime(f))}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#e8edf2">{getDroneDisplay(f) || '—'}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatDuration(getDurationSecs(f))}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatDistance(getTotalDistance(f))}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatAltitude(getMaxAltitude(f))}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" c="#5a6478" style={monoFont}>{formatSpeed(getMaxSpeed(f))}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Menu shadow="md" width={160} position="bottom-end">
                          <Menu.Target>
                            <ActionIcon variant="subtle" color="gray" size="sm" onClick={(e) => e.stopPropagation()}>
                              <IconDotsVertical size={14} />
                            </ActionIcon>
                          </Menu.Target>
                          <Menu.Dropdown styles={{ dropdown: { background: '#0e1117', borderColor: '#1a1f2e' } }}>
                            <Menu.Item leftSection={<IconEdit size={14} />} onClick={(e) => { e.stopPropagation(); openEdit(f); }}>
                              Edit Flight
                            </Menu.Item>
                            <Menu.Divider />
                            <Menu.Item leftSection={<IconDownload size={14} />} onClick={(e) => { e.stopPropagation(); handleExport(String(f.id), 'gpx', getDisplayName(f)); }}>
                              Export GPX
                            </Menu.Item>
                            <Menu.Item leftSection={<IconDownload size={14} />} onClick={(e) => { e.stopPropagation(); handleExport(String(f.id), 'kml', getDisplayName(f)); }}>
                              Export KML
                            </Menu.Item>
                            <Menu.Item leftSection={<IconDownload size={14} />} onClick={(e) => { e.stopPropagation(); handleExport(String(f.id), 'csv', getDisplayName(f)); }}>
                              Export CSV
                            </Menu.Item>
                            <Menu.Divider />
                            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={(e) => { e.stopPropagation(); handleDelete(String(f.id)); }}>
                              Delete
                            </Menu.Item>
                          </Menu.Dropdown>
                        </Menu>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </>
      )}

      {/* ── Flight Detail Drawer ──────────────────────────────────── */}
      <Drawer
        opened={!!detailFlight}
        onClose={() => setDetailFlight(null)}
        title={<Text fw={700} size="lg" c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>{detailFlight ? getDisplayName(detailFlight).toUpperCase() : ''}</Text>}
        position="right"
        size="xl"
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#050608' }, content: { background: '#050608' } }}
      >
        {detailFlight && (
          <Stack gap="md" pt="md">
            {/* ── Flight Path Map ── */}
            {(() => {
              const track = detailTrack?.gps_track;
              if (trackLoading) return (
                <Card padding="md" radius="sm" style={{ background: '#0e1117', border: '1px solid #1a1f2e', height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Loader size="sm" color="cyan" />
                </Card>
              );
              if (!track || !Array.isArray(track) || track.length < 2) return null;
              const coords = track.map((p: any) => [p.lng, p.lat]);
              const geojson = {
                type: 'FeatureCollection' as const,
                features: [
                  {
                    type: 'Feature',
                    geometry: { type: 'LineString', coordinates: coords },
                    properties: { flight_index: 0, color: '#00d4ff' },
                  },
                  {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: coords[0] },
                    properties: { type: 'start', flight_index: 0, color: '#2ecc40' },
                  },
                  {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: coords[coords.length - 1] },
                    properties: { type: 'end', flight_index: 0, color: '#ff6b1a' },
                  },
                ],
              };
              return <FlightMap geojson={geojson} height="260px" />;
            })()}

            {/* Replay button — only if flight has GPS track */}
            {detailTrack?.gps_track && Array.isArray(detailTrack.gps_track) && detailTrack.gps_track.length >= 2 && (
              <Button
                leftSection={<IconPlayerPlay size={16} />}
                color="cyan"
                variant="light"
                fullWidth
                onClick={() => { setDetailFlight(null); navigate(`/flights/${detailFlight.id}/replay`); }}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' } }}
              >
                FLIGHT REPLAY
              </Button>
            )}

            {/* Drone info — name, model, serial */}
            <Card padding="sm" radius="sm" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
              <Group gap="xs" mb={4}>
                <IconDrone size={16} color="#00d4ff" />
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>AIRCRAFT</Text>
              </Group>
              <Text c="#e8edf2" fw={600} size="lg">
                {getDroneDisplay(detailFlight) || 'Unknown'}
              </Text>
              {detailFlight.drone_serial && (
                <Text size="xs" c="#5a6478" style={monoFont}>S/N: {detailFlight.drone_serial}</Text>
              )}
            </Card>

            <SimpleGrid cols={2}>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DATE</Text>
                <Text c="#e8edf2" fw={600}>{formatDate(getStartTime(detailFlight))}</Text>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>SOURCE</Text>
                {detailFlight.source ? (
                  <Badge color={SOURCE_COLORS[detailFlight.source] || 'gray'} variant="light" size="sm" mt={4}>
                    {SOURCE_LABELS[detailFlight.source] || detailFlight.source}
                  </Badge>
                ) : <Text c="#5a6478">—</Text>}
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DURATION</Text>
                <Text c="#00d4ff" fw={700} size="xl" style={{ fontFamily: "'Bebas Neue', sans-serif" }}>
                  {formatDuration(getDurationSecs(detailFlight))}
                </Text>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DISTANCE</Text>
                <Text c="#00d4ff" fw={700} size="xl" style={{ fontFamily: "'Bebas Neue', sans-serif" }}>
                  {formatDistance(getTotalDistance(detailFlight))}
                </Text>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>MAX ALTITUDE</Text>
                <Text c="#ff6b1a" fw={700} size="xl" style={{ fontFamily: "'Bebas Neue', sans-serif" }}>
                  {formatAltitude(getMaxAltitude(detailFlight))}
                </Text>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>MAX SPEED</Text>
                <Text c="#ff6b1a" fw={700} size="xl" style={{ fontFamily: "'Bebas Neue', sans-serif" }}>
                  {formatSpeed(getMaxSpeed(detailFlight))}
                </Text>
              </div>
            </SimpleGrid>

            {/* Battery info */}
            {detailFlight.battery_serial && (
              <Card padding="sm" radius="sm" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
                <Group gap="xs" mb={4}>
                  <IconBattery size={16} color="#2ecc40" />
                  <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>BATTERY</Text>
                </Group>
                <Text c="#e8edf2" fw={500}>{detailFlight.battery_serial}</Text>
              </Card>
            )}

            {/* Home location */}
            {(detailFlight.home_lat && detailFlight.home_lon) && (
              <Card padding="sm" radius="sm" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
                <Group gap="xs" mb={4}>
                  <IconMapPin size={16} color="#ff6b1a" />
                  <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>HOME POINT</Text>
                </Group>
                <Text c="#e8edf2" size="sm" style={monoFont}>
                  {Number(detailFlight.home_lat).toFixed(6)}, {Number(detailFlight.home_lon).toFixed(6)}
                </Text>
              </Card>
            )}

            {detailFlight.original_filename && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>LOG FILE</Text>
                <Text c="#e8edf2" size="sm" style={monoFont}>{detailFlight.original_filename}</Text>
              </div>
            )}

            {detailFlight.notes && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>NOTES</Text>
                <Text c="#e8edf2" size="sm">{detailFlight.notes}</Text>
              </div>
            )}

            {detailFlight.point_count != null && detailFlight.point_count > 0 && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DATA POINTS</Text>
                <Text c="#e8edf2" size="sm" style={monoFont}>{Number(detailFlight.point_count).toLocaleString()}</Text>
              </div>
            )}

            <Button
              size="xs"
              variant="light"
              color="grape"
              leftSection={<IconEdit size={14} />}
              onClick={() => openEdit(detailFlight)}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              EDIT FLIGHT
            </Button>

            <Group>
              <Button size="xs" variant="light" color="cyan" leftSection={<IconDownload size={14} />}
                onClick={() => handleExport(String(detailFlight.id), 'gpx', getDisplayName(detailFlight))}>GPX</Button>
              <Button size="xs" variant="light" color="cyan" leftSection={<IconDownload size={14} />}
                onClick={() => handleExport(String(detailFlight.id), 'kml', getDisplayName(detailFlight))}>KML</Button>
              <Button size="xs" variant="light" color="cyan" leftSection={<IconDownload size={14} />}
                onClick={() => handleExport(String(detailFlight.id), 'csv', getDisplayName(detailFlight))}>CSV</Button>
            </Group>

            <Button
              color="red"
              variant="light"
              size="xs"
              leftSection={<IconTrash size={14} />}
              onClick={() => handleDelete(String(detailFlight.id))}
            >
              Delete Flight
            </Button>
          </Stack>
        )}
      </Drawer>

      {/* ── Edit Flight Modal (name, drone, assignment) ─────────── */}
      <Modal
        opened={!!editId}
        onClose={() => setEditId(null)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>EDIT FLIGHT</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
        size="md"
      >
        <Stack gap="md">
          <TextInput
            label="Flight Name"
            value={editForm.name}
            onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
            styles={inputStyles}
            autoFocus
          />
          <Select
            label="Assign to Fleet Aircraft"
            placeholder="Select aircraft from fleet..."
            data={aircraft.map((a) => ({ value: a.id, label: `${a.model_name} (${a.manufacturer})` }))}
            value={editForm.aircraft_id}
            onChange={(val) => {
              setEditForm({ ...editForm, aircraft_id: val });
              // Auto-fill drone model from fleet if empty
              if (val && !editForm.drone_model) {
                const ac = aircraft.find((a) => a.id === val);
                if (ac) setEditForm((prev) => ({ ...prev, aircraft_id: val, drone_model: ac.model_name }));
              }
            }}
            clearable
            searchable
            styles={{
              input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
              label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px', letterSpacing: '1px' },
              dropdown: { background: '#0e1117', borderColor: '#1a1f2e' },
              option: { color: '#e8edf2' },
            }}
          />
          <TextInput
            label="Drone Model"
            placeholder="e.g. DJI Matrice 300 RTK"
            value={editForm.drone_model}
            onChange={(e) => setEditForm({ ...editForm, drone_model: e.target.value })}
            styles={inputStyles}
          />
          <Textarea
            label="Notes"
            value={editForm.notes}
            onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })}
            styles={inputStyles}
          />
          <Button fullWidth color="cyan" onClick={handleEditSave}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE CHANGES
          </Button>
        </Stack>
      </Modal>

      {/* ── Manual Entry Modal ────────────────────────────────────── */}
      <Modal
        opened={manualOpen}
        onClose={() => setManualOpen(false)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>ADD MANUAL FLIGHT</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
        size="md"
      >
        <Stack gap="md">
          <TextInput
            label="Flight Name"
            placeholder="e.g. SAR Mission - Oak Park"
            required
            value={manualForm.name}
            onChange={(e) => setManualForm({ ...manualForm, name: e.target.value })}
            styles={inputStyles}
          />
          <TextInput
            label="Drone Model"
            placeholder="e.g. DJI Matrice 30T"
            value={manualForm.drone_model}
            onChange={(e) => setManualForm({ ...manualForm, drone_model: e.target.value })}
            styles={inputStyles}
          />
          <DateTimePicker
            label="Flight Date & Time"
            value={manualForm.start_time}
            onChange={(v) => setManualForm({ ...manualForm, start_time: v })}
            styles={inputStyles}
          />
          <SimpleGrid cols={{ base: 1, xs: 2 }}>
            <NumberInput
              label="Duration (minutes)"
              value={manualForm.duration_secs / 60}
              onChange={(v) => setManualForm({ ...manualForm, duration_secs: (Number(v) || 0) * 60 })}
              min={0}
              styles={inputStyles}
            />
            <NumberInput
              label="Distance (miles)"
              value={manualForm.total_distance / 1609.344}
              onChange={(v) => setManualForm({ ...manualForm, total_distance: (Number(v) || 0) * 1609.344 })}
              min={0}
              decimalScale={2}
              styles={inputStyles}
            />
            <NumberInput
              label="Max Altitude (feet)"
              value={manualForm.max_altitude * 3.28084}
              onChange={(v) => setManualForm({ ...manualForm, max_altitude: (Number(v) || 0) / 3.28084 })}
              min={0}
              styles={inputStyles}
            />
            <NumberInput
              label="Max Speed (mph)"
              value={manualForm.max_speed * 2.23694}
              onChange={(v) => setManualForm({ ...manualForm, max_speed: (Number(v) || 0) / 2.23694 })}
              min={0}
              decimalScale={1}
              styles={inputStyles}
            />
          </SimpleGrid>
          <Textarea
            label="Notes"
            value={manualForm.notes}
            onChange={(e) => setManualForm({ ...manualForm, notes: e.target.value })}
            styles={inputStyles}
          />
          <Button fullWidth color="cyan" onClick={handleManualSave}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE FLIGHT
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
