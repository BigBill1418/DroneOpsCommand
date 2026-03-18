import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Progress,
  ScrollArea,
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
  IconChevronDown,
  IconChevronUp,
  IconEdit,
  IconPlus,
  IconRefresh,
  IconSelector,
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
  const [editBattery, setEditBattery] = useState<BatteryRecord | null>(null);
  const [editForm, setEditForm] = useState({ serial: '', model: '' });
  const [sortBy, setSortBy] = useState<string>('model');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [droneModels, setDroneModels] = useState<string[]>([]);

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

  const loadDroneModels = async () => {
    try {
      const resp = await api.get('/aircraft');
      const models = (resp.data as { model_name: string }[]).map((a) => a.model_name).filter(Boolean);
      setDroneModels(models);
    } catch {
      setDroneModels([]);
    }
  };

  useEffect(() => { loadBatteries(); loadDroneModels(); }, []);

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

  const handleEditSave = async () => {
    if (!editBattery || !editForm.serial.trim()) return;
    try {
      const resp = await api.put(`/batteries/${editBattery.id}`, {
        serial: editForm.serial.trim(),
        model: editForm.model.trim() || null,
      });
      notifications.show({ title: 'Updated', message: `Battery updated`, color: 'cyan' });
      setBatteries((prev) => prev.map((b) => b.id === editBattery.id ? resp.data : b));
      if (selectedBattery?.id === editBattery.id) setSelectedBattery(resp.data);
      setEditBattery(null);
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to update battery', color: 'red' });
    }
  };

  const stats = useMemo(() => {
    const active = batteries.filter((b) => b.status === 'active').length;
    const totalCycles = batteries.reduce((s, b) => s + b.cycle_count, 0);
    const withHealth = batteries.filter(b => b.health_pct !== null);
    const avgHealth = withHealth.length > 0
      ? withHealth.reduce((s, b) => s + (b.health_pct || 0), 0) / withHealth.length
      : null;
    return { total: batteries.length, active, totalCycles, avgHealth };
  }, [batteries]);

  // Group batteries by drone model for display
  const grouped = useMemo(() => {
    const sorted = [...batteries].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case 'serial': cmp = a.serial.localeCompare(b.serial); break;
        case 'model': cmp = (a.model || 'Unknown').localeCompare(b.model || 'Unknown'); break;
        case 'status': cmp = a.status.localeCompare(b.status); break;
        case 'health': cmp = (a.health_pct ?? -1) - (b.health_pct ?? -1); break;
        case 'cycles': cmp = a.cycle_count - b.cycle_count; break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });

    const map = new Map<string, BatteryRecord[]>();
    for (const bat of sorted) {
      const key = bat.model || 'Unknown';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(bat);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [batteries, sortBy, sortDir]);

  const toggleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(col);
      setSortDir(col === 'serial' || col === 'model' || col === 'status' ? 'asc' : 'desc');
    }
  };

  const SortIcon = ({ col }: { col: string }) => {
    if (sortBy !== col) return <IconSelector size={12} style={{ opacity: 0.3 }} />;
    return sortDir === 'asc' ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />;
  };

  // Build drone model options for Select dropdowns
  const droneModelOptions = useMemo(() => {
    // Combine configured aircraft models with any existing battery models not in the list
    const allModels = new Set(droneModels);
    for (const b of batteries) {
      if (b.model && b.model !== 'Unknown') allModels.add(b.model);
    }
    return Array.from(allModels).sort().map((m) => ({ value: m, label: m }));
  }, [droneModels, batteries]);

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

          <Card padding="lg" radius="md" style={cardStyle}>
            <Text size="sm" c="#5a6478" style={monoFont} mb="md">
              {batteries.length} BATTER{batteries.length !== 1 ? 'IES' : 'Y'} — {grouped.length} DRONE TYPE{grouped.length !== 1 ? 'S' : ''}
            </Text>

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
                    {([['serial', 'BATTERY'], ['model', 'DRONE TYPE'], ['status', 'STATUS'], ['health', 'HEALTH'], ['cycles', 'CYCLES']] as const).map(([col, label]) => (
                      <Table.Th key={col} onClick={() => toggleSort(col)} style={{ cursor: 'pointer', userSelect: 'none' }}>
                        <Group gap={4} wrap="nowrap">
                          {label}<SortIcon col={col} />
                        </Group>
                      </Table.Th>
                    ))}
                    <Table.Th w={80}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {grouped.map(([model, bats]) => (
                    <>
                      <Table.Tr key={`group-${model}`} style={{ background: 'rgba(0, 212, 255, 0.03)' }}>
                        <Table.Td colSpan={6} style={{ borderBottom: '1px solid #1a1f2e', padding: '6px 12px' }}>
                          <Text size="xs" fw={700} c="#00d4ff" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '14px' }}>
                            {model.toUpperCase()} — {bats.length} batter{bats.length !== 1 ? 'ies' : 'y'}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                      {bats.map((bat) => {
                        const healthColor = getHealthColor(bat.health_pct);
                        return (
                          <Table.Tr
                            key={bat.id}
                            style={{ cursor: 'pointer' }}
                            onClick={() => { setSelectedBattery(bat); loadLogs(bat.id); }}
                          >
                            <Table.Td>
                              <Text size="sm" fw={600} c="#e8edf2" style={monoFont}>{bat.serial}</Text>
                            </Table.Td>
                            <Table.Td>
                              <Text size="xs" c="#5a6478" style={monoFont}>{bat.model || 'Unknown'}</Text>
                            </Table.Td>
                            <Table.Td>
                              <Badge color={STATUS_COLORS[bat.status] || 'gray'} variant="light" size="sm">
                                {bat.status.toUpperCase()}
                              </Badge>
                            </Table.Td>
                            <Table.Td>
                              {bat.health_pct !== null ? (
                                <Group gap={8} wrap="nowrap">
                                  <Progress value={bat.health_pct} color={healthColor} size="sm" style={{ background: '#1a1f2e', flex: 1, minWidth: 60 }} />
                                  <Text size="xs" c={healthColor} fw={700} style={monoFont} w={40} ta="right">{Math.round(bat.health_pct)}%</Text>
                                </Group>
                              ) : (
                                <Text size="xs" c="#5a6478" style={monoFont}>—</Text>
                              )}
                            </Table.Td>
                            <Table.Td>
                              <Text size="sm" c="#00d4ff" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '18px' }}>
                                {bat.cycle_count}
                              </Text>
                            </Table.Td>
                            <Table.Td>
                              <Group gap={4} wrap="nowrap">
                                <Button
                                  variant="subtle"
                                  color="grape"
                                  size="compact-xs"
                                  onClick={(e) => { e.stopPropagation(); setEditBattery(bat); setEditForm({ serial: bat.serial, model: bat.model || '' }); }}
                                >
                                  <IconEdit size={14} />
                                </Button>
                                <Button
                                  variant="subtle"
                                  color="red"
                                  size="compact-xs"
                                  onClick={(e) => { e.stopPropagation(); handleDelete(bat.id); }}
                                >
                                  <IconTrash size={14} />
                                </Button>
                              </Group>
                            </Table.Td>
                          </Table.Tr>
                        );
                      })}
                    </>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </>
      )}

      {/* Battery Detail Modal */}
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
                <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }}>DRONE TYPE</Text>
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

            <Group>
              <Button
                size="xs"
                variant="light"
                color="grape"
                leftSection={<IconEdit size={14} />}
                onClick={() => { setEditBattery(selectedBattery); setEditForm({ serial: selectedBattery.serial, model: selectedBattery.model || '' }); }}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                EDIT BATTERY
              </Button>
              <Button color="red" variant="light" size="xs" leftSection={<IconTrash size={14} />}
                onClick={() => { handleDelete(selectedBattery.id); setSelectedBattery(null); }}>
                Delete Battery
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      {/* Add Battery Modal */}
      <Modal
        opened={addOpen}
        onClose={() => setAddOpen(false)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>ADD BATTERY</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
      >
        <Stack gap="md">
          <TextInput label="Serial Number" required placeholder="e.g. 1ZNDH3E0030E7C"
            value={form.serial} onChange={(e) => setForm({ ...form, serial: e.target.value })} styles={inputStyles} />
          <Select
            label="Drone Type"
            placeholder="Select drone model"
            data={droneModelOptions}
            value={form.model || null}
            onChange={(v) => setForm({ ...form, model: v || '' })}
            searchable
            clearable
            styles={inputStyles}
          />
          <Select label="Status" data={[{ value: 'active', label: 'Active' }, { value: 'service', label: 'In Service' }, { value: 'retired', label: 'Retired' }]}
            value={form.status} onChange={(v) => setForm({ ...form, status: v || 'active' })} styles={inputStyles} />
          <Textarea label="Notes" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} styles={inputStyles} />
          <Button fullWidth color="cyan" onClick={handleAdd}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE BATTERY
          </Button>
        </Stack>
      </Modal>

      {/* Edit Battery Modal */}
      <Modal
        opened={!!editBattery}
        onClose={() => setEditBattery(null)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>EDIT BATTERY</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
        size="sm"
      >
        <Stack gap="md">
          <TextInput
            label="Battery Name / Serial"
            value={editForm.serial}
            onChange={(e) => setEditForm({ ...editForm, serial: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleEditSave(); } }}
            styles={inputStyles}
            autoFocus
          />
          <Select
            label="Drone Type"
            placeholder="Select drone model"
            data={droneModelOptions}
            value={editForm.model || null}
            onChange={(v) => setEditForm({ ...editForm, model: v || '' })}
            searchable
            clearable
            styles={inputStyles}
          />
          <Button fullWidth color="cyan" onClick={handleEditSave}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE CHANGES
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
