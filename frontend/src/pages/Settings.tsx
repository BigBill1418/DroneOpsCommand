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
  NumberInput,
  PasswordInput,
  Tabs,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconX, IconPlus, IconEdit, IconTrash, IconCurrencyDollar, IconMail, IconSend, IconBrandPaypal, IconCash, IconDrone, IconPlugConnected, IconMapPin, IconSearch, IconSignature, IconUpload, IconSettings, IconReceipt, IconPlane, IconPalette, IconWorldWww, IconKey, IconUser, IconLock } from '@tabler/icons-react';
import api from '../api/client';
import { Aircraft, RateTemplate } from '../api/types';
import { inputStyles, cardStyle } from '../components/shared/styles';
import { invalidateBrandingCache } from '../hooks/useBranding';

const tabStyles = {
  tab: {
    color: '#5a6478',
    fontFamily: "'Share Tech Mono', monospace",
    letterSpacing: '1px',
    fontSize: '12px',
    '&[data-active]': { color: '#00d4ff', borderColor: '#00d4ff' },
  },
  list: { borderColor: '#1a1f2e' },
};

export default function Settings() {
  const [llmStatus, setLlmStatus] = useState<{ status: string; configured_model?: string; model_available?: boolean; models?: string[] } | null>(null);
  const [llmLoading, setLlmLoading] = useState(true);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [aircraftModal, setAircraftModal] = useState(false);
  const [editingAircraftId, setEditingAircraftId] = useState<string | null>(null);
  const [rateTemplates, setRateTemplates] = useState<RateTemplate[]>([]);
  const [rateModal, setRateModal] = useState(false);
  const [editingRateId, setEditingRateId] = useState<string | null>(null);
  const [smtpSaving, setSmtpSaving] = useState(false);
  const [smtpTesting, setSmtpTesting] = useState(false);
  const [paymentSaving, setPaymentSaving] = useState(false);
  const [odlSaving, setOdlSaving] = useState(false);
  const [odlTesting, setOdlTesting] = useState(false);
  const [odlStatus, setOdlStatus] = useState<{ status: string; message?: string } | null>(null);
  const [weatherSaving, setWeatherSaving] = useState(false);
  const [weatherLooking, setWeatherLooking] = useState(false);
  const [weatherQuery, setWeatherQuery] = useState('');
  const [tosUploaded, setTosUploaded] = useState(false);
  const [tosUploading, setTosUploading] = useState(false);
  const [brandingSaving, setBrandingSaving] = useState(false);
  const [djiSaving, setDjiSaving] = useState(false);
  const [djiTesting, setDjiTesting] = useState(false);
  const [djiStatus, setDjiStatus] = useState<{ status: string; message?: string } | null>(null);
  const [odlImporting, setOdlImporting] = useState(false);
  const [odlImportProgress, setOdlImportProgress] = useState({ current: 0, total: 0, imported: 0, skipped: 0, errors: 0, currentFlight: '' });
  const [accountSaving, setAccountSaving] = useState(false);
  const [currentUsername, setCurrentUsername] = useState('');

  const aircraftForm = useForm({
    initialValues: { model_name: '', manufacturer: 'DJI', specs_json: '{}' },
  });

  const rateForm = useForm({
    initialValues: { name: '', description: '', category: 'other', default_quantity: 1, default_unit: '', default_rate: 0 },
  });

  const smtpForm = useForm({
    initialValues: {
      smtp_host: '',
      smtp_port: '587',
      smtp_user: '',
      smtp_password: '',
      smtp_from_email: '',
      smtp_from_name: '',
      smtp_use_tls: 'true',
    },
  });

  const paymentForm = useForm({
    initialValues: { paypal_link: '', venmo_link: '' },
  });

  const odlForm = useForm({
    initialValues: { opendronelog_url: '' },
  });

  const djiForm = useForm({
    initialValues: { dji_api_key: '' },
  });

  const accountForm = useForm({
    initialValues: { current_password: '', new_username: '', new_password: '', confirm_password: '' },
    validate: {
      current_password: (v) => (v.length === 0 ? 'Current password is required' : null),
      new_password: (v) => (v && v.length > 0 && v.length < 6 ? 'Password must be at least 6 characters' : null),
      confirm_password: (v, values) => (v !== values.new_password ? 'Passwords do not match' : null),
    },
  });

  const weatherForm = useForm({
    initialValues: { weather_lat: '', weather_lon: '', weather_label: '', weather_airport_icao: '' },
  });

  const brandingForm = useForm({
    initialValues: {
      company_name: '',
      company_tagline: '',
      company_website: '',
      company_social_url: '',
      company_contact_email: '',
    },
  });

  useEffect(() => {
    api.get('/llm/status').then((r) => setLlmStatus(r.data)).catch(() => setLlmStatus({ status: 'offline' })).finally(() => setLlmLoading(false));
    api.get('/aircraft').then((r) => setAircraft(r.data)).catch(() => setAircraft([]));
    api.get('/rate-templates').then((r) => setRateTemplates(r.data)).catch(() => setRateTemplates([]));
    api.get('/settings/smtp').then((r) => smtpForm.setValues(r.data)).catch(() => {});
    api.get('/settings/payment').then((r) => paymentForm.setValues(r.data)).catch(() => {});
    api.get('/settings/opendronelog').then((r) => odlForm.setValues(r.data)).catch(() => {});
    api.get('/settings/dji').then((r) => djiForm.setValues(r.data)).catch(() => {});
    api.get('/auth/account').then((r) => { setCurrentUsername(r.data.username); accountForm.setFieldValue('new_username', r.data.username); }).catch(() => {});
    api.get('/settings/weather').then((r) => weatherForm.setValues(r.data)).catch(() => {});
    api.get('/intake/default-tos-status').then((r) => setTosUploaded(r.data.uploaded)).catch(() => {});
    api.get('/settings/branding').then((r) => brandingForm.setValues(r.data)).catch(() => {});
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
    try {
      await api.delete(`/aircraft/${id}`);
      setAircraft((prev) => prev.filter((a) => a.id !== id));
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

  const handleSaveSmtp = async (values: typeof smtpForm.values) => {
    setSmtpSaving(true);
    try {
      await api.put('/settings/smtp', values);
      notifications.show({ title: 'Saved', message: 'SMTP settings updated', color: 'cyan' });
      const r = await api.get('/settings/smtp');
      smtpForm.setValues(r.data);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save SMTP settings', color: 'red' });
    } finally {
      setSmtpSaving(false);
    }
  };

  const handleTestSmtp = async () => {
    setSmtpTesting(true);
    try {
      const r = await api.post('/settings/smtp/test');
      if (r.data.status === 'ok') {
        notifications.show({ title: 'Success', message: r.data.message, color: 'green' });
      } else {
        notifications.show({ title: 'SMTP Error', message: r.data.message, color: 'red' });
      }
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to test SMTP', color: 'red' });
    } finally {
      setSmtpTesting(false);
    }
  };

  const handleSaveOdl = async (values: typeof odlForm.values) => {
    setOdlSaving(true);
    try {
      await api.put('/settings/opendronelog', values);
      notifications.show({ title: 'Saved', message: 'OpenDroneLog URL updated', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save', color: 'red' });
    } finally {
      setOdlSaving(false);
    }
  };

  const handleTestOdl = async () => {
    setOdlTesting(true);
    setOdlStatus(null);
    try {
      const r = await api.get('/flights/test');
      setOdlStatus(r.data);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setOdlStatus({ status: 'error', message: axiosErr.response?.data?.detail || 'Connection failed' });
    } finally {
      setOdlTesting(false);
    }
  };

  const handleSaveDji = async (values: typeof djiForm.values) => {
    setDjiSaving(true);
    try {
      await api.put('/settings/dji', values);
      notifications.show({ title: 'Saved', message: 'DJI API key updated', color: 'cyan' });
      const r = await api.get('/settings/dji');
      djiForm.setValues(r.data);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save DJI API key', color: 'red' });
    } finally {
      setDjiSaving(false);
    }
  };

  const handleTestDji = async () => {
    setDjiTesting(true);
    try {
      const r = await api.post('/settings/dji/test');
      setDjiStatus(r.data);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setDjiStatus({ status: 'error', message: axiosErr.response?.data?.detail || 'Test failed' });
    } finally {
      setDjiTesting(false);
    }
  };

  const handleSaveAccount = async (values: typeof accountForm.values) => {
    if (!values.new_username && !values.new_password) {
      notifications.show({ title: 'Nothing to update', message: 'Change username or password', color: 'orange' });
      return;
    }
    setAccountSaving(true);
    try {
      const payload: Record<string, string> = { current_password: values.current_password };
      if (values.new_username && values.new_username !== currentUsername) {
        payload.new_username = values.new_username;
      }
      if (values.new_password) {
        payload.new_password = values.new_password;
      }
      const r = await api.put('/auth/account', payload);
      // Update stored tokens if returned
      if (r.data.access_token) {
        localStorage.setItem('access_token', r.data.access_token);
      }
      if (r.data.refresh_token) {
        localStorage.setItem('refresh_token', r.data.refresh_token);
      }
      setCurrentUsername(r.data.username);
      accountForm.setValues({ current_password: '', new_username: r.data.username, new_password: '', confirm_password: '' });
      notifications.show({ title: 'Account Updated', message: 'Your credentials have been changed', color: 'green' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to update account', color: 'red' });
    } finally {
      setAccountSaving(false);
    }
  };

  const handleOdlImport = async () => {
    setOdlImporting(true);
    setOdlImportProgress({ current: 0, total: 0, imported: 0, skipped: 0, errors: 0, currentFlight: '' });
    try {
      const token = localStorage.getItem('access_token');
      const resp = await fetch('/api/flight-library/import/opendronelog/stream', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Import failed' }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'progress') {
              setOdlImportProgress({
                current: event.current,
                total: event.total,
                imported: event.imported,
                skipped: event.skipped,
                errors: event.errors,
                currentFlight: event.flight_name || '',
              });
            } else if (event.type === 'complete') {
              setOdlImportProgress((p) => ({ ...p, current: event.total, total: event.total }));
              notifications.show({
                title: 'Import Complete',
                message: `${event.imported} imported, ${event.skipped} skipped, ${event.errors} errors`,
                color: event.errors > 0 ? 'orange' : 'green',
                autoClose: 8000,
              });
            } else if (event.type === 'error') {
              notifications.show({ title: 'Import Error', message: event.message, color: 'red', autoClose: 8000 });
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (err: any) {
      notifications.show({ title: 'Import Failed', message: err.message || 'Migration failed', color: 'red', autoClose: 8000 });
    } finally {
      setOdlImporting(false);
    }
  };

  const handleSavePayment = async (values: typeof paymentForm.values) => {
    setPaymentSaving(true);
    try {
      await api.put('/settings/payment', values);
      notifications.show({ title: 'Saved', message: 'Payment links updated', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save payment links', color: 'red' });
    } finally {
      setPaymentSaving(false);
    }
  };

  const handleSaveWeather = async (values: typeof weatherForm.values) => {
    setWeatherSaving(true);
    try {
      await api.put('/settings/weather', values);
      notifications.show({ title: 'Saved', message: `Weather location set to ${values.weather_label || 'configured coordinates'}`, color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save weather location', color: 'red' });
    } finally {
      setWeatherSaving(false);
    }
  };

  const handleLookupLocation = async () => {
    if (!weatherQuery.trim()) return;
    setWeatherLooking(true);
    try {
      const r = await api.post('/settings/weather/lookup', { query: weatherQuery.trim() });
      if (r.data.error) {
        notifications.show({ title: 'Not Found', message: r.data.error, color: 'orange' });
      } else {
        weatherForm.setValues({
          weather_lat: r.data.lat,
          weather_lon: r.data.lon,
          weather_label: r.data.label,
          weather_airport_icao: r.data.airport_icao,
        });
        notifications.show({ title: 'Location Found', message: `${r.data.label} — nearest airport: ${r.data.airport_icao || 'none found'}`, color: 'green' });
      }
    } catch {
      notifications.show({ title: 'Error', message: 'Location lookup failed', color: 'red' });
    } finally {
      setWeatherLooking(false);
    }
  };

  const handleSaveBranding = async (values: typeof brandingForm.values) => {
    setBrandingSaving(true);
    try {
      await api.put('/settings/branding', values);
      invalidateBrandingCache();
      notifications.show({ title: 'Saved', message: 'Branding settings updated — reload to see changes in header', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save branding settings', color: 'red' });
    } finally {
      setBrandingSaving(false);
    }
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
    <Stack gap="md">
      <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>SETTINGS</Title>

      <Tabs defaultValue="branding" styles={tabStyles}>
        <Tabs.List>
          <Tabs.Tab value="branding" leftSection={<IconPalette size={14} />}>
            BRANDING
          </Tabs.Tab>
          <Tabs.Tab value="general" leftSection={<IconSettings size={14} />}>
            GENERAL
          </Tabs.Tab>
          <Tabs.Tab value="email" leftSection={<IconMail size={14} />}>
            EMAIL & BILLING
          </Tabs.Tab>
          <Tabs.Tab value="flight" leftSection={<IconDrone size={14} />}>
            FLIGHT DATA
          </Tabs.Tab>
          <Tabs.Tab value="fleet" leftSection={<IconPlane size={14} />}>
            FLEET & RATES
          </Tabs.Tab>
          <Tabs.Tab value="account" leftSection={<IconUser size={14} />}>
            ACCOUNT
          </Tabs.Tab>
        </Tabs.List>

        {/* ═══ BRANDING TAB ═══ */}
        <Tabs.Panel value="branding" pt="md">
          <Stack gap="md">
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconPalette size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>COMPANY BRANDING</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                These settings control how your company appears throughout the app, in emails, PDF reports, and customer-facing pages.
              </Text>
              <form onSubmit={brandingForm.onSubmit(handleSaveBranding)}>
                <Stack gap="sm">
                  <TextInput
                    label="Company Name"
                    placeholder="Your Company Name"
                    {...brandingForm.getInputProps('company_name')}
                    styles={inputStyles}
                  />
                  <TextInput
                    label="Tagline"
                    placeholder="Professional Aerial Operations"
                    {...brandingForm.getInputProps('company_tagline')}
                    styles={inputStyles}
                  />
                  <TextInput
                    label="Website"
                    placeholder="https://yourcompany.com"
                    leftSection={<IconWorldWww size={14} />}
                    {...brandingForm.getInputProps('company_website')}
                    styles={inputStyles}
                  />
                  <TextInput
                    label="Social Media URL"
                    placeholder="https://facebook.com/yourcompany"
                    {...brandingForm.getInputProps('company_social_url')}
                    styles={inputStyles}
                  />
                  <TextInput
                    label="Contact Email"
                    placeholder="info@yourcompany.com"
                    {...brandingForm.getInputProps('company_contact_email')}
                    styles={inputStyles}
                  />
                  <Button type="submit" color="cyan" loading={brandingSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE BRANDING
                  </Button>
                </Stack>
              </form>
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* ═══ GENERAL TAB ═══ */}
        <Tabs.Panel value="general" pt="md">
          <Stack gap="md">
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

            {/* Weather / Airspace Location */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconMapPin size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>WEATHER & AIRSPACE LOCATION</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Set the location for dashboard weather, METAR, TFR, and NOTAM monitoring. Enter a zip code or city name to auto-fill.
              </Text>
              <Group mb="md" align="end">
                <TextInput
                  label="Search by Zip Code or City"
                  placeholder="97402 or Eugene, OR"
                  value={weatherQuery}
                  onChange={(e) => setWeatherQuery(e.currentTarget.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleLookupLocation(); } }}
                  styles={inputStyles}
                  style={{ flex: 1 }}
                />
                <Button
                  leftSection={<IconSearch size={14} />}
                  color="cyan"
                  variant="light"
                  loading={weatherLooking}
                  onClick={handleLookupLocation}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                >
                  LOOKUP
                </Button>
              </Group>
              <form onSubmit={weatherForm.onSubmit(handleSaveWeather)}>
                <Stack gap="sm">
                  <TextInput label="Location Label" placeholder="Eugene, OR" {...weatherForm.getInputProps('weather_label')} styles={inputStyles} />
                  <Group grow>
                    <TextInput label="Latitude" placeholder="44.0500" {...weatherForm.getInputProps('weather_lat')} styles={inputStyles} />
                    <TextInput label="Longitude" placeholder="-123.0900" {...weatherForm.getInputProps('weather_lon')} styles={inputStyles} />
                  </Group>
                  <TextInput label="Nearest Airport (ICAO)" placeholder="KEUG" {...weatherForm.getInputProps('weather_airport_icao')} styles={inputStyles} />
                  <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                    ICAO airport code is used for METAR, TFR, and NOTAM data. The lookup fills this automatically.
                  </Text>
                  <Button type="submit" color="cyan" loading={weatherSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE WEATHER LOCATION
                  </Button>
                </Stack>
              </form>
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* ═══ EMAIL & BILLING TAB ═══ */}
        <Tabs.Panel value="email" pt="md">
          <Stack gap="md">
            {/* SMTP Settings */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group justify="space-between" mb="md">
                <Group gap="sm">
                  <IconMail size={20} color="#00d4ff" />
                  <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>SMTP / EMAIL</Title>
                </Group>
                <Button
                  leftSection={<IconSend size={14} />}
                  size="xs"
                  variant="light"
                  color="cyan"
                  loading={smtpTesting}
                  onClick={handleTestSmtp}
                >
                  Send Test
                </Button>
              </Group>
              <form onSubmit={smtpForm.onSubmit(handleSaveSmtp)}>
                <Stack gap="sm">
                  <Group grow>
                    <TextInput label="SMTP Host" placeholder="smtp.gmail.com" {...smtpForm.getInputProps('smtp_host')} styles={inputStyles} />
                    <TextInput label="SMTP Port" placeholder="587" {...smtpForm.getInputProps('smtp_port')} styles={inputStyles} />
                  </Group>
                  <Group grow>
                    <TextInput label="Username" placeholder="user@example.com" {...smtpForm.getInputProps('smtp_user')} styles={inputStyles} />
                    <PasswordInput label="Password" placeholder="App password or SMTP key" {...smtpForm.getInputProps('smtp_password')} styles={inputStyles} />
                  </Group>
                  <Group grow>
                    <TextInput label="From Email" placeholder="reports@yourcompany.com" {...smtpForm.getInputProps('smtp_from_email')} styles={inputStyles} />
                    <TextInput label="From Name" placeholder="Your Company Drone Operations" {...smtpForm.getInputProps('smtp_from_name')} styles={inputStyles} />
                  </Group>
                  <Switch
                    label="Use TLS"
                    checked={smtpForm.values.smtp_use_tls === 'true'}
                    onChange={(e) => smtpForm.setFieldValue('smtp_use_tls', e.currentTarget.checked ? 'true' : 'false')}
                    color="cyan"
                    styles={{ label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' } }}
                  />
                  <Button type="submit" color="cyan" loading={smtpSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE SMTP SETTINGS
                  </Button>
                </Stack>
              </form>
            </Card>

            {/* Terms of Service PDF */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconSignature size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>TERMS OF SERVICE</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Upload the default TOS PDF that customers will review and sign during onboarding.
              </Text>
              <Group>
                <Badge color={tosUploaded ? 'green' : 'orange'} variant="light">
                  {tosUploaded ? 'TOS PDF UPLOADED' : 'NO TOS PDF'}
                </Badge>
                <Button
                  leftSection={<IconUpload size={14} />}
                  color="cyan"
                  variant="light"
                  size="xs"
                  loading={tosUploading}
                  onClick={() => {
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.accept = '.pdf';
                    input.onchange = async (e: Event) => {
                      const file = (e.target as HTMLInputElement).files?.[0];
                      if (!file) return;
                      setTosUploading(true);
                      try {
                        const formData = new FormData();
                        formData.append('file', file);
                        await api.post('/intake/upload-default-tos', formData);
                        setTosUploaded(true);
                        notifications.show({ title: 'Uploaded', message: 'Default TOS PDF uploaded', color: 'cyan' });
                      } catch {
                        notifications.show({ title: 'Error', message: 'Failed to upload TOS PDF', color: 'red' });
                      } finally {
                        setTosUploading(false);
                      }
                    };
                    input.click();
                  }}
                >
                  Upload PDF
                </Button>
              </Group>
            </Card>

            {/* Payment Links */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconCash size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>PAYMENT LINKS</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                These links appear on invoices that are not marked as paid in full.
              </Text>
              <form onSubmit={paymentForm.onSubmit(handleSavePayment)}>
                <Stack gap="sm">
                  <TextInput
                    label="PayPal Link"
                    placeholder="https://paypal.me/yourname"
                    leftSection={<IconBrandPaypal size={14} />}
                    {...paymentForm.getInputProps('paypal_link')}
                    styles={inputStyles}
                  />
                  <TextInput
                    label="Venmo Link"
                    placeholder="https://venmo.com/yourname"
                    {...paymentForm.getInputProps('venmo_link')}
                    styles={inputStyles}
                  />
                  <Button type="submit" color="cyan" loading={paymentSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE PAYMENT LINKS
                  </Button>
                </Stack>
              </form>
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* ═══ FLIGHT DATA TAB ═══ */}
        <Tabs.Panel value="flight" pt="md">
          <Stack gap="md">
            {/* OpenDroneLog */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group justify="space-between" mb="md">
                <Group gap="sm">
                  <IconDrone size={20} color="#00d4ff" />
                  <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>OPENDRONELOG</Title>
                </Group>
                <Button
                  leftSection={<IconPlugConnected size={14} />}
                  size="xs"
                  variant="light"
                  color="cyan"
                  loading={odlTesting}
                  onClick={handleTestOdl}
                >
                  Test Connection
                </Button>
              </Group>
              <form onSubmit={odlForm.onSubmit(handleSaveOdl)}>
                <Stack gap="sm">
                  <TextInput
                    label="OpenDroneLog URL"
                    placeholder="http://host.docker.internal:8080 or http://192.168.x.x:8080"
                    {...odlForm.getInputProps('opendronelog_url')}
                    styles={inputStyles}
                  />
                  <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                    If OpenDroneLog runs on the same machine as Docker, use http://host.docker.internal:PORT
                  </Text>
                  {odlStatus && (
                    <Group gap="xs">
                      <Badge color={odlStatus.status === 'online' ? 'green' : 'red'} size="sm">
                        {odlStatus.status}
                      </Badge>
                      <Text c={odlStatus.status === 'online' ? '#e8edf2' : '#ff6b6b'} size="sm">
                        {odlStatus.message}
                      </Text>
                    </Group>
                  )}
                  <Button type="submit" color="cyan" loading={odlSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE
                  </Button>
                  {odlStatus?.status === 'online' && (
                    <Button
                      variant="light"
                      color="orange"
                      leftSection={<IconDrone size={14} />}
                      onClick={handleOdlImport}
                      loading={odlImporting}
                      styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                    >
                      IMPORT ALL FLIGHTS TO LOCAL LIBRARY
                    </Button>
                  )}
                  {odlImporting && (
                    <Card padding="sm" radius="sm" style={{ background: '#050608', border: '1px solid #1a1f2e' }}>
                      <Group justify="space-between" mb={4}>
                        <Text size="xs" c="#00d4ff" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                          IMPORTING FLIGHTS...
                        </Text>
                        <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                          {odlImportProgress.current} / {odlImportProgress.total}
                        </Text>
                      </Group>
                      <div style={{ width: '100%', height: 6, background: '#1a1f2e', borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{
                          width: odlImportProgress.total > 0 ? `${(odlImportProgress.current / odlImportProgress.total) * 100}%` : '0%',
                          height: '100%',
                          background: '#00d4ff',
                          borderRadius: 3,
                          transition: 'width 0.3s ease',
                        }} />
                      </div>
                      <Text size="xs" c="#5a6478" mt={4} style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                        {odlImportProgress.imported} imported · {odlImportProgress.skipped} skipped · {odlImportProgress.errors} errors
                      </Text>
                      {odlImportProgress.currentFlight && (
                        <Text size="xs" c="#5a6478" mt={2} style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }} lineClamp={1}>
                          Processing: {odlImportProgress.currentFlight}
                        </Text>
                      )}
                    </Card>
                  )}
                </Stack>
              </form>
            </Card>

            {/* DJI API Key */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconKey size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>DJI API KEY</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Enter your DJI Developer API key for direct integration with DJI cloud services.
              </Text>
              <form onSubmit={djiForm.onSubmit(handleSaveDji)}>
                <Stack gap="sm">
                  <PasswordInput
                    label="DJI API Key"
                    placeholder="Enter your DJI API key"
                    leftSection={<IconKey size={14} />}
                    {...djiForm.getInputProps('dji_api_key')}
                    styles={inputStyles}
                  />
                  <Group>
                    <Button type="submit" color="cyan" loading={djiSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                      SAVE DJI API KEY
                    </Button>
                    <Button
                      variant="light"
                      color={djiStatus?.status === 'online' ? 'green' : djiStatus?.status === 'error' ? 'red' : 'gray'}
                      loading={djiTesting}
                      onClick={handleTestDji}
                      leftSection={djiStatus?.status === 'online' ? <IconCheck size={14} /> : djiStatus?.status === 'error' ? <IconX size={14} /> : <IconPlugConnected size={14} />}
                      styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                    >
                      TEST KEY
                    </Button>
                  </Group>
                  {djiStatus && (
                    <Badge
                      color={djiStatus.status === 'online' ? 'green' : djiStatus.status === 'unknown' ? 'yellow' : 'red'}
                      variant="light"
                      size="lg"
                      leftSection={djiStatus.status === 'online' ? <IconCheck size={12} /> : <IconX size={12} />}
                    >
                      {djiStatus.message || djiStatus.status}
                    </Badge>
                  )}
                </Stack>
              </form>
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* ═══ FLEET & RATES TAB ═══ */}
        <Tabs.Panel value="fleet" pt="md">
          <Stack gap="md">
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
                th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', borderBottom: '1px solid #1a1f2e' },
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
          </Stack>
        </Tabs.Panel>

        {/* ═══ ACCOUNT TAB ═══ */}
        <Tabs.Panel value="account" pt="md">
          <Stack gap="md">
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconUser size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>ADMIN ACCOUNT</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Change your login username and password. You must enter your current password to confirm changes.
              </Text>
              <form onSubmit={accountForm.onSubmit(handleSaveAccount)}>
                <Stack gap="sm">
                  <TextInput
                    label="Current Username"
                    value={currentUsername}
                    readOnly
                    styles={{
                      ...inputStyles,
                      input: { ...inputStyles.input, opacity: 0.6 },
                    }}
                  />
                  <PasswordInput
                    label="Current Password"
                    placeholder="Enter your current password"
                    required
                    leftSection={<IconLock size={14} />}
                    {...accountForm.getInputProps('current_password')}
                    styles={inputStyles}
                  />
                  <div style={{ borderTop: '1px solid #1a1f2e', margin: '8px 0' }} />
                  <TextInput
                    label="New Username"
                    placeholder="Leave unchanged to keep current username"
                    leftSection={<IconUser size={14} />}
                    {...accountForm.getInputProps('new_username')}
                    styles={inputStyles}
                  />
                  <PasswordInput
                    label="New Password"
                    placeholder="Leave blank to keep current password"
                    leftSection={<IconLock size={14} />}
                    {...accountForm.getInputProps('new_password')}
                    styles={inputStyles}
                  />
                  <PasswordInput
                    label="Confirm New Password"
                    placeholder="Re-enter new password"
                    leftSection={<IconLock size={14} />}
                    {...accountForm.getInputProps('confirm_password')}
                    styles={inputStyles}
                  />
                  <Button type="submit" color="cyan" loading={accountSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    UPDATE ACCOUNT
                  </Button>
                </Stack>
              </form>
            </Card>
          </Stack>
        </Tabs.Panel>
      </Tabs>

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
