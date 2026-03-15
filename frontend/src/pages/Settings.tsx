import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Text,
  TextInput,
  Title,
  Switch,
  Table,
  ActionIcon,
  Modal,
  Textarea,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconX, IconPlus, IconEdit, IconTrash, IconCurrencyDollar } from '@tabler/icons-react';
import api from '../api/client';
import { Aircraft, RateTemplate } from '../api/types';

const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
};

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

export default function Settings() {
  const [llmStatus, setLlmStatus] = useState<any>(null);
  const [llmLoading, setLlmLoading] = useState(true);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [aircraftModal, setAircraftModal] = useState(false);
  const [editingAircraftId, setEditingAircraftId] = useState<string | null>(null);
  const [rateTemplates, setRateTemplates] = useState<RateTemplate[]>([]);
  const [rateModal, setRateModal] = useState(false);
  const [editingRateId, setEditingRateId] = useState<string | null>(null);

  const aircraftForm = useForm({
    initialValues: { model_name: '', manufacturer: 'DJI', specs_json: '{}' },
  });

  const rateForm = useForm({
    initialValues: { name: '', description: '', category: 'other', default_quantity: 1, default_unit: '', default_rate: 0 },
  });

  useEffect(() => {
    api.get('/llm/status').then((r) => setLlmStatus(r.data)).catch(() => setLlmStatus({ status: 'offline' })).finally(() => setLlmLoading(false));
    api.get('/aircraft').then((r) => setAircraft(r.data)).catch(() => {});
    api.get('/rate-templates').then((r) => setRateTemplates(r.data)).catch(() => {});
  }, []);

  const handleSaveAircraft = async (values: typeof aircraftForm.values) => {
    try {
      const data = {
        model_name: values.model_name,
        manufacturer: values.manufacturer,
        specs: JSON.parse(values.specs_json || '{}'),
      };
      if (editingAircraftId) {
        await api.put(`/aircraft/${editingAircraftId}`, data);
      } else {
        await api.post('/aircraft', data);
      }
      setAircraftModal(false);
      setEditingAircraftId(null);
      aircraftForm.reset();
      api.get('/aircraft').then((r) => setAircraft(r.data));
      notifications.show({ title: 'Saved', message: 'Aircraft profile saved', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Invalid specs JSON', color: 'red' });
    }
  };

  const handleEditAircraft = (a: Aircraft) => {
    setEditingAircraftId(a.id);
    aircraftForm.setValues({
      model_name: a.model_name,
      manufacturer: a.manufacturer,
      specs_json: JSON.stringify(a.specs, null, 2),
    });
    setAircraftModal(true);
  };

  const handleDeleteAircraft = async (id: string) => {
    if (!confirm('Delete this aircraft?')) return;
    await api.delete(`/aircraft/${id}`);
    setAircraft((prev) => prev.filter((a) => a.id !== id));
  };

  const handleSaveRate = async (values: typeof rateForm.values) => {
    try {
      if (editingRateId) {
        await api.put(`/rate-templates/${editingRateId}`, values);
      } else {
        await api.post('/rate-templates', values);
      }
      setRateModal(false);
      setEditingRateId(null);
      rateForm.reset();
      api.get('/rate-templates').then((r) => setRateTemplates(r.data));
      notifications.show({ title: 'Saved', message: 'Rate template saved', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save rate template', color: 'red' });
    }
  };

  const handleEditRate = (t: RateTemplate) => {
    setEditingRateId(t.id);
    rateForm.setValues({
      name: t.name,
      description: t.description || '',
      category: t.category,
      default_quantity: t.default_quantity,
      default_unit: t.default_unit || '',
      default_rate: t.default_rate,
    });
    setRateModal(true);
  };

  const handleDeleteRate = async (id: string) => {
    if (!confirm('Delete this rate template?')) return;
    await api.delete(`/rate-templates/${id}`);
    setRateTemplates((prev) => prev.filter((t) => t.id !== id));
  };

  const categoryLabels: Record<string, string> = {
    travel: 'Travel',
    billed_time: 'Billed Time',
    rapid_deployment: 'Rapid Deploy',
    equipment: 'Equipment',
    special: 'Special',
    other: 'Other',
  };

  return (
    <Stack gap="lg">
      <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>SETTINGS</Title>

      {/* LLM Status */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>LLM STATUS</Title>
        {llmLoading ? (
          <Loader color="cyan" size="sm" />
        ) : (
          <Stack gap="sm">
            <Group>
              <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>STATUS:</Text>
              <Badge
                color={llmStatus?.status === 'online' ? 'green' : 'red'}
                leftSection={llmStatus?.status === 'online' ? <IconCheck size={12} /> : <IconX size={12} />}
              >
                {llmStatus?.status || 'unknown'}
              </Badge>
            </Group>
            <Group>
              <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>MODEL:</Text>
              <Text c="#e8edf2">{llmStatus?.configured_model || 'Not set'}</Text>
              {llmStatus?.model_available && <Badge color="green" size="xs">Available</Badge>}
            </Group>
            {llmStatus?.models && (
              <Group>
                <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>INSTALLED:</Text>
                {llmStatus.models.map((m: string) => <Badge key={m} color="cyan" variant="light" size="sm">{m}</Badge>)}
              </Group>
            )}
          </Stack>
        )}
      </Card>

      {/* Info Card */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>CONNECTIONS</Title>
        <Text c="#5a6478" size="sm">
          OpenDroneLog URL and SMTP settings are configured via environment variables in docker-compose.yml.
          Update your .env file and restart the containers to change these settings.
        </Text>
      </Card>

      {/* Aircraft Manager */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" mb="md">
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>AIRCRAFT FLEET</Title>
          <Button
            leftSection={<IconPlus size={14} />}
            size="xs"
            color="cyan"
            onClick={() => { setEditingAircraftId(null); aircraftForm.reset(); setAircraftModal(true); }}
          >
            Add Aircraft
          </Button>
        </Group>

        <Table styles={{
          table: { color: '#e8edf2' },
          th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', borderBottom: '1px solid #1a1f2e' },
          td: { borderBottom: '1px solid #1a1f2e' },
        }}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>MODEL</Table.Th>
              <Table.Th>MANUFACTURER</Table.Th>
              <Table.Th>KEY SPECS</Table.Th>
              <Table.Th>ACTIONS</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {aircraft.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td fw={600}>{a.model_name}</Table.Td>
                <Table.Td c="#5a6478">{a.manufacturer}</Table.Td>
                <Table.Td c="#5a6478" style={{ fontSize: '12px' }}>
                  {a.specs.max_flight_time && `${a.specs.max_flight_time}`}
                  {a.specs.camera && ` | ${a.specs.camera.substring(0, 30)}...`}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon variant="subtle" color="cyan" onClick={() => handleEditAircraft(a)}>
                      <IconEdit size={14} />
                    </ActionIcon>
                    <ActionIcon variant="subtle" color="red" onClick={() => handleDeleteAircraft(a.id)}>
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      {/* Rate Templates */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" mb="md">
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>RATE TEMPLATES</Title>
          <Button
            leftSection={<IconPlus size={14} />}
            size="xs"
            color="cyan"
            onClick={() => { setEditingRateId(null); rateForm.reset(); setRateModal(true); }}
          >
            Add Template
          </Button>
        </Group>

        <Table styles={{
          table: { color: '#e8edf2' },
          th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', borderBottom: '1px solid #1a1f2e' },
          td: { borderBottom: '1px solid #1a1f2e' },
        }}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>NAME</Table.Th>
              <Table.Th>CATEGORY</Table.Th>
              <Table.Th>DEFAULT RATE</Table.Th>
              <Table.Th>UNIT</Table.Th>
              <Table.Th>ACTIONS</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rateTemplates.map((t) => (
              <Table.Tr key={t.id}>
                <Table.Td fw={600}>{t.name}</Table.Td>
                <Table.Td><Badge color="cyan" variant="light" size="sm">{categoryLabels[t.category] || t.category}</Badge></Table.Td>
                <Table.Td c="#00d4ff" style={{ fontFamily: "'Share Tech Mono', monospace" }}>${Number(t.default_rate).toFixed(2)}</Table.Td>
                <Table.Td c="#5a6478">{t.default_unit || '—'}</Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon variant="subtle" color="cyan" onClick={() => handleEditRate(t)}>
                      <IconEdit size={14} />
                    </ActionIcon>
                    <ActionIcon variant="subtle" color="red" onClick={() => handleDeleteRate(t.id)}>
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      {/* Rate Template Modal */}
      <Modal
        opened={rateModal}
        onClose={() => setRateModal(false)}
        title={editingRateId ? 'Edit Rate Template' : 'New Rate Template'}
        styles={{ header: { background: '#0e1117' }, content: { background: '#0e1117' }, title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif" } }}
      >
        <form onSubmit={rateForm.onSubmit(handleSaveRate)}>
          <Stack gap="sm">
            <TextInput label="Name" required {...rateForm.getInputProps('name')} styles={inputStyles} />
            <TextInput label="Description" {...rateForm.getInputProps('description')} styles={inputStyles} />
            <TextInput label="Category" placeholder="billed_time, travel, equipment, special, rapid_deployment, other" {...rateForm.getInputProps('category')} styles={inputStyles} />
            <TextInput label="Default Quantity" type="number" {...rateForm.getInputProps('default_quantity')} styles={inputStyles} />
            <TextInput label="Default Unit" placeholder="hours, miles, flat" {...rateForm.getInputProps('default_unit')} styles={inputStyles} />
            <TextInput label="Default Rate ($)" type="number" step="0.01" {...rateForm.getInputProps('default_rate')} styles={inputStyles} />
            <Button type="submit" color="cyan" styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE
            </Button>
          </Stack>
        </form>
      </Modal>

      <Modal
        opened={aircraftModal}
        onClose={() => setAircraftModal(false)}
        title={editingAircraftId ? 'Edit Aircraft' : 'New Aircraft'}
        styles={{ header: { background: '#0e1117' }, content: { background: '#0e1117' }, title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif" } }}
      >
        <form onSubmit={aircraftForm.onSubmit(handleSaveAircraft)}>
          <Stack gap="sm">
            <TextInput label="Model Name" required {...aircraftForm.getInputProps('model_name')} styles={inputStyles} />
            <TextInput label="Manufacturer" {...aircraftForm.getInputProps('manufacturer')} styles={inputStyles} />
            <Textarea label="Specs (JSON)" minRows={6} {...aircraftForm.getInputProps('specs_json')} styles={inputStyles} />
            <Button type="submit" color="cyan" styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
