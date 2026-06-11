import { useMemo, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Image,
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
import { IconDrone, IconEdit, IconPhoto, IconPlus, IconTrash, IconUpload } from '@tabler/icons-react';
import api from '../../api/client';
import { Aircraft, RateTemplate } from '../../api/types';
import { inputStyles, cardStyle } from '../../components/shared/styles';
import { useApiCache, invalidate as invalidateCache } from '../../hooks/useApiCache';

const categoryLabels: Record<string, string> = {
  travel: 'Travel',
  billed_time: 'Billed Time',
  rapid_deployment: 'Rapid Deploy',
  equipment: 'Equipment',
  special: 'Special',
  other: 'Other',
};

/**
 * FLEET & RATES tab — aircraft fleet manager + rate templates.
 *
 * The two read-only reference lists (/aircraft, /rate-templates) are routed
 * through useApiCache (per P2-5 1b — same pattern as Dashboard.tsx): one cached
 * round-trip shared across the app, refreshed by invalidate() after each
 * mutation. Image upload mutates the row optimistically and also invalidates
 * the cache so the next read is fresh. Payloads/field names unchanged.
 */
export default function FleetTab() {
  const { data: aircraftRaw } = useApiCache<Aircraft[]>('/aircraft');
  const { data: rateTemplatesRaw } = useApiCache<RateTemplate[]>('/rate-templates');
  const aircraft = useMemo(() => (Array.isArray(aircraftRaw) ? aircraftRaw : []), [aircraftRaw]);
  const rateTemplates = useMemo(() => (Array.isArray(rateTemplatesRaw) ? rateTemplatesRaw : []), [rateTemplatesRaw]);

  const [aircraftModal, setAircraftModal] = useState(false);
  const [editingAircraftId, setEditingAircraftId] = useState<string | null>(null);
  const [aircraftImageUploading, setAircraftImageUploading] = useState(false);
  const [editingAircraftImage, setEditingAircraftImage] = useState<string | null>(null);
  const [rateModal, setRateModal] = useState(false);
  const [editingRateId, setEditingRateId] = useState<string | null>(null);

  const aircraftForm = useForm({
    initialValues: { model_name: '', manufacturer: 'DJI', serial_number: '', specs_json: '{}' },
  });

  const rateForm = useForm({
    initialValues: { name: '', description: '', category: 'other', default_quantity: 1, default_unit: '', default_rate: 0 },
  });

  const handleSaveAircraft = async (values: typeof aircraftForm.values) => {
    try {
      const data = {
        model_name: values.model_name,
        manufacturer: values.manufacturer,
        serial_number: values.serial_number.trim() || null,
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
      invalidateCache('/aircraft');
      notifications.show({ title: 'Saved', message: 'Aircraft profile saved', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Invalid specs JSON', color: 'red' });
    }
  };

  const handleEditAircraft = (a: Aircraft) => {
    setEditingAircraftId(a.id);
    setEditingAircraftImage(a.image_filename || null);
    aircraftForm.setValues({
      model_name: a.model_name,
      manufacturer: a.manufacturer,
      serial_number: a.serial_number || '',
      specs_json: JSON.stringify(a.specs, null, 2),
    });
    setAircraftModal(true);
  };

  const handleUploadAircraftImage = (aircraftId: string) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      setAircraftImageUploading(true);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await api.post(`/aircraft/${aircraftId}/image`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        setEditingAircraftImage(resp.data.image_filename);
        invalidateCache('/aircraft');
        notifications.show({ title: 'Uploaded', message: 'Aircraft image uploaded', color: 'cyan' });
      } catch {
        notifications.show({ title: 'Error', message: 'Failed to upload image (JPEG/PNG/WebP, max 10MB)', color: 'red' });
      } finally {
        setAircraftImageUploading(false);
      }
    };
    input.click();
  };

  const handleDeleteAircraftImage = async (aircraftId: string) => {
    try {
      await api.delete(`/aircraft/${aircraftId}/image`);
      setEditingAircraftImage(null);
      invalidateCache('/aircraft');
      notifications.show({ title: 'Removed', message: 'Aircraft image removed', color: 'orange' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to remove image', color: 'red' });
    }
  };

  const handleDeleteAircraft = async (id: string) => {
    if (!confirm('Delete this aircraft?')) return;
    try {
      await api.delete(`/aircraft/${id}`);
      invalidateCache('/aircraft');
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete aircraft', color: 'red' });
    }
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
      invalidateCache('/rate-templates');
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
    invalidateCache('/rate-templates');
  };

  return (
    <Stack gap="md">
      {/* Aircraft Manager */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" mb="md">
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>AIRCRAFT FLEET</Title>
          <Group gap="xs">
            <Button
              leftSection={<IconDrone size={14} />}
              size="xs"
              variant="light"
              color="cyan"
              onClick={async () => {
                try {
                  notifications.show({ id: 'backfill', title: 'Matching...', message: 'Re-matching flights to fleet aircraft', loading: true, autoClose: false });
                  const resp = await api.post('/flight-library/backfill-aircraft');
                  const { matched, total_unlinked, still_unlinked } = resp.data;
                  notifications.update({ id: 'backfill', title: 'Complete', message: `Matched ${matched} of ${total_unlinked} unlinked flights. ${still_unlinked} still unlinked.`, loading: false, autoClose: 5000, color: matched > 0 ? 'green' : 'yellow' });
                } catch {
                  notifications.update({ id: 'backfill', title: 'Error', message: 'Failed to run backfill', loading: false, autoClose: 4000, color: 'red' });
                }
              }}
            >
              Re-match Flights
            </Button>
            <Button
              leftSection={<IconPlus size={14} />}
              size="xs"
              color="cyan"
              onClick={() => { setEditingAircraftId(null); aircraftForm.reset(); setAircraftModal(true); }}
            >
              Add Aircraft
            </Button>
          </Group>
        </Group>

        <Table styles={{
          table: { color: '#e8edf2' },
          th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', borderBottom: '1px solid #1a1f2e' },
          td: { borderBottom: '1px solid #1a1f2e' },
        }}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={50}></Table.Th>
              <Table.Th>MODEL</Table.Th>
              <Table.Th>SERIAL NUMBER</Table.Th>
              <Table.Th>MANUFACTURER</Table.Th>
              <Table.Th>KEY SPECS</Table.Th>
              <Table.Th>ACTIONS</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {aircraft.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>
                  {a.image_filename ? (
                    <Image src={`/uploads/${a.image_filename}`} w={36} h={36} radius="sm" fit="cover" />
                  ) : (
                    <IconDrone size={20} color="#5a6478" />
                  )}
                </Table.Td>
                <Table.Td fw={600}>{a.model_name}</Table.Td>
                <Table.Td c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '12px' }}>{a.serial_number || '—'}</Table.Td>
                <Table.Td c="#5a6478">{a.manufacturer}</Table.Td>
                <Table.Td c="#5a6478" style={{ fontSize: '12px' }}>
                  {a.specs.max_flight_time && `${a.specs.max_flight_time}`}
                  {a.specs.camera && ` | ${a.specs.camera.substring(0, 30)}...`}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon variant="subtle" color="cyan" onClick={() => handleEditAircraft(a)} aria-label={`Edit aircraft: ${a.model_name}`}>
                      <IconEdit size={14} />
                    </ActionIcon>
                    <ActionIcon variant="subtle" color="red" onClick={() => handleDeleteAircraft(a.id)} aria-label={`Delete aircraft: ${a.model_name}`}>
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
          th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', borderBottom: '1px solid #1a1f2e' },
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
                    <ActionIcon variant="subtle" color="cyan" onClick={() => handleEditRate(t)} aria-label={`Edit rate template: ${t.name}`}>
                      <IconEdit size={14} />
                    </ActionIcon>
                    <ActionIcon variant="subtle" color="red" onClick={() => handleDeleteRate(t.id)} aria-label={`Delete rate template: ${t.name}`}>
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

      {/* Aircraft Modal */}
      <Modal
        opened={aircraftModal}
        onClose={() => { setAircraftModal(false); setEditingAircraftImage(null); }}
        title={editingAircraftId ? 'Edit Aircraft' : 'New Aircraft'}
        styles={{ header: { background: '#0e1117' }, content: { background: '#0e1117' }, title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif" } }}
      >
        <form onSubmit={aircraftForm.onSubmit(handleSaveAircraft)}>
          <Stack gap="sm">
            <TextInput label="Model Name" required {...aircraftForm.getInputProps('model_name')} styles={inputStyles} />
            <TextInput label="Serial Number" placeholder="Drone hardware serial number" {...aircraftForm.getInputProps('serial_number')} styles={inputStyles} />
            <TextInput label="Manufacturer" {...aircraftForm.getInputProps('manufacturer')} styles={inputStyles} />

            {/* Aircraft Image Upload */}
            {editingAircraftId && (
              <div>
                <Text size="sm" fw={500} c="#c1c2c5" mb={4}>Aircraft Image</Text>
                <div
                  style={{
                    border: '1px dashed #1a1f2e',
                    borderRadius: 8,
                    padding: 16,
                    textAlign: 'center',
                    background: '#050608',
                  }}
                >
                  {editingAircraftImage ? (
                    <Stack align="center" gap="xs">
                      <Image
                        src={`/uploads/${editingAircraftImage}`}
                        maw={200}
                        mah={150}
                        radius="md"
                        fit="contain"
                      />
                      <Group gap="xs">
                        <Button
                          size="xs"
                          variant="light"
                          color="cyan"
                          leftSection={<IconPhoto size={14} />}
                          loading={aircraftImageUploading}
                          onClick={() => handleUploadAircraftImage(editingAircraftId)}
                          styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
                        >
                          Replace
                        </Button>
                        <Button
                          size="xs"
                          variant="light"
                          color="red"
                          leftSection={<IconTrash size={14} />}
                          onClick={() => handleDeleteAircraftImage(editingAircraftId)}
                          styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
                        >
                          Remove
                        </Button>
                      </Group>
                    </Stack>
                  ) : (
                    <Stack align="center" gap="xs">
                      <IconDrone size={40} color="#5a6478" />
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        No image uploaded
                      </Text>
                      <Button
                        size="xs"
                        variant="light"
                        color="cyan"
                        leftSection={<IconUpload size={14} />}
                        loading={aircraftImageUploading}
                        onClick={() => handleUploadAircraftImage(editingAircraftId)}
                        styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
                      >
                        Upload Image
                      </Button>
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                        JPEG, PNG, or WebP — max 10MB
                      </Text>
                    </Stack>
                  )}
                </div>
              </div>
            )}
            {!editingAircraftId && (
              <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Save the aircraft first, then edit it to upload an image.
              </Text>
            )}

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
