import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconEdit, IconPlus, IconTrash, IconUsers } from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';

/**
 * PILOTS tab — pilot roster + FAA currency. Fetches /pilots on mount.
 * Create/edit/deactivate flows and field names are unchanged.
 */
export default function PilotsTab() {
  const [pilots, setPilots] = useState<any[]>([]);
  const [pilotModal, setPilotModal] = useState(false);
  const [editingPilotId, setEditingPilotId] = useState<string | null>(null);
  const [pilotSaving, setPilotSaving] = useState(false);

  const pilotForm = useForm({
    initialValues: { name: '', email: '', phone: '', faa_certificate_number: '', faa_certificate_expiry: '', notes: '' },
  });

  const reload = () => api.get('/pilots').then((r) => setPilots(Array.isArray(r.data) ? r.data : []));

  useEffect(() => {
    reload().catch(() => {});
  }, []);

  const handleSavePilot = async (values: typeof pilotForm.values) => {
    setPilotSaving(true);
    try {
      const payload = {
        ...values,
        faa_certificate_expiry: values.faa_certificate_expiry || null,
      };
      if (editingPilotId) {
        await api.put(`/pilots/${editingPilotId}`, payload);
      } else {
        await api.post('/pilots', payload);
      }
      setPilotModal(false);
      setEditingPilotId(null);
      pilotForm.reset();
      reload();
      notifications.show({ title: 'Saved', message: 'Pilot saved', color: 'cyan' });
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to save pilot', color: 'red' });
    } finally {
      setPilotSaving(false);
    }
  };

  const handleEditPilot = (p: any) => {
    setEditingPilotId(p.id);
    pilotForm.setValues({
      name: p.name || '',
      email: p.email || '',
      phone: p.phone || '',
      faa_certificate_number: p.faa_certificate_number || '',
      faa_certificate_expiry: p.faa_certificate_expiry ? p.faa_certificate_expiry.slice(0, 16) : '',
      notes: p.notes || '',
    });
    setPilotModal(true);
  };

  const handleDeletePilot = async (pilotId: string) => {
    try {
      await api.delete(`/pilots/${pilotId}`);
      reload();
      notifications.show({ title: 'Deactivated', message: 'Pilot deactivated', color: 'yellow' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to deactivate pilot', color: 'red' });
    }
  };

  return (
    <Stack gap="md">
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" mb="md">
          <Group gap="sm">
            <IconUsers size={20} color="#00d4ff" />
            <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>PILOTS</Title>
          </Group>
          <Button leftSection={<IconPlus size={14} />} size="xs" color="cyan" onClick={() => { setEditingPilotId(null); pilotForm.reset(); setPilotModal(true); }}>
            Add Pilot
          </Button>
        </Group>
        <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Manage drone pilots, track flight hours, and monitor FAA Part 107 currency status.
        </Text>

        <Table styles={{
          table: { color: '#e8edf2' },
          th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', borderBottom: '1px solid #1a1f2e' },
          td: { borderBottom: '1px solid #1a1f2e', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px' },
        }}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>NAME</Table.Th>
              <Table.Th>FLIGHTS</Table.Th>
              <Table.Th>HOURS</Table.Th>
              <Table.Th>FAA CERT EXPIRY</Table.Th>
              <Table.Th>STATUS</Table.Th>
              <Table.Th>ACTIONS</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pilots.length === 0 && (
              <Table.Tr><Table.Td colSpan={6}><Text c="#5a6478" size="sm" ta="center" py="md">No pilots added yet</Text></Table.Td></Table.Tr>
            )}
            {pilots.map((p: any) => (
              <Table.Tr key={p.id}>
                <Table.Td>{p.name}</Table.Td>
                <Table.Td>{p.total_flights ?? 0}</Table.Td>
                <Table.Td>{p.total_flight_hours?.toFixed(1) ?? '0.0'}h</Table.Td>
                <Table.Td>
                  {p.faa_certificate_expiry
                    ? new Date(p.faa_certificate_expiry).toLocaleDateString()
                    : <Text c="#5a6478" size="xs">Not set</Text>}
                </Table.Td>
                <Table.Td>
                  <Badge color={p.is_active ? 'green' : 'gray'} size="sm" variant="light">
                    {p.is_active ? 'ACTIVE' : 'INACTIVE'}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Group gap={4}>
                    <ActionIcon variant="subtle" color="cyan" size="sm" onClick={() => handleEditPilot(p)}>
                      <IconEdit size={14} />
                    </ActionIcon>
                    {p.is_active && (
                      <ActionIcon variant="subtle" color="red" size="sm" onClick={() => handleDeletePilot(p.id)}>
                        <IconTrash size={14} />
                      </ActionIcon>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      {/* Pilot Modal */}
      <Modal
        opened={pilotModal}
        onClose={() => { setPilotModal(false); setEditingPilotId(null); }}
        title={editingPilotId ? 'Edit Pilot' : 'New Pilot'}
        styles={{ header: { background: '#0e1117' }, content: { background: '#0e1117' }, title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif" } }}
      >
        <form onSubmit={pilotForm.onSubmit(handleSavePilot)}>
          <Stack gap="sm">
            <TextInput label="Name" required {...pilotForm.getInputProps('name')} styles={inputStyles} />
            <TextInput label="Email" {...pilotForm.getInputProps('email')} styles={inputStyles} />
            <TextInput label="Phone" {...pilotForm.getInputProps('phone')} styles={inputStyles} />
            <TextInput label="FAA Certificate Number" {...pilotForm.getInputProps('faa_certificate_number')} styles={inputStyles} />
            <TextInput label="FAA Certificate Expiry" type="datetime-local" {...pilotForm.getInputProps('faa_certificate_expiry')} styles={inputStyles} />
            <Textarea label="Notes" minRows={3} {...pilotForm.getInputProps('notes')} styles={inputStyles} />
            <Button type="submit" color="cyan" loading={pilotSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
