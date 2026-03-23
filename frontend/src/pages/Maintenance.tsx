import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  FileButton,
  Group,
  Image,
  Loader,
  Modal,
  MultiSelect,
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
  IconCamera,
  IconCheck,
  IconEdit,
  IconPhoto,
  IconPlus,
  IconRefresh,
  IconSettings,
  IconTool,
  IconTrash,
  IconUpload,
  IconX,
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
  const [selectedRecord, setSelectedRecord] = useState<MaintenanceRecordType | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<{
    aircraft_id: string;
    maintenance_types: string[];
    description: string;
    performed_at: Date;
    flight_hours_at: number | null;
    next_due_hours: number | null;
    next_due_date: Date | null;
    cost: number | null;
    notes: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [form, setForm] = useState({
    aircraft_id: '',
    maintenance_types: ['general_service'] as string[],
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
    if (form.maintenance_types.length === 0) {
      notifications.show({ title: 'Error', message: 'Select at least one maintenance type', color: 'red' });
      return;
    }
    try {
      await api.post('/maintenance/records', {
        aircraft_id: form.aircraft_id,
        maintenance_type: form.maintenance_types.join(','),
        description: form.description,
        performed_at: form.performed_at.toISOString().split('T')[0],
        flight_hours_at: form.flight_hours_at,
        next_due_date: form.next_due_date ? form.next_due_date.toISOString().split('T')[0] : null,
        cost: form.cost,
        notes: form.notes,
      });
      notifications.show({ title: 'Maintenance Logged', message: 'Record added successfully', color: 'cyan' });
      setAddOpen(false);
      setForm({ aircraft_id: '', maintenance_types: ['general_service'], description: '', performed_at: new Date(), flight_hours_at: null, next_due_date: null, cost: null, notes: '' });
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
      if (selectedRecord?.id === id) setSelectedRecord(null);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete', color: 'red' });
    }
  };

  const handleImageUpload = async (file: File | null) => {
    if (!file) return;
    uploadFiles([file]);
  };

  const handleImageDelete = async (idx: number) => {
    if (!selectedRecord) return;
    try {
      const resp = await api.delete(`/maintenance/records/${selectedRecord.id}/images/${idx}`);
      setSelectedRecord(resp.data);
      setRecords((prev) => prev.map((r) => r.id === resp.data.id ? resp.data : r));
      notifications.show({ title: 'Removed', message: 'Image deleted', color: 'orange' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to remove image', color: 'red' });
    }
  };

  const startEditing = (rec: MaintenanceRecordType) => {
    setEditForm({
      aircraft_id: rec.aircraft_id,
      maintenance_types: rec.maintenance_type.split(',').map((t) => t.trim()).filter(Boolean),
      description: rec.description || '',
      performed_at: new Date(rec.performed_at + 'T00:00:00'),
      flight_hours_at: rec.flight_hours_at,
      next_due_hours: rec.next_due_hours,
      next_due_date: rec.next_due_date ? new Date(rec.next_due_date + 'T00:00:00') : null,
      cost: rec.cost,
      notes: rec.notes || '',
    });
    setEditing(true);
  };

  const cancelEditing = () => {
    setEditing(false);
    setEditForm(null);
  };

  const handleSaveEdit = async () => {
    if (!selectedRecord || !editForm) return;
    if (editForm.maintenance_types.length === 0) {
      notifications.show({ title: 'Error', message: 'Select at least one maintenance type', color: 'red' });
      return;
    }
    setSaving(true);
    try {
      const resp = await api.put(`/maintenance/records/${selectedRecord.id}`, {
        aircraft_id: editForm.aircraft_id,
        maintenance_type: editForm.maintenance_types.join(','),
        description: editForm.description || null,
        performed_at: editForm.performed_at.toISOString().split('T')[0],
        flight_hours_at: editForm.flight_hours_at,
        next_due_hours: editForm.next_due_hours,
        next_due_date: editForm.next_due_date ? editForm.next_due_date.toISOString().split('T')[0] : null,
        cost: editForm.cost,
        notes: editForm.notes || null,
      });
      setSelectedRecord(resp.data);
      setRecords((prev) => prev.map((r) => r.id === resp.data.id ? resp.data : r));
      setEditing(false);
      setEditForm(null);
      notifications.show({ title: 'Updated', message: 'Record saved', color: 'cyan' });
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to update', color: 'red' });
    } finally {
      setSaving(false);
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (!selectedRecord || files.length === 0) return;
    setUploading(true);
    let latest = selectedRecord;
    for (const file of files) {
      if (file.size > 10_000_000) {
        notifications.show({ title: 'Skipped', message: `${file.name} exceeds 10MB limit`, color: 'orange' });
        continue;
      }
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
        notifications.show({ title: 'Skipped', message: `${file.name} — only JPEG, PNG, WebP allowed`, color: 'orange' });
        continue;
      }
      try {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await api.post(`/maintenance/records/${selectedRecord.id}/images`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        latest = resp.data;
      } catch {
        notifications.show({ title: 'Failed', message: `Could not upload ${file.name}`, color: 'red' });
      }
    }
    setSelectedRecord(latest);
    setRecords((prev) => prev.map((r) => r.id === latest.id ? latest : r));
    if (files.length > 0) {
      notifications.show({ title: 'Uploaded', message: `${files.length} photo(s) processed`, color: 'cyan' });
    }
    setUploading(false);
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const items = e.dataTransfer.files;
    if (!items || items.length === 0) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      files.push(items[i]);
    }
    uploadFiles(files);
  }, [selectedRecord]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const getAircraftName = (id: string) => {
    const a = aircraft.find((ac) => ac.id === id);
    return a ? `${a.manufacturer} ${a.model_name}` : 'Unknown';
  };

  const getAircraftSerial = (id: string) => {
    const a = aircraft.find((ac) => ac.id === id);
    return a?.serial_number || null;
  };

  const getTypeLabel = (type: string) => {
    return MAINTENANCE_TYPES.find((t) => t.value === type)?.label || type;
  };

  const getTypeLabels = (typeStr: string) => {
    return typeStr.split(',').map((t) => t.trim()).filter(Boolean);
  };

  const overdueCount = alerts.filter((a) => a.overdue).length;
  const dueCount = alerts.filter((a) => !a.overdue).length;
  const totalCost = records.reduce((s, r) => s + (r.cost || 0), 0);

  const modalStyles = {
    header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' },
    body: { background: '#0e1117' },
    content: { background: '#0e1117' },
  };

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

          {/* Alerts */}
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
                    <Badge color={alert.overdue ? 'red' : 'yellow'} variant="light" size="sm">
                      {alert.overdue
                        ? `${Math.abs(alert.days_until)} DAYS OVERDUE`
                        : `DUE IN ${alert.days_until} DAYS`}
                    </Badge>
                  </Group>
                ))}
              </Stack>
            </Card>
          )}

          {/* Records Table */}
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
                  td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px', cursor: 'pointer' },
                  tr: { '&:hover': { background: '#141922' } },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>AIRCRAFT</Table.Th>
                    <Table.Th className="hide-mobile">S/N</Table.Th>
                    <Table.Th>TYPE</Table.Th>
                    <Table.Th className="hide-mobile">DESCRIPTION</Table.Th>
                    <Table.Th className="hide-mobile">COST</Table.Th>
                    <Table.Th className="hide-mobile">NEXT DUE</Table.Th>
                    <Table.Th className="hide-mobile" w={30}><IconPhoto size={14} color="#5a6478" /></Table.Th>
                    <Table.Th w={40}></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {records.map((rec) => (
                    <Table.Tr key={rec.id} onClick={() => setSelectedRecord(rec)} style={{ cursor: 'pointer' }}>
                      <Table.Td>
                        <Text size="xs" style={monoFont} c="#5a6478">{rec.performed_at}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" fw={500}>{getAircraftName(rec.aircraft_id)}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" style={monoFont} c="#5a6478">{getAircraftSerial(rec.aircraft_id) || '—'}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="wrap">
                          {getTypeLabels(rec.maintenance_type).map((t) => (
                            <Badge key={t} variant="light" color="cyan" size="sm">{getTypeLabel(t)}</Badge>
                          ))}
                        </Group>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" c="#5a6478" lineClamp={1}>{rec.description || rec.notes || '—'}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" style={monoFont} c={rec.cost ? '#e8edf2' : '#5a6478'}>
                          {rec.cost ? `$${rec.cost.toFixed(0)}` : '—'}
                        </Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        <Text size="xs" style={monoFont} c="#5a6478">{rec.next_due_date || '—'}</Text>
                      </Table.Td>
                      <Table.Td className="hide-mobile">
                        {(rec.images?.length ?? 0) > 0 && (
                          <Badge variant="light" color="gray" size="xs">{rec.images!.length}</Badge>
                        )}
                      </Table.Td>
                      <Table.Td onClick={(e) => e.stopPropagation()}>
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

      {/* Detail / Edit Modal */}
      <Modal
        opened={!!selectedRecord}
        onClose={() => { setSelectedRecord(null); cancelEditing(); }}
        title={
          <Group gap="sm">
            <Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>
              {editing ? 'EDIT MAINTENANCE' : 'MAINTENANCE DETAIL'}
            </Text>
          </Group>
        }
        size="lg"
        styles={modalStyles}
      >
        {selectedRecord && (() => {
          const rec = selectedRecord;
          const acName = getAircraftName(rec.aircraft_id);
          const serial = getAircraftSerial(rec.aircraft_id);
          const images = rec.images || [];

          // ── Edit Mode ──
          if (editing && editForm) {
            return (
              <Stack gap="md">
                <Select
                  label="Aircraft"
                  required
                  data={aircraft.map((a) => ({ value: a.id, label: `${a.manufacturer} ${a.model_name}${a.serial_number ? ` (S/N: ${a.serial_number})` : ''}` }))}
                  value={editForm.aircraft_id}
                  onChange={(v) => setEditForm({ ...editForm, aircraft_id: v || '' })}
                  styles={inputStyles}
                />
                <MultiSelect
                  label="Maintenance Type(s)"
                  data={MAINTENANCE_TYPES}
                  value={editForm.maintenance_types}
                  onChange={(v) => setEditForm({ ...editForm, maintenance_types: v.length > 0 ? v : ['general_service'] })}
                  styles={inputStyles}
                />
                <Textarea
                  label="Description"
                  placeholder="What was done?"
                  value={editForm.description}
                  onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                  styles={inputStyles}
                />
                <DateInput
                  label="Date Performed"
                  value={editForm.performed_at}
                  onChange={(v) => setEditForm({ ...editForm, performed_at: v || new Date() })}
                  styles={inputStyles}
                />
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <NumberInput
                    label="Flight Hours At"
                    placeholder="Optional"
                    value={editForm.flight_hours_at ?? ''}
                    onChange={(v) => setEditForm({ ...editForm, flight_hours_at: v ? Number(v) : null })}
                    min={0} decimalScale={1}
                    styles={inputStyles}
                  />
                  <NumberInput
                    label="Cost ($)"
                    placeholder="Optional"
                    value={editForm.cost ?? ''}
                    onChange={(v) => setEditForm({ ...editForm, cost: v ? Number(v) : null })}
                    min={0} decimalScale={2}
                    styles={inputStyles}
                  />
                </SimpleGrid>
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <NumberInput
                    label="Next Due Hours"
                    placeholder="Optional"
                    value={editForm.next_due_hours ?? ''}
                    onChange={(v) => setEditForm({ ...editForm, next_due_hours: v ? Number(v) : null })}
                    min={0} decimalScale={1}
                    styles={inputStyles}
                  />
                  <DateInput
                    label="Next Due Date"
                    placeholder="Optional"
                    value={editForm.next_due_date}
                    onChange={(v) => setEditForm({ ...editForm, next_due_date: v })}
                    styles={inputStyles}
                  />
                </SimpleGrid>
                <Textarea
                  label="Notes"
                  value={editForm.notes}
                  onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })}
                  styles={inputStyles}
                />
                <Group grow>
                  <Button variant="light" color="gray" onClick={cancelEditing}
                    leftSection={<IconX size={16} />}
                    styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                    CANCEL
                  </Button>
                  <Button color="cyan" onClick={handleSaveEdit} loading={saving}
                    leftSection={<IconCheck size={16} />}
                    styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                    SAVE CHANGES
                  </Button>
                </Group>
              </Stack>
            );
          }

          // ── View Mode ──
          return (
            <Stack gap="md">
              {/* Aircraft info */}
              <Card padding="sm" radius="sm" style={{ background: '#141922', border: '1px solid #1a1f2e' }}>
                <Group justify="space-between" wrap="wrap">
                  <div>
                    <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">AIRCRAFT</Text>
                    <Text size="lg" fw={600} c="#e8edf2">{acName}</Text>
                  </div>
                  {serial && (
                    <div>
                      <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">SERIAL NUMBER</Text>
                      <Text size="sm" style={monoFont} c="#00d4ff">{serial}</Text>
                    </div>
                  )}
                  <div>
                    <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">DATE PERFORMED</Text>
                    <Text size="sm" style={monoFont} c="#e8edf2">{rec.performed_at}</Text>
                  </div>
                </Group>
              </Card>

              {/* Type badges */}
              <div>
                <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase" mb={4}>MAINTENANCE TYPE</Text>
                <Group gap={6} wrap="wrap">
                  {getTypeLabels(rec.maintenance_type).map((t) => (
                    <Badge key={t} variant="light" color="cyan" size="md">{getTypeLabel(t)}</Badge>
                  ))}
                </Group>
              </div>

              {/* Details grid */}
              <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
                <div>
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">FLIGHT HOURS</Text>
                  <Text size="sm" style={monoFont} c="#e8edf2">{rec.flight_hours_at != null ? rec.flight_hours_at.toFixed(1) : '—'}</Text>
                </div>
                <div>
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">COST</Text>
                  <Text size="sm" style={monoFont} c="#e8edf2">{rec.cost != null ? `$${rec.cost.toFixed(2)}` : '—'}</Text>
                </div>
                <div>
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">NEXT DUE HOURS</Text>
                  <Text size="sm" style={monoFont} c="#e8edf2">{rec.next_due_hours != null ? rec.next_due_hours.toFixed(1) : '—'}</Text>
                </div>
                <div>
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">NEXT DUE DATE</Text>
                  <Text size="sm" style={monoFont} c={rec.next_due_date ? '#e8edf2' : '#5a6478'}>{rec.next_due_date || '—'}</Text>
                </div>
              </SimpleGrid>

              {/* Description */}
              {rec.description && (
                <div>
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase" mb={4}>DESCRIPTION</Text>
                  <Text size="sm" c="#e8edf2" style={{ whiteSpace: 'pre-wrap' }}>{rec.description}</Text>
                </div>
              )}

              {/* Notes */}
              {rec.notes && (
                <div>
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase" mb={4}>NOTES</Text>
                  <Text size="sm" c="#e8edf2" style={{ whiteSpace: 'pre-wrap' }}>{rec.notes}</Text>
                </div>
              )}

              {/* Photos — drag & drop zone */}
              <div
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
              >
                <Group justify="space-between" mb="xs">
                  <Text size="10px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
                    PHOTOS {images.length > 0 && `(${images.length})`}
                  </Text>
                  <FileButton onChange={handleImageUpload} accept="image/jpeg,image/png,image/webp">
                    {(props) => (
                      <Button
                        {...props}
                        size="compact-xs"
                        variant="light"
                        color="cyan"
                        leftSection={<IconCamera size={14} />}
                        loading={uploading}
                        styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                      >
                        ADD PHOTO
                      </Button>
                    )}
                  </FileButton>
                </Group>
                {images.length === 0 ? (
                  <Card
                    padding="md"
                    radius="sm"
                    style={{
                      background: dragOver ? '#0a1628' : '#141922',
                      border: dragOver ? '2px dashed #00d4ff' : '1px dashed #1a1f2e',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <Stack align="center" gap="xs" py="sm">
                      <IconUpload size={32} color={dragOver ? '#00d4ff' : '#5a6478'} />
                      <Text size="xs" c={dragOver ? '#00d4ff' : '#5a6478'}>
                        {dragOver ? 'Drop photos here' : 'Drag & drop photos here, or click ADD PHOTO'}
                      </Text>
                    </Stack>
                  </Card>
                ) : (
                  <div>
                    <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">
                      {images.map((imgPath, idx) => (
                        <div key={idx} style={{ position: 'relative' }}>
                          <Image
                            src={`/uploads/${imgPath}`}
                            radius="sm"
                            h={140}
                            fit="cover"
                            style={{ border: '1px solid #1a1f2e', cursor: 'pointer' }}
                            onClick={() => window.open(`/uploads/${imgPath}`, '_blank')}
                          />
                          <ActionIcon
                            size="xs"
                            color="red"
                            variant="filled"
                            radius="xl"
                            style={{ position: 'absolute', top: 4, right: 4 }}
                            onClick={() => handleImageDelete(idx)}
                          >
                            <IconX size={10} />
                          </ActionIcon>
                        </div>
                      ))}
                    </SimpleGrid>
                    {/* Drop zone below existing photos */}
                    <Card
                      padding="xs"
                      radius="sm"
                      mt="sm"
                      style={{
                        background: dragOver ? '#0a1628' : 'transparent',
                        border: dragOver ? '2px dashed #00d4ff' : '1px dashed #1a1f2e',
                        transition: 'all 0.2s ease',
                      }}
                    >
                      <Group justify="center" gap="xs" py={4}>
                        <IconUpload size={14} color={dragOver ? '#00d4ff' : '#5a6478'} />
                        <Text size="xs" c={dragOver ? '#00d4ff' : '#5a6478'}>
                          {dragOver ? 'Drop to add photos' : 'Drop more photos here'}
                        </Text>
                      </Group>
                    </Card>
                  </div>
                )}
                {uploading && (
                  <Group justify="center" mt="xs">
                    <Loader size="sm" color="cyan" />
                    <Text size="xs" c="#5a6478">Uploading...</Text>
                  </Group>
                )}
              </div>

              {/* Created */}
              <Text size="10px" c="#5a6478" style={monoFont} ta="right">
                Created: {new Date(rec.created_at).toLocaleString()}
              </Text>

              {/* Action buttons */}
              <Group grow>
                <Button
                  variant="light"
                  color="cyan"
                  leftSection={<IconEdit size={16} />}
                  onClick={() => startEditing(rec)}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  EDIT RECORD
                </Button>
                <Button
                  variant="light"
                  color="red"
                  leftSection={<IconTrash size={16} />}
                  onClick={() => { handleDelete(rec.id); }}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  DELETE RECORD
                </Button>
              </Group>
            </Stack>
          );
        })()}
      </Modal>

      {/* Add Record Modal */}
      <Modal
        opened={addOpen}
        onClose={() => setAddOpen(false)}
        title={<Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>LOG MAINTENANCE</Text>}
        size="md"
        styles={modalStyles}
      >
        <Stack gap="md">
          <Select
            label="Aircraft"
            required
            data={aircraft.map((a) => ({ value: a.id, label: `${a.manufacturer} ${a.model_name}${a.serial_number ? ` (S/N: ${a.serial_number})` : ''}` }))}
            value={form.aircraft_id}
            onChange={(v) => setForm({ ...form, aircraft_id: v || '' })}
            styles={inputStyles}
          />
          <MultiSelect
            label="Maintenance Type(s)"
            data={MAINTENANCE_TYPES}
            value={form.maintenance_types}
            onChange={(v) => setForm({ ...form, maintenance_types: v.length > 0 ? v : ['general_service'] })}
            placeholder="Select one or more categories"
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
