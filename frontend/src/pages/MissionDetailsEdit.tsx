/**
 * MissionDetailsEdit — Mission Hub redesign (v2.67.0, ADR-0014).
 *
 * Focused editor for the Details facet. Mounted at
 * `/missions/:id/details/edit`. Loads an existing mission via
 * `GET /api/missions/{id}`, edits in place, persists with
 * `PUT /api/missions/{id}`, returns to the Hub on Save or Cancel.
 *
 * NEVER creates a mission — see the constraint comment on
 * `handleSave()` below.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Customer, Mission, NominatimResult } from '../api/types';
import { inputStyles, cardStyle } from '../components/shared/styles';
import UnsavedChangesModal from '../components/shared/UnsavedChangesModal';
import { useDirtyGuard } from '../hooks/useDirtyGuard';

const missionTypes = [
  { value: 'sar', label: 'Search & Rescue' },
  { value: 'videography', label: 'Videography' },
  { value: 'lost_pet', label: 'Lost Pet Recovery' },
  { value: 'inspection', label: 'Inspection' },
  { value: 'mapping', label: 'Mapping' },
  { value: 'photography', label: 'Photography' },
  { value: 'survey', label: 'Survey' },
  { value: 'security_investigations', label: 'Security & Investigations' },
  { value: 'other', label: 'Other' },
];

interface FormValues {
  customer_id: string;
  title: string;
  mission_type: string;
  description: string;
  mission_date: Date | null;
  location_name: string;
  is_billable: boolean;
}

export default function MissionDetailsEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [unasFolderPath, setUnasFolderPath] = useState('');
  const [downloadLinkUrl, setDownloadLinkUrl] = useState('');
  const [downloadLinkExpiresAt, setDownloadLinkExpiresAt] = useState<Date | null>(null);

  // Baseline snapshot for fields outside Mantine's form (UNAS trio).
  // Mantine `form.isDirty()` only tracks fields registered in
  // initialValues; the 3 UNAS fields are plain useState. Snapshot the
  // loaded values so the dirty-guard knows when they've drifted.
  const [unasBaseline, setUnasBaseline] = useState({
    folderPath: '',
    linkUrl: '',
    linkExpiresAtIso: '',
  });

  // Nominatim address autosuggest (lifted from Customers.tsx pattern).
  const [addressSuggestions, setAddressSuggestions] = useState<NominatimResult[]>([]);
  const [showAddressDropdown, setShowAddressDropdown] = useState(false);
  const addressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const form = useForm<FormValues>({
    initialValues: {
      customer_id: '',
      title: '',
      mission_type: 'other',
      description: '',
      mission_date: null,
      location_name: '',
      is_billable: false,
    },
    validate: {
      title: (v) => (v.trim() ? null : 'Title is required'),
    },
  });

  // Load reference data + the mission itself.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    const load = async () => {
      try {
        const [missionResp, customersResp] = await Promise.all([
          api.get<Mission>(`/missions/${id}`),
          api.get<Customer[]>('/customers').catch(() => ({ data: [] as Customer[] })),
        ]);
        if (cancelled) return;
        const m = missionResp.data;

        const loaded = {
          customer_id: m.customer_id || '',
          title: m.title,
          mission_type: m.mission_type,
          description: m.description || '',
          mission_date: m.mission_date ? new Date(m.mission_date + 'T00:00:00') : null,
          location_name: m.location_name || '',
          is_billable: m.is_billable,
        };
        form.setValues(loaded);
        // Mantine's isDirty() compares against initialValues, which were
        // the empty defaults. Re-baseline so a freshly loaded form is
        // NOT considered dirty by the unsaved-changes guard.
        form.resetDirty(loaded);
        const initialUnasPath = m.unas_folder_path || '';
        const initialLinkUrl = m.download_link_url || '';
        const initialExpiresAt = m.download_link_expires_at
          ? new Date(m.download_link_expires_at)
          : null;
        setUnasFolderPath(initialUnasPath);
        setDownloadLinkUrl(initialLinkUrl);
        setDownloadLinkExpiresAt(initialExpiresAt);
        setUnasBaseline({
          folderPath: initialUnasPath,
          linkUrl: initialLinkUrl,
          linkExpiresAtIso: initialExpiresAt ? initialExpiresAt.toISOString() : '',
        });
        setCustomers(customersResp.data);
      } catch (err) {
        console.error('[MissionDetailsEdit] load failed', err);
        notifications.show({
          title: 'Load failed',
          message: 'Could not load mission details — returning to mission list.',
          color: 'red',
        });
        navigate('/missions');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const searchAddress = useCallback((query: string) => {
    if (addressTimerRef.current) clearTimeout(addressTimerRef.current);
    if (!query || query.length < 4) {
      setAddressSuggestions([]);
      setShowAddressDropdown(false);
      return;
    }
    addressTimerRef.current = setTimeout(async () => {
      try {
        const resp = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&limit=5&countrycodes=us&q=${encodeURIComponent(query)}`,
          { headers: { 'Accept-Language': 'en' } },
        );
        if (!resp.ok) return;
        const data: NominatimResult[] = await resp.json();
        setAddressSuggestions(data);
        setShowAddressDropdown(data.length > 0);
      } catch {
        // Address autosuggest is a UX nicety — silent failure is acceptable.
      }
    }, 400);
  }, []);

  // Cleanup pending address-search timer on unmount.
  useEffect(
    () => () => {
      if (addressTimerRef.current) clearTimeout(addressTimerRef.current);
    },
    [],
  );

  const handleSave = async () => {
    // CONSTRAINT: this page edits an EXISTING mission only.
    // POST /missions is forbidden here per ADR-0013 / spec §2.
    // The slim create modal in MissionCreateModal.tsx is the ONLY
    // POST /missions code path. Any attempt to create here would
    // reintroduce the duplicate-mission bug from 2026-05-03.
    if (!id) return;
    const validation = form.validate();
    if (validation.hasErrors) return;

    setSaving(true);
    try {
      const v = form.values;
      const payload: Record<string, unknown> = {
        title: v.title,
        customer_id: v.customer_id || null,
        mission_type: v.mission_type,
        description: v.description,
        mission_date: v.mission_date ? v.mission_date.toISOString().split('T')[0] : null,
        location_name: v.location_name,
        is_billable: v.is_billable,
      };

      // UNAS fields — only include when the operator actually set them
      // (avoids sending columns that may not exist on databases upgraded
      // from very old versions).
      const trimmedPath = unasFolderPath.trim();
      const trimmedUrl = downloadLinkUrl.trim();
      if (trimmedPath) payload.unas_folder_path = trimmedPath;
      else payload.unas_folder_path = null;
      if (trimmedUrl) payload.download_link_url = trimmedUrl;
      else payload.download_link_url = null;
      if (trimmedUrl && downloadLinkExpiresAt) {
        payload.download_link_expires_at = downloadLinkExpiresAt.toISOString();
      } else {
        payload.download_link_expires_at = null;
      }

      await api.put(`/missions/${id}`, payload);
      // Reset dirty state so a subsequent Cancel doesn't re-prompt.
      // Mantine: re-baseline initialValues to current values.
      // UNAS trio: snapshot what we just persisted.
      form.resetDirty(form.values);
      setUnasBaseline({
        folderPath: trimmedPath,
        linkUrl: trimmedUrl,
        linkExpiresAtIso:
          trimmedUrl && downloadLinkExpiresAt
            ? downloadLinkExpiresAt.toISOString()
            : '',
      });
      notifications.show({
        title: 'Mission Updated',
        message: v.title,
        color: 'cyan',
      });
      navigate(`/missions/${id}`);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } }; message?: string })?.response?.data
          ?.detail ?? (err as { message?: string })?.message ?? 'Unknown error';
      console.error('[MissionDetailsEdit] save failed', err);
      notifications.show({
        title: 'Save failed',
        message: `Could not save mission: ${detail}`,
        color: 'red',
      });
    } finally {
      setSaving(false);
    }
  };

  // Dirty calc: either the Mantine-tracked form changed OR one of the
  // UNAS fields drifted from its loaded baseline. While `loading`,
  // form.isDirty() can be true mid-setValues — gate to false.
  const unasDirty =
    !loading &&
    (unasFolderPath !== unasBaseline.folderPath ||
      downloadLinkUrl !== unasBaseline.linkUrl ||
      (downloadLinkExpiresAt ? downloadLinkExpiresAt.toISOString() : '') !==
        unasBaseline.linkExpiresAtIso);
  const isDirty = !loading && (form.isDirty() || unasDirty);

  const { showConfirm, setShowConfirm, guardedNavigate, confirmAndNavigate } =
    useDirtyGuard({ isDirty, navigate });

  const handleCancel = () => {
    guardedNavigate(`/missions/${id}`);
  };

  if (loading) {
    return (
      <Stack gap="lg" align="center" py="xl">
        <Loader color="cyan" size="lg" />
        <Text c="#5a6478">Loading mission details...</Text>
      </Stack>
    );
  }

  const linkActive =
    downloadLinkUrl && downloadLinkExpiresAt && new Date() < downloadLinkExpiresAt;

  return (
    <Stack gap="lg">
      <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
        EDIT MISSION DETAILS
      </Title>

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <TextInput
            label="Mission Title"
            required
            {...form.getInputProps('title')}
            styles={inputStyles}
          />
          <Select
            label="Customer"
            placeholder="Select customer"
            data={customers.map((c) => ({
              value: c.id,
              label: `${c.name}${c.company ? ` (${c.company})` : ''}`,
            }))}
            searchable
            clearable
            {...form.getInputProps('customer_id')}
            styles={inputStyles}
          />
          <Group grow>
            <Select
              label="Mission Type"
              data={missionTypes}
              {...form.getInputProps('mission_type')}
              styles={inputStyles}
            />
            <DateInput
              label="Date"
              clearable
              {...form.getInputProps('mission_date')}
              styles={inputStyles}
            />
          </Group>

          <Box style={{ position: 'relative' }}>
            <TextInput
              label="Location"
              {...form.getInputProps('location_name')}
              onChange={(e) => {
                form.setFieldValue('location_name', e.currentTarget.value);
                searchAddress(e.currentTarget.value);
              }}
              onFocus={() => {
                if (addressSuggestions.length > 0) setShowAddressDropdown(true);
              }}
              onBlur={() => {
                // Delay so click on suggestion can fire first.
                setTimeout(() => setShowAddressDropdown(false), 200);
              }}
              styles={inputStyles}
            />
            {showAddressDropdown && addressSuggestions.length > 0 && (
              <Paper
                shadow="md"
                style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  right: 0,
                  zIndex: 10,
                  background: '#0e1117',
                  border: '1px solid #1a1f2e',
                  maxHeight: 240,
                  overflowY: 'auto',
                }}
              >
                {addressSuggestions.map((s, i) => (
                  <Box
                    key={i}
                    onMouseDown={(e) => {
                      // mouseDown beats blur — keeps the suggestion clickable.
                      e.preventDefault();
                      form.setFieldValue('location_name', s.display_name);
                      setShowAddressDropdown(false);
                    }}
                    style={{
                      padding: '8px 12px',
                      color: '#e8edf2',
                      fontSize: 13,
                      cursor: 'pointer',
                      borderBottom: '1px solid #1a1f2e',
                    }}
                  >
                    {s.display_name}
                  </Box>
                ))}
              </Paper>
            )}
          </Box>

          <Textarea
            label="Description"
            {...form.getInputProps('description')}
            styles={inputStyles}
            minRows={3}
            autosize
          />
          <Switch
            label="Billable Mission"
            color="cyan"
            checked={form.values.is_billable}
            onChange={(e) => form.setFieldValue('is_billable', e.currentTarget.checked)}
          />

          <Text
            c="#00d4ff"
            fw={600}
            mt="md"
            style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px', fontSize: 14 }}
          >
            MISSION FOOTAGE (UNAS)
          </Text>
          <TextInput
            label="UNAS Folder Path"
            placeholder="/missions/2026-05-03-solar-inspection/"
            value={unasFolderPath}
            onChange={(e) => setUnasFolderPath(e.currentTarget.value)}
            styles={inputStyles}
          />
          <TextInput
            label="Download Link URL"
            placeholder="Paste share link from UNAS web interface"
            value={downloadLinkUrl}
            onChange={(e) => setDownloadLinkUrl(e.currentTarget.value)}
            styles={inputStyles}
          />
          {downloadLinkUrl && (
            <DateInput
              label="Link Expires At"
              clearable
              value={downloadLinkExpiresAt}
              onChange={setDownloadLinkExpiresAt}
              styles={inputStyles}
            />
          )}
          {downloadLinkUrl && downloadLinkExpiresAt && (
            <Badge color={linkActive ? 'green' : 'red'} variant="light" size="sm">
              {linkActive ? 'Link Active' : 'Link Expired'}
            </Badge>
          )}
          <Text
            c="#5a6478"
            size="xs"
            style={{ fontFamily: "'Share Tech Mono', monospace" }}
          >
            Create a share link in your UNAS web interface, then paste the URL here.
          </Text>
        </Stack>
      </Card>

      <Group justify="flex-end" gap="md">
        <Button
          variant="default"
          onClick={handleCancel}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          CANCEL
        </Button>
        <Button
          color="cyan"
          loading={saving}
          onClick={handleSave}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          SAVE CHANGES
        </Button>
      </Group>

      <UnsavedChangesModal
        opened={showConfirm}
        onKeepEditing={() => setShowConfirm(false)}
        onDiscard={confirmAndNavigate}
      />
    </Stack>
  );
}
