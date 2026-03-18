import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  NumberInput,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCalendarEvent,
  IconPlus,
  IconRefresh,
  IconSettings,
  IconTool,
  IconTrash,
} from '@tabler/icons-react';
import api from '../api/client';
import { Aircraft, MaintenanceRecordType, MaintenanceAlert } from '../api/types';
import StatCard from '../components/shared/StatCard';
import { cardStyle, inputStyles, monoFont } from '../components/shared/styles';

const MAINTENANCE_TYPES = [
  { value: 'prop_replacement', label: 'Propeller Replacement' },
  { value: 'motor_inspection', label: 'Motor Inspection' },
  { value: 'firmware_update', label: 'Firmware Update' },
  { value: 'gimbal_calibration', label: 'Gimbal Calibration' },
  { value: 'sensor_calibration', label: 'Sensor Calibration' },
  { value: 'battery_check', label: 'Battery Check' },
  { value: 'airframe_inspection', label: 'Airframe Inspection' },
  { value: 'antenna_check', label: 'Antenna Check' },
  { value: 'general_service', label: 'General Service' },
  { value: 'other', label: 'Other' },
];

export default function Maintenance() {
  const [records, setRecords] = useState<MaintenanceRecordType[]>([]);
  const [alerts, setAlerts] = useState<MaintenanceAlert[]>([]);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({
    aircraft_id: '',
    maintenance_type: 'general_service',
    description: '',
    performed_at: new Date(),
    flight_hours_at: null as number | null,
    next_due_date: null as Date | null,
    cost: null as number | null,
    notes: '',
  });

  const loadData = async () => {
    setLoading(true);
    try {
      const [recordsResp, alertsResp, aircraftResp] = await Promise.all([
        api.get('/maintenance/records'),
        api.get('/maintenance/due'),
        api.get('/aircraft'),
      ]);
      setRecords(recordsResp.data);
      setAlerts(alertsResp.data);
      setAircraft(aircraftResp.data);
    } catch {
      // Silent fail — pages may not have data yet
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  const handleAdd = async () => {
    if (!form.aircraft_id) {
      notifications.show({ title: 'Error', message: 'Select an aircraft', color: 'red' });
      return;
    }
    try {
      await api.post('/maintenance/records', {
        ...form,
        performed_at: form.performed_at.toISOString().split('T')[0],
        next_due_date: form.next_due_date ? form.next_due_date.toISOString().split('T')[0] : null,
      });
      notifications.show({ title: 'Maintenance Logged', message: 'Record added successfully', color: 'cyan' });
      setAddOpen(false);
      setForm({ aircraft_id: '', maintenance_type: 'general_service', description: '', performed_at: new Date(), flight_hours_at: null, next_due_date: null, cost: null, notes: '' });
      loadData();
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to add record', color: 'red' });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/maintenance/records/${id}`);
      notifications.show({ title: 'Deleted', message: 'Record removed', color: 'orange' });
      setRecords((prev) => prev.filter((r) => r.id !== id));
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete', color: 'red' });
    }
  };

  const getAircraftName = (id: string) => {
    const a = aircraft.find((ac) => ac.id === id);
    return a ? `${a.manufacturer} ${a.model_name}` : 'Unknown';
  };

  const getTypeLabel = (type: string) => {
    return MAINTENANCE_TYPES.find((t) => t.value === type)?.label || type;
  };

  const overdueCount = alerts.filter((a) => a.overdue).length;
  const dueCount = alerts.filter((a) => !a.overdue).length;
  const totalCost = records.reduce((s, r) => s + (r.cost || 0), 0);

  return (
    <Stack gap="lg">
      <Group justify="space-between" wrap="wrap">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>MAINTENANCE</Title>
        <Group wrap="wrap">
          <Button leftSection={<IconPlus size={16} />} color="cyan" onClick={() => setAddOpen(true)}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            LOG MAINTENANCE
          </Button>
          <Button leftSection={<IconRefresh size={16} />} variant="subtle" color="gray" onClick={loadData} loading={loading}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            REFRESH
          </Button>
        </Group>
      </Group>

      {loading ? (
        <Group justify="center" py="xl"><Loader color="cyan" size="lg" /></Group>
      ) : (
        <>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <StatCard icon={IconTool} label="Total Records" value={String(records.length)} />
            <StatCard icon={IconAlertTriangle} label="Overdue" value={String(overdueCount)} color={overdueCount > 0 ? '#ff6b6b' : '#2ecc40'} />
            <StatCard icon={IconCalendarEvent} label="Due Soon" value={String(dueCount)} color={dueCount > 0 ? '#ffd43b' : '#2ecc40'} />
            <StatCard icon={IconSettings} label="Total Cost" value={totalCost > 0 ? `$${totalCost.toFixed(0)}` : '—'} />
          </SimpleGrid>

          {/* ── Alerts ─────────────────────────────────────────────── */}
          {alerts.length > 0 && (
            <Card padding="md" radius="md" style={{ ...cardStyle, borderColor: alerts.some(a => a.overdue) ? '#ff6b6b' : '#ffd43b' }}>
              <Text size="11px" c={alerts.some(a => a.overdue) ? '#ff6b6b' : '#ffd43b'} mb="sm"
                style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
                {alerts.some(a => a.overdue) ? 'OVERDUE MAINTENANCE' : 'UPCOMING MAINTENANCE'}
              </Text>
              <Stack gap={6}>
                {alerts.map((alert, i) => (
                  <Group key={i} justify="space-between" wrap="wrap" gap="xs">
                    <Group gap="sm" wrap="wrap">
                      <IconAlertTriangle size={16} color={alert.overdue ? '#ff6b6b' : '#ffd43b'} style={{ flexShrink: 0 }} />
                      <Text size="sm" c="#e8edf2">{getAircraftName(alert.aircraft_id)}</Text>
                      <Text size="sm" c="#5a6478">—</Text>
                      <Text size="sm" c="#e8edf2">{getTypeLabel(alert.maintenance_type)}</Text>
                    </Group>
                    <Badge
                      color={alert.overdue ? 'red' : 'yellow'}
                      variant="light"
                      size="sm"
                    >
                      {alert.overdue
                        ? `${Math.abs(alert.days_until)} DAYS OVERDUE`
                        : `DUE IN ${alert.days_until} DAYS`}
                    </Badge>
                  </Group>
                ))}
              </Stack>
            </Card>
          )}

          {/* ── Records Table ──────────────────────────────────────── */}
          {records.length === 0 ? (
            <Card padding="xl" radius="md" style={cardStyle}>
              <Stack align="center" gap="md">
                <IconTool size={48} color="#5a6478" />
                <Title order={3} c="#e8edf2">No Maintenance Records</Title>
                <Text c="#5a6478" ta="center" maw={400}>
                  Track propeller changes, firmware updates, inspections, and other maintenance for your fleet.
                </Text>
                <Button leftSection={<IconPlus size={16} />} color="cyan" onClick={() => setAddOpen(true)}>
                  Log First Maintenance
                </Button>
              </Stack>
            </Card>
          ) : (
            <Card padding="lg" radius="md" style={cardStyle}>
              <Text size="sm" c="#5a6478" mb="md" style={monoFont}>
                {records.length} RECORD{records.length !== 1 ? 'S' : ''}
              </Text>
              <ScrollArea type="auto">
              <Table
                highlightOnHover
                styles={{
                  table: { color: '#e8edf2' },
                  th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e', padding: '10px 12px', whiteSpace: 'nowrap' as const },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>AIRCRAFT</Table.Th>
                    <Table.Th>TYPE</Table.Th>
                    <Table.Th className="hide-mobile">DESCRIPTION</Table.Th>
                    <Table.Th className="hide-mobile">COST</Table.Th>
                    <Table.Th>NEXT DUE</Table.Th>
                    <Table.Th w={40}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {records.map((rec) => (
                    <Table.Tr key={rec.id}>
                      <Table.Td>
                        <Text size="xs" style={monoFont} c="#5a6478">{rec.performed_at}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" fw={500}>{getAircraftName(rec.aircraft_id)}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant="light" color="cyan" size="sm">{getTypeLabel(rec.maintenance_type)}</Badge>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" c="#5a6478" lineClamp={1}>{rec.description || rec.notes || '—'}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" style={monoFont} c={rec.cost ? '#e8edf2' : '#5a6478'}>
                          {rec.cost ? `$${rec.cost.toFixed(0)}` : '—'}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" style={monoFont} c="#5a6478">{rec.next_due_date || '—'}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Button size="compact-xs" variant="subtle" color="red" onClick={() => handleDelete(rec.id)}>
                          <IconTrash size={14} />
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              </ScrollArea>
            </Card>
          )}
        </>
      )}

      {/* ── Add Record Modal ──────────────────────────────────────── */}
      <Modal
        opened={addOpen}
        onClose={() => setAddOpen(false)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>LOG MAINTENANCE</Text>}
        size="md"
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
      >
        <Stack gap="md">
          <Select
            label="Aircraft"
            required
            data={aircraft.map((a) => ({ value: a.id, label: `${a.manufacturer} ${a.model_name}` }))}
            value={form.aircraft_id}
            onChange={(v) => setForm({ ...form, aircraft_id: v || '' })}
            styles={inputStyles}
          />
          <Select
            label="Maintenance Type"
            data={MAINTENANCE_TYPES}
            value={form.maintenance_type}
            onChange={(v) => setForm({ ...form, maintenance_type: v || 'general_service' })}
            styles={inputStyles}
          />
          <Textarea
            label="Description"
            placeholder="What was done?"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            styles={inputStyles}
          />
          <DateInput
            label="Date Performed"
            value={form.performed_at}
            onChange={(v) => setForm({ ...form, performed_at: v || new Date() })}
            styles={inputStyles}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <NumberInput
              label="Flight Hours At"
              placeholder="Optional"
              value={form.flight_hours_at || ''}
              onChange={(v) => setForm({ ...form, flight_hours_at: v ? Number(v) : null })}
              min={0}
              decimalScale={1}
              styles={inputStyles}
            />
            <NumberInput
              label="Cost ($)"
              placeholder="Optional"
              value={form.cost || ''}
              onChange={(v) => setForm({ ...form, cost: v ? Number(v) : null })}
              min={0}
              decimalScale={2}
              styles={inputStyles}
            />
          </SimpleGrid>
          <DateInput
            label="Next Due Date"
            placeholder="Optional"
            value={form.next_due_date}
            onChange={(v) => setForm({ ...form, next_due_date: v })}
            styles={inputStyles}
          />
          <Textarea
            label="Notes"
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            styles={inputStyles}
          />
          <Button fullWidth color="cyan" onClick={handleAdd}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            SAVE RECORD
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
