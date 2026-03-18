import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Progress,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
  Select,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconBattery,
  IconBattery1,
  IconBattery2,
  IconBattery3,
  IconBattery4,
  IconBatteryOff,
  IconPlus,
  IconRefresh,
  IconTrash,
} from '@tabler/icons-react';
import api from '../api/client';
import { BatteryRecord, BatteryLogRecord } from '../api/types';
import StatCard from '../components/shared/StatCard';
import { cardStyle, inputStyles, monoFont } from '../components/shared/styles';

const STATUS_COLORS: Record<string, string> = {
  active: 'green',
  service: 'yellow',
  retired: 'red',
};

function getBatteryIcon(pct: number | null) {
  if (pct === null) return IconBattery;
  if (pct >= 80) return IconBattery4;
  if (pct >= 60) return IconBattery3;
  if (pct >= 40) return IconBattery2;
  if (pct >= 20) return IconBattery1;
  return IconBatteryOff;
}

function getHealthColor(pct: number | null): string {
  if (pct === null) return '#5a6478';
  if (pct >= 80) return '#2ecc40';
  if (pct >= 60) return '#00d4ff';
  if (pct >= 40) return '#ffd43b';
  if (pct >= 20) return '#ff6b1a';
  return '#ff6b6b';
}

export default function Batteries() {
  const [batteries, setBatteries] = useState<BatteryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [selectedBattery, setSelectedBattery] = useState<BatteryRecord | null>(null);
  const [logs, setLogs] = useState<BatteryLogRecord[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [form, setForm] = useState({ serial: '', model: '', status: 'active', notes: '' });

  const loadBatteries = async () => {
    setLoading(true);
    try {
      const resp = await api.get('/batteries');
      setBatteries(resp.data);
    } catch {
      setBatteries([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadBatteries(); }, []);

  const loadLogs = async (batteryId: string) => {
    setLogsLoading(true);
    try {
      const resp = await api.get(`/batteries/${batteryId}/logs`);
      setLogs(resp.data);
    } catch {
      setLogs([]);
    } finally {
      setLogsLoading(false);
    }
  };

  const handleAdd = async () => {
    if (!form.serial.trim()) {
      notifications.show({ title: 'Error', message: 'Serial number is required', color: 'red' });
      return;
    }
    try {
      await api.post('/batteries', form);
      notifications.show({ title: 'Battery Added', message: form.serial, color: 'cyan' });
      setAddOpen(false);
      setForm({ serial: '', model: '', status: 'active', notes: '' });
      loadBatteries();
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to add', color: 'red' });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/batteries/${id}`);
      notifications.show({ title: 'Deleted', message: 'Battery removed', color: 'orange' });
      setBatteries((prev) => prev.filter((b) => b.id !== id));
      if (selectedBattery?.id === id) setSelectedBattery(null);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete', color: 'red' });
    }
  };

  const stats = useMemo(() => {
    const active = batteries.filter((b) => b.status === 'active').length;
    const totalCycles = batteries.reduce((s, b) => s + b.cycle_count, 0);
    const avgHealth = batteries.filter(b => b.health_pct !== null).length > 0
      ? batteries.filter(b => b.health_pct !== null).reduce((s, b) => s + (b.health_pct || 0), 0) / batteries.filter(b => b.health_pct !== null).length
      : null;
    return { total: batteries.length, active, totalCycles, avgHealth };
  }, [batteries]);

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>BATTERIES</Title>
        <Group>
          <Button leftSection={<IconPlus size={16} />} color="cyan" onClick={() => setAddOpen(true)}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            ADD BATTERY
          </Button>
          <Button leftSection={<IconRefresh size={16} />} variant="subtle" color="gray" onClick={loadBatteries} loading={loading}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            REFRESH
          </Button>
        </Group>
      </Group>

      {loading ? (
        <Group justify="center" py="xl"><Loader color="cyan" size="lg" /></Group>
      ) : batteries.length === 0 ? (
        <Card padding="xl" radius="md" style={cardStyle}>
          <Stack align="center" gap="md">
            <IconBattery size={48} color="#5a6478" />
            <Title order={3} c="#e8edf2">No Batteries Tracked</Title>
            <Text c="#5a6478" ta="center" maw={400}>
              Batteries are automatically tracked when you upload flight logs that contain battery serial numbers.
              You can also add batteries manually.
            </Text>
            <Button leftSection={<IconPlus size={16} />} color="cyan" onClick={() => setAddOpen(true)}>Add Battery</Button>
          </Stack>
        </Card>
      ) : (
        <>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <StatCard icon={IconBattery} label="Total Batteries" value={String(stats.total)} />
            <StatCard icon={IconBattery4} label="Active" value={String(stats.active)} color="#2ecc40" />
            <StatCard icon={IconRefresh} label="Total Cycles" value={stats.totalCycles.toLocaleString()} />
            <StatCard icon={IconBattery3} label="Avg Health" value={stats.avgHealth !== null ? `${Math.round(stats.avgHealth)}%` : '—'} color={getHealthColor(stats.avgHealth)} />
          </SimpleGrid>

          <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
            {batteries.map((bat) => {
              const Icon = getBatteryIcon(bat.health_pct);
              const healthColor = getHealthColor(bat.health_pct);
              return (
                <Card key={bat.id} padding="md" radius="md" style={{ ...cardStyle, cursor: 'pointer', transition: 'border-color 0.2s' }}
                  onClick={() => { setSelectedBattery(bat); loadLogs(bat.id); }}
                >
                  <Group justify="space-between" mb="sm">
                    <Group gap="sm">
                      <Icon size={24} color={healthColor} />
                      <div>
                        <Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px', fontSize: '18px' }}>
                          {bat.serial}
                        </Text>
                        <Text size="xs" c="#5a6478" style={monoFont}>{bat.model || 'Unknown Model'}</Text>
                      </div>
                    </Group>
                    <Badge color={STATUS_COLORS[bat.status] || 'gray'} variant="light" size="sm">
                      {bat.status.toUpperCase()}
                    </Badge>
                  </Group>

                  {bat.health_pct !== null && (
                    <div style={{ marginBottom: 8 }}>
                      <Group justify="space-between" mb={4}>
                        <Text size="xs" c="#5a6478" style={monoFont}>HEALTH</Text>
                        <Text size="xs" c={healthColor} fw={700} style={monoFont}>{Math.round(bat.health_pct)}%</Text>
                      </Group>
                      <Progress value={bat.health_pct} color={healthColor} size="sm" style={{ background: '#1a1f2e' }} />
                    </div>
                  )}

                  <Group justify="space-between">
                    <div>
                      <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>CYCLES</Text>
                      <Text c="#00d4ff" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '22px' }}>
                        {bat.cycle_count}
                      </Text>
                    </div>
                    {bat.last_voltage && (
                      <div>
                        <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>LAST VOLTAGE</Text>
                        <Text c="#e8edf2" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '22px' }}>
                          {bat.last_voltage.toFixed(1)}V
                        </Text>
                      </div>
                    )}
                  </Group>
                </Card>
              );
            })}
          </SimpleGrid>
        </>
      )}

      {/* ── Battery Detail Modal ──────────────────────────────────── */}
      <Modal
        opened={!!selectedBattery}
        onClose={() => setSelectedBattery(null)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>
          {selectedBattery?.serial} — BATTERY DETAIL
        </Text>}
        size="lg"
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
      >
        {selectedBattery && (
          <Stack gap="md">
            <SimpleGrid cols={3}>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>MODEL</Text>
                <Text c="#e8edf2">{selectedBattery.model || '—'}</Text>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>STATUS</Text>
                <Badge color={STATUS_COLORS[selectedBattery.status] || 'gray'} variant="light">{selectedBattery.status}</Badge>
              </div>
              <div>
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>CYCLES</Text>
                <Text c="#00d4ff" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '24px' }}>{selectedBattery.cycle_count}</Text>
              </div>
            </SimpleGrid>

            <Text size="sm" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>CHARGE HISTORY</Text>
            {logsLoading ? (
              <Group justify="center"><Loader color="cyan" size="sm" /></Group>
            ) : logs.length === 0 ? (
              <Text c="#5a6478" size="sm" ta="center">No charge logs recorded yet</Text>
            ) : (
              <Table
                styles={{
                  table: { color: '#e8edf2' },
                  th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e', padding: '8px' },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '6px 8px', fontSize: '13px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>START V</Table.Th>
                    <Table.Th>END V</Table.Th>
                    <Table.Th>MIN V</Table.Th>
                    <Table.Th>MAX TEMP</Table.Th>
                    <Table.Th>CYCLE #</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {logs.slice(0, 20).map((log) => (
                    <Table.Tr key={log.id}>
                      <Table.Td><Text size="xs" style={monoFont} c="#5a6478">{new Date(log.timestamp).toLocaleDateString()}</Text></Table.Td>
                      <Table.Td><Text size="xs" style={monoFont}>{log.start_voltage?.toFixed(1) || '—'}V</Text></Table.Td>
                      <Table.Td><Text size="xs" style={monoFont}>{log.end_voltage?.toFixed(1) || '—'}V</Text></Table.Td>
                      <Table.Td><Text size="xs" style={monoFont} c={log.min_voltage && log.min_voltage < 3.5 ? '#ff6b6b' : '#e8edf2'}>{log.min_voltage?.toFixed(1) || '—'}V</Text></Table.Td>
                      <Table.Td><Text size="xs" style={monoFont} c={log.max_temp && log.max_temp > 45 ? '#ff6b6b' : '#e8edf2'}>{log.max_temp ? `${log.max_temp.toFixed(0)}°C` : '—'}</Text></Table.Td>
                      <Table.Td><Text size="xs" style={monoFont} c="#5a6478">{log.cycles_at_time ?? '—'}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}

            <Button color="red" variant="light" size="xs" leftSection={<IconTrash size={14} />}
              onClick={() => { handleDelete(selectedBattery.id); setSelectedBattery(null); }}>
              Delete Battery
            </Button>
          </Stack>
        )}
      </Modal>

      {/* ── Add Battery Modal ─────────────────────────────────────── */}
      <Modal
        opened={addOpen}
        onClose={() => setAddOpen(false)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>ADD BATTERY</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
      >
        <Stack gap="md">
          <TextInput label="Serial Number" required placeholder="e.g. 1ZNDH3E0030E7C"
            value={form.serial} onChange={(e) => setForm({ ...form, serial: e.target.value })} styles={inputStyles} />
          <TextInput label="Model" placeholder="e.g. TB65"
            value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} styles={inputStyles} />
          <Select label="Status" data={[{ value: 'active', label: 'Active' }, { value: 'service', label: 'In Service' }, { value: 'retired', label: 'Retired' }]}
            value={form.status} onChange={(v) => setForm({ ...form, status: v || 'active' })} styles={inputStyles} />
          <Textarea label="Notes" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} styles={inputStyles} />
          <Button fullWidth color="cyan" onClick={handleAdd}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE BATTERY
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
