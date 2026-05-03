/**
 * MissionCreateModal — slim mission creation modal mounted from the
 * Missions list page (per spec §2 + §3).
 *
 * Replaces the legacy `/missions/new` 5-step wizard for creation. The
 * Hub redesign (v2.67.0) makes mission creation a single-shot POST:
 * title + customer + type + optional date. On success we navigate to
 * the new mission's Hub at `/missions/{id}` where the operator edits
 * each facet (Details / Flights / Images / Report / Invoice) via a
 * focused per-facet editor.
 *
 * Critical contract (per spec §4 defensive guard): the POST body MUST
 * NEVER include an `id` field. The backend rejects `id`-bearing POSTs
 * with 400 to make the duplicate-mission class physically impossible.
 */
import { useEffect, useState } from 'react';
import {
  Button,
  Group,
  Modal,
  Select,
  Stack,
  TextInput,
  Loader,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';

import api from '../api/client';
import type { Customer } from '../api/types';

const MISSION_TYPES = [
  { value: 'sar', label: 'Search and Rescue' },
  { value: 'videography', label: 'Videography' },
  { value: 'lost_pet', label: 'Lost Pet' },
  { value: 'inspection', label: 'Inspection' },
  { value: 'mapping', label: 'Mapping' },
  { value: 'photography', label: 'Photography' },
  { value: 'survey', label: 'Survey' },
  { value: 'security_investigations', label: 'Security / Investigations' },
  { value: 'other', label: 'Other' },
];

interface Props {
  opened: boolean;
  onClose: () => void;
}

export default function MissionCreateModal({ opened, onClose }: Props) {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loadingCustomers, setLoadingCustomers] = useState(false);
  const navigate = useNavigate();

  const form = useForm({
    initialValues: {
      title: '',
      customer_id: '',
      mission_type: 'other',
      mission_date: null as Date | null,
    },
    validate: {
      title: (v) => (v.trim().length === 0 ? 'Title is required' : null),
      mission_type: (v) => (!v ? 'Mission type is required' : null),
    },
  });

  useEffect(() => {
    if (!opened) return;
    setLoadingCustomers(true);
    api
      .get('/customers')
      .then((r) => setCustomers(r.data))
      .catch(() => setCustomers([]))
      .finally(() => setLoadingCustomers(false));
    // Reset on each open so a previous submission doesn't linger
    form.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const handleSubmit = form.onSubmit(async (values) => {
    setSubmitting(true);
    try {
      // EXPLICIT: never include `id` in the create payload — spec §4
      // defensive guard rejects POSTs that smuggle an id field.
      const payload: Record<string, unknown> = {
        title: values.title.trim(),
        mission_type: values.mission_type,
      };
      if (values.customer_id) payload.customer_id = values.customer_id;
      if (values.mission_date) {
        // Send YYYY-MM-DD string (Date column on the server)
        payload.mission_date = values.mission_date.toISOString().slice(0, 10);
      }

      const resp = await api.post('/missions', payload);
      const newId = resp.data?.id;
      if (!newId) {
        throw new Error('Server response missing mission id');
      }
      notifications.show({
        title: 'Mission created',
        message: `${values.title.trim()} is ready — opening the Hub`,
        color: 'cyan',
      });
      onClose();
      navigate(`/missions/${newId}`);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      const detail = axiosErr.response?.data?.detail || 'Failed to create mission';
      notifications.show({ title: 'Error', message: detail, color: 'red' });
    } finally {
      setSubmitting(false);
    }
  });

  return (
    <Modal
      opened={opened}
      onClose={() => !submitting && onClose()}
      title="NEW MISSION"
      centered
      size="lg"
      styles={{
        title: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', color: '#e8edf2' },
        content: { background: '#0e1117', border: '1px solid #1a1f2e' },
        header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' },
        body: { color: '#c0c8d4' },
      }}
    >
      <form onSubmit={handleSubmit}>
        <Stack gap="md">
          <TextInput
            label="Title"
            placeholder="e.g. Smith Property Inspection"
            required
            data-autofocus
            {...form.getInputProps('title')}
          />
          <Select
            label="Customer"
            placeholder={loadingCustomers ? 'Loading...' : 'Select customer (optional)'}
            data={customers.map((c) => ({
              value: c.id,
              label: `${c.name}${c.company ? ` (${c.company})` : ''}`,
            }))}
            searchable
            clearable
            disabled={loadingCustomers}
            {...form.getInputProps('customer_id')}
          />
          <Select
            label="Mission Type"
            data={MISSION_TYPES}
            required
            {...form.getInputProps('mission_type')}
          />
          <DateInput
            label="Mission Date (optional)"
            placeholder="Pick a date"
            valueFormat="YYYY-MM-DD"
            clearable
            {...form.getInputProps('mission_date')}
          />
          <Group justify="flex-end" mt="md">
            <Button variant="subtle" color="gray" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              type="submit"
              color="cyan"
              loading={submitting}
              leftSection={submitting ? <Loader size={14} color="white" /> : null}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              CREATE MISSION
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
