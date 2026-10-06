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
  Alert,
  Button,
  Group,
  Modal,
  Radio,
  Select,
  Stack,
  Textarea,
  TextInput,
  Loader,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';

import api from '../api/client';
import type { Customer } from '../api/types';
import { missionTitleFromLead, type LeadDetail } from '../api/leads';
import LeadPicker from './LeadPicker';

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

// ADR-0016 — lead-source options. Values must match the backend
// MissionSource enum (app/models/mission.py). Drives "how much revenue
// came from the website" attribution on the Financials dashboard.
const LEAD_SOURCES = [
  { value: 'website', label: 'Website' },
  { value: 'referral', label: 'Referral' },
  { value: 'repeat_client', label: 'Repeat Client' },
  { value: 'phone', label: 'Phone' },
  { value: 'social', label: 'Social Media' },
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
  // ADR-0050 — the website lead this mission is being started from, if any.
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const navigate = useNavigate();

  const form = useForm({
    initialValues: {
      title: '',
      customer_id: '',
      mission_type: 'other',
      mission_date: null as Date | null,
      source: '' as string,
      // ADR-0050 — lead prefill fields (only used when a lead is picked).
      description: '',
      source_ref: '',
      customer_mode: 'existing' as 'existing' | 'new',
      new_name: '',
      new_email: '',
      new_phone: '',
      new_company: '',
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
    setLead(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const applyLead = (d: LeadDetail) => {
    setLead(d);
    const l = d.lead;
    form.setValues({
      title: missionTitleFromLead(l),
      description: l.details,
      source: 'website',
      source_ref: l.key,
      customer_mode: d.matching_customer ? 'existing' : 'new',
      customer_id: d.matching_customer?.id ?? '',
      new_name: l.name,
      new_email: l.email,
      new_phone: l.phone,
      new_company: l.organization,
    });
  };

  const handleSubmit = form.onSubmit(async (values) => {
    setSubmitting(true);
    try {
      // ADR-0050 — starting from a lead with no matching customer: create the
      // customer first (linked to the lead), then the mission.
      let customerId = values.customer_id;
      if (lead && values.customer_mode === 'new') {
        const c = await api.post('/customers', {
          name: values.new_name.trim() || lead.lead.email,
          email: values.new_email.trim() || null,
          phone: values.new_phone.replace(/\D/g, '') || null,
          company: values.new_company.trim() || null,
          source_ref: values.source_ref,
        });
        customerId = c.data?.id;
      }
      // EXPLICIT: never include `id` in the create payload — spec §4
      // defensive guard rejects POSTs that smuggle an id field.
      const payload: Record<string, unknown> = {
        title: values.title.trim(),
        mission_type: values.mission_type,
      };
      if (customerId) payload.customer_id = customerId;
      if (values.mission_date) {
        // Send YYYY-MM-DD string (Date column on the server)
        payload.mission_date = values.mission_date.toISOString().slice(0, 10);
      }
      // ADR-0016 — only send source when the operator picked one; an
      // empty selection leaves origin unknown (NULL on the server).
      if (values.source) payload.source = values.source;
      if (values.description.trim()) payload.description = values.description.trim();
      if (values.source_ref) payload.source_ref = values.source_ref;

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
          <LeadPicker onPick={applyLead} />
          <TextInput
            label="Title"
            placeholder="e.g. Smith Property Inspection"
            required
            data-autofocus
            {...form.getInputProps('title')}
          />
          {lead && lead.matching_customer && (
            <Alert color="cyan" variant="light">
              Matches existing customer {lead.matching_customer.name}
            </Alert>
          )}
          {lead && (
            <Radio.Group
              label="Customer"
              value={form.values.customer_mode}
              onChange={(v) => form.setFieldValue('customer_mode', v as 'existing' | 'new')}
            >
              <Group mt={4}>
                {lead.matching_customer && (
                  <Radio value="existing" label={`Use ${lead.matching_customer.name}`} />
                )}
                <Radio value="new" label="Create new customer" />
              </Group>
            </Radio.Group>
          )}
          {lead && form.values.customer_mode === 'new' && (
            <Stack gap="xs">
              <TextInput label="Name" {...form.getInputProps('new_name')} />
              <TextInput label="Email" {...form.getInputProps('new_email')} />
              <TextInput label="Phone" {...form.getInputProps('new_phone')} />
              <TextInput label="Company" {...form.getInputProps('new_company')} />
            </Stack>
          )}
          {lead && (
            <Textarea label="Description" autosize minRows={2} {...form.getInputProps('description')} />
          )}
          {(!lead || form.values.customer_mode === 'existing') && (
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
          )}
          <Select
            label="Mission Type"
            data={MISSION_TYPES}
            required
            {...form.getInputProps('mission_type')}
          />
          <Select
            label="Lead Source (optional)"
            placeholder="How did this job come in?"
            data={LEAD_SOURCES}
            clearable
            {...form.getInputProps('source')}
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
