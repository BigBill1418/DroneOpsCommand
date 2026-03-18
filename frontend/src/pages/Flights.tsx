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
import { DateTimePicker } from '@mantine/dates';
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
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { FlightRecord } from '../api/types';
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
  return f.drone_model || f.droneModel || f.drone || f.aircraft || f.model || '';
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
  const [uploading, setUploading] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [detailFlight, setDetailFlight] = useState<FlightRecord | null>(null);
  const navigate = useNavigate();

  // Sort state
  const [sortBy, setSortBy] = useState<string>('date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Rename state
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameName, setRenameName] = useState('');

  // Manual flight form state
  const [manualForm, setManualForm] = useState({
    name: '', drone_model: '', duration_secs: 0, total_distance: 0,
    max_altitude: 0, max_speed: 0, notes: '', start_time: null as Date | null,
  });

  const loadFlights = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.get('/flight-library');
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

  // ── Rename handler ─────────────────────────────────────────────────
  const handleRename = async () => {
    if (!renameId || !renameName.trim()) return;
    try {
      await api.put(`/flight-library/${renameId}`, { name: renameName.trim() });
      notifications.show({ title: 'Renamed', message: `Flight renamed to "${renameName.trim()}"`, color: 'cyan' });
      setFlights((prev) => prev.map((f) => String(f.id) === renameId ? { ...f, name: renameName.trim() } : f));
      if (detailFlight && String(detailFlight.id) === renameId) {
        setDetailFlight({ ...detailFlight, name: renameName.trim() });
      }
      setRenameId(null);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to rename flight', color: 'red' });
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

  const filtered = useMemo(() => {
    let result = flights;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter((f) => {
        return [getDisplayName(f), getDroneModel(f), getStartTime(f), f.notes, f.drone_serial, f.source, f.original_filename]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
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
        case 'drone': cmp = getDroneModel(a).localeCompare(getDroneModel(b)); break;
        case 'duration': cmp = getDurationSecs(a) - getDurationSecs(b); break;
        case 'distance': cmp = getTotalDistance(a) - getTotalDistance(b); break;
        case 'altitude': cmp = getMaxAltitude(a) - getMaxAltitude(b); break;
        case 'speed': cmp = getMaxSpeed(a) - getMaxSpeed(b); break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [flights, search, sortBy, sortDir]);

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
            <Group justify="space-between" mb="md" wrap="wrap">
              <Text size="sm" c="#5a6478" style={monoFont}>
                {filtered.length} FLIGHT{filtered.length !== 1 ? 'S' : ''}
              </Text>
              <TextInput
                placeholder="Search flights..."
                leftSection={<IconSearch size={14} />}
                size="xs"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                styles={{ input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', minWidth: 200, maxWidth: 300 } }}
              />
            </Group>

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
                        <Text size="xs" c="#e8edf2">{getDroneModel(f) || '—'}</Text>
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
                            <Menu.Item leftSection={<IconEdit size={14} />} onClick={(e) => { e.stopPropagation(); setRenameId(String(f.id)); setRenameName(getDisplayName(f)); }}>
                              Edit Name
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
            <SimpleGrid cols={2}>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DRONE</Text>
                <Text c="#e8edf2" fw={600}>{getDroneModel(detailFlight) || '—'}</Text>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DATE</Text>
                <Text c="#e8edf2" fw={600}>{formatDate(getStartTime(detailFlight))}</Text>
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

            {detailFlight.source && (
              <Group gap="xs">
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>SOURCE</Text>
                <Badge color={SOURCE_COLORS[detailFlight.source] || 'gray'} variant="light" size="sm">
                  {SOURCE_LABELS[detailFlight.source] || detailFlight.source}
                </Badge>
              </Group>
            )}

            {detailFlight.original_filename && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>LOG FILE</Text>
                <Text c="#e8edf2" size="sm" style={monoFont}>{detailFlight.original_filename}</Text>
              </div>
            )}

            {detailFlight.drone_serial && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>SERIAL</Text>
                <Text c="#e8edf2" size="sm" style={monoFont}>{detailFlight.drone_serial}</Text>
              </div>
            )}

            {detailFlight.battery_serial && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>BATTERY</Text>
                <Text c="#e8edf2" size="sm" style={monoFont}>{detailFlight.battery_serial}</Text>
              </div>
            )}

            {detailFlight.notes && (
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>NOTES</Text>
                <Text c="#e8edf2" size="sm">{detailFlight.notes}</Text>
              </div>
            )}

            <Button
              size="xs"
              variant="light"
              color="grape"
              leftSection={<IconEdit size={14} />}
              onClick={() => { setRenameId(String(detailFlight.id)); setRenameName(getDisplayName(detailFlight)); }}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              EDIT NAME
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

      {/* ── Rename Modal ──────────────────────────────────────────── */}
      <Modal
        opened={!!renameId}
        onClose={() => setRenameId(null)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>EDIT FLIGHT NAME</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
        size="sm"
      >
        <Stack gap="md">
          <TextInput
            label="Flight Name"
            value={renameName}
            onChange={(e) => setRenameName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleRename(); } }}
            styles={inputStyles}
            autoFocus
          />
          <Button fullWidth color="cyan" onClick={handleRename}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE NAME
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
