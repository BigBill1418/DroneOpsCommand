import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Image,
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
import { IconCheck, IconX, IconPlus, IconEdit, IconTrash, IconCurrencyDollar, IconMail, IconSend, IconBrandPaypal, IconCash, IconDrone, IconPlugConnected, IconMapPin, IconSearch, IconSignature, IconUpload, IconSettings, IconReceipt, IconPlane, IconPalette, IconWorldWww, IconKey, IconUser, IconLock, IconDatabaseExport, IconDatabaseImport, IconShieldCheck, IconDownload, IconAlertTriangle, IconPhoto } from '@tabler/icons-react';
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
  const [aircraftImageUploading, setAircraftImageUploading] = useState(false);
  const [editingAircraftImage, setEditingAircraftImage] = useState<string | null>(null);
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
  const [purgeConfirmOpen, setPurgeConfirmOpen] = useState(false);
  const [purgeChecked, setPurgeChecked] = useState(false);
  const [purging, setPurging] = useState(false);
  const [odlImporting, setOdlImporting] = useState(false);
  const [odlImportProgress, setOdlImportProgress] = useState({ current: 0, total: 0, imported: 0, skipped: 0, errors: 0, currentFlight: '' });
  const [accountSaving, setAccountSaving] = useState(false);
  const [currentUsername, setCurrentUsername] = useState('');
  const [backupCreating, setBackupCreating] = useState(false);
  const [backupResult, setBackupResult] = useState<{ filename: string; sha256: string; objects: number; size: number } | null>(null);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreValidation, setRestoreValidation] = useState<{ valid: boolean; filename: string; sha256: string; size_bytes: number; toc_entries: number } | null>(null);
  const [restoreValidating, setRestoreValidating] = useState(false);
  const [restoreRunning, setRestoreRunning] = useState(false);
  const [restoreChecked, setRestoreChecked] = useState(false);
  const [restoreResult, setRestoreResult] = useState<{ restored: boolean; table_count: number; sha256: string } | null>(null);

  // Device API keys (DroneOpsSync)
  const [deviceKeys, setDeviceKeys] = useState<{ id: string; label: string; is_active: boolean; created_at: string; last_used_at: string | null }[]>([]);
  const [deviceKeyCreating, setDeviceKeyCreating] = useState(false);
  const [deviceKeyLabel, setDeviceKeyLabel] = useState('');
  const [newDeviceKey, setNewDeviceKey] = useState<string | null>(null);

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
    api.get('/settings/device-keys').then((r) => setDeviceKeys(r.data)).catch(() => {});
  }, []);

  const handleBackupAndDownload = async () => {
    setBackupCreating(true);
    setBackupResult(null);
    try {
      const resp = await api.post('/backup/create-and-download', {}, { responseType: 'blob' });
      const sha256 = resp.headers['x-backup-sha256'] || '';
      const objects = parseInt(resp.headers['x-backup-objects'] || '0', 10);
      const disposition = resp.headers['content-disposition'] || '';
      const filenameMatch = disposition.match(/filename="(.+?)"/);
      const filename = filenameMatch ? filenameMatch[1] : `doc_backup_${Date.now()}.dump`;
      const blob = new Blob([resp.data], { type: 'application/octet-stream' });
      const size = blob.size;

      // Trigger Save As dialog
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);

      setBackupResult({ filename, sha256, objects, size });
      notifications.show({
        title: 'Backup Created & Verified',
        message: `${filename} — ${objects} objects, SHA-256 verified`,
        color: 'green',
        autoClose: 8000,
      });
    } catch (err: any) {
      notifications.show({ title: 'Backup Failed', message: err.response?.data?.detail || 'Failed to create backup', color: 'red' });
    } finally {
      setBackupCreating(false);
    }
  };

  const handleRestoreFileSelect = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.dump';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      setRestoreFile(file);
      setRestoreValidation(null);
      setRestoreResult(null);
      setRestoreChecked(false);

      // Immediately validate the uploaded file
      setRestoreValidating(true);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await api.post('/backup/validate-upload', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        setRestoreValidation(resp.data);
      } catch (err: any) {
        notifications.show({
          title: 'Invalid Backup File',
          message: err.response?.data?.detail || 'File validation failed — not a valid backup',
          color: 'red',
        });
        setRestoreFile(null);
      } finally {
        setRestoreValidating(false);
      }
    };
    input.click();
  };

  const handleConfirmRestore = async () => {
    if (!restoreFile) return;
    setRestoreRunning(true);
    setRestoreResult(null);
    try {
      const formData = new FormData();
      formData.append('file', restoreFile);
      const resp = await api.post('/backup/restore-from-upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 600000,
      });
      setRestoreResult(resp.data);
      notifications.show({
        title: 'Database Restored Successfully',
        message: `${resp.data.table_count} tables restored from ${restoreFile.name} — SHA-256: ${resp.data.sha256.slice(0, 12)}...`,
        color: 'green',
        autoClose: 10000,
      });
    } catch (err: any) {
      notifications.show({ title: 'Restore Failed', message: err.response?.data?.detail || 'Restore failed', color: 'red' });
    } finally {
      setRestoreRunning(false);
    }
  };

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
    setEditingAircraftImage(a.image_filename || null);
    aircraftForm.setValues({
      model_name: a.model_name,
      manufacturer: a.manufacturer,
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
        setAircraft((prev) => prev.map((a) => a.id === aircraftId ? { ...a, image_filename: resp.data.image_filename } : a));
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
      setAircraft((prev) => prev.map((a) => a.id === aircraftId ? { ...a, image_filename: null } : a));
      notifications.show({ title: 'Removed', message: 'Aircraft image removed', color: 'orange' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to remove image', color: 'red' });
    }
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

  const handlePurgeFlights = async () => {
    setPurging(true);
    try {
      const r = await api.delete('/flight-library/purge/all');
      const batMsg = r.data.batteries_deleted ? `, ${r.data.batteries_deleted} batteries removed` : '';
      notifications.show({ title: 'Data Purged', message: `${r.data.deleted} flights deleted${batMsg}`, color: 'orange' });
      setPurgeConfirmOpen(false);
      setPurgeChecked(false);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to purge flights', color: 'red' });
    } finally {
      setPurging(false);
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
          <Tabs.Tab value="devices" leftSection={<IconKey size={14} />}>
            DEVICE ACCESS
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

            {/* Database Backup & Restore */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="xs">
                <IconDatabaseExport size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>DATABASE BACKUP & RESTORE</Title>
              </Group>
              <Text c="#ff6b6b" size="xs" fw={700} mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                THIS DATA IS IRREPLICABLE. MAINTAIN REGULAR BACKUPS.
              </Text>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Backups are saved directly to your local machine. Restore uploads from your local machine.
                All backups are validated with SHA-256 checksums and archive integrity checks before download and before restore.
              </Text>

              {/* ── BACKUP SECTION ── */}
              <Text size="11px" c="#00d4ff" fw={700} mb="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                BACKUP
              </Text>
              <Group mb="md" align="flex-start">
                <Button
                  leftSection={<IconDownload size={16} />}
                  color="cyan"
                  loading={backupCreating}
                  onClick={handleBackupAndDownload}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  {backupCreating ? 'CREATING & VERIFYING...' : 'BACKUP & SAVE TO COMPUTER'}
                </Button>
              </Group>

              {backupResult && (
                <Card mb="md" padding="sm" radius="sm" style={{ background: 'rgba(46, 204, 64, 0.08)', border: '1px solid #2ecc40' }}>
                  <Group gap="sm">
                    <IconShieldCheck size={20} color="#2ecc40" />
                    <div>
                      <Text size="xs" fw={700} c="#2ecc40">BACKUP CREATED & INTEGRITY VERIFIED</Text>
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        File: {backupResult.filename}
                      </Text>
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        Size: {(backupResult.size / 1024 / 1024).toFixed(2)} MB | Objects: {backupResult.objects} | SHA-256: {backupResult.sha256.slice(0, 24)}...
                      </Text>
                    </div>
                  </Group>
                </Card>
              )}

              {/* ── RESTORE SECTION ── */}
              <Text size="11px" c="#00d4ff" fw={700} mb="xs" mt="md" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                RESTORE
              </Text>
              <Group mb="sm" align="flex-start">
                <Button
                  leftSection={<IconDatabaseImport size={16} />}
                  color="orange"
                  variant="light"
                  loading={restoreValidating}
                  onClick={handleRestoreFileSelect}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  {restoreValidating ? 'VALIDATING...' : 'SELECT BACKUP FILE FROM COMPUTER'}
                </Button>
              </Group>

              {/* Validation result */}
              {restoreValidation && (
                <Card mb="sm" padding="md" radius="sm" style={{ background: 'rgba(0, 212, 255, 0.05)', border: '1px solid #1a1f2e' }}>
                  <Group gap="sm" mb="sm">
                    <IconShieldCheck size={18} color="#2ecc40" />
                    <Text size="xs" fw={700} c="#2ecc40">FILE VALIDATED — READY TO RESTORE</Text>
                  </Group>
                  <Stack gap={4} mb="md">
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      File: {restoreValidation.filename}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      Size: {(restoreValidation.size_bytes / 1024 / 1024).toFixed(2)} MB | Objects: {restoreValidation.toc_entries}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      SHA-256: {restoreValidation.sha256}
                    </Text>
                  </Stack>

                  <Card padding="sm" radius="sm" style={{ background: 'rgba(255, 107, 107, 0.08)', border: '1px solid rgba(255, 107, 107, 0.3)' }}>
                    <Group gap="sm" mb="sm">
                      <IconAlertTriangle size={20} color="#ff6b6b" />
                      <Text c="#ff6b6b" size="sm" fw={700}>WARNING: This will replace ALL current data.</Text>
                    </Group>
                    <Text c="#5a6478" size="xs" mb="sm">
                      Restoring will drop and recreate all database tables. This cannot be undone.
                      Make sure you have a backup of the current database before proceeding.
                    </Text>
                    <Checkbox
                      label="I understand this will permanently replace all current data"
                      checked={restoreChecked}
                      onChange={(e) => setRestoreChecked(e.currentTarget.checked)}
                      color="red"
                      mb="sm"
                    />
                    <Group>
                      <Button
                        color="red"
                        disabled={!restoreChecked}
                        loading={restoreRunning}
                        onClick={handleConfirmRestore}
                        leftSection={<IconDatabaseImport size={16} />}
                        styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                      >
                        {restoreRunning ? 'RESTORING...' : 'RESTORE DATABASE NOW'}
                      </Button>
                      <Button
                        variant="subtle"
                        color="gray"
                        onClick={() => { setRestoreFile(null); setRestoreValidation(null); setRestoreChecked(false); setRestoreResult(null); }}
                      >
                        Cancel
                      </Button>
                    </Group>
                  </Card>
                </Card>
              )}

              {/* Restore success result */}
              {restoreResult && restoreResult.restored && (
                <Card mt="sm" padding="sm" radius="sm" style={{ background: 'rgba(46, 204, 64, 0.08)', border: '1px solid #2ecc40' }}>
                  <Group gap="sm">
                    <IconShieldCheck size={20} color="#2ecc40" />
                    <div>
                      <Text size="xs" fw={700} c="#2ecc40">DATABASE RESTORED & VERIFIED</Text>
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {restoreResult.table_count} tables restored | SHA-256: {restoreResult.sha256.slice(0, 24)}...
                      </Text>
                    </div>
                  </Group>
                </Card>
              )}
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

            {/* Purge Flight Data */}
            <Card padding="lg" radius="md" style={{ ...cardStyle, border: '1px solid rgba(255, 68, 68, 0.2)' }}>
              <Group gap="sm" mb="md">
                <IconTrash size={20} color="#ff4444" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>PURGE FLIGHT DATA</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Delete all flights, batteries, and battery logs from the local database. Use this before re-importing from OpenDroneLog to get a clean sync.
                This action cannot be undone.
              </Text>
              <Button
                color="red"
                variant="light"
                leftSection={<IconTrash size={14} />}
                onClick={() => { setPurgeChecked(false); setPurgeConfirmOpen(true); }}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
              >
                WIPE ALL DATA
              </Button>
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

        {/* ═══ DEVICE ACCESS TAB (DroneOpsSync) ═══ */}
        <Tabs.Panel value="devices" pt="md">
          <Stack gap="md">
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconKey size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>DRONEOPSSYNC API KEYS</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Generate API keys for DroneOpsSync field controllers to upload flight logs without a user login.
                Keys are shown once at creation — copy immediately to your device.
              </Text>

              {/* Create new key */}
              <Group mb="md">
                <TextInput
                  placeholder="Device label (e.g. Field Tablet #1)"
                  value={deviceKeyLabel}
                  onChange={(e) => setDeviceKeyLabel(e.target.value)}
                  styles={{ ...inputStyles, root: { flex: 1 } }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      if (!deviceKeyLabel.trim()) return;
                      setDeviceKeyCreating(true);
                      setNewDeviceKey(null);
                      api.post('/settings/device-keys', { label: deviceKeyLabel.trim() })
                        .then((r) => {
                          setNewDeviceKey(r.data.raw_key);
                          setDeviceKeys((prev) => [r.data, ...prev]);
                          setDeviceKeyLabel('');
                          notifications.show({ title: 'Key Created', message: 'Copy the key below — it won\'t be shown again', color: 'cyan' });
                        })
                        .catch(() => notifications.show({ title: 'Error', message: 'Failed to create key', color: 'red' }))
                        .finally(() => setDeviceKeyCreating(false));
                    }
                  }}
                />
                <Button
                  leftSection={<IconPlus size={14} />}
                  color="cyan"
                  loading={deviceKeyCreating}
                  onClick={() => {
                    if (!deviceKeyLabel.trim()) return;
                    setDeviceKeyCreating(true);
                    setNewDeviceKey(null);
                    api.post('/settings/device-keys', { label: deviceKeyLabel.trim() })
                      .then((r) => {
                        setNewDeviceKey(r.data.raw_key);
                        setDeviceKeys((prev) => [r.data, ...prev]);
                        setDeviceKeyLabel('');
                        notifications.show({ title: 'Key Created', message: 'Copy the key below — it won\'t be shown again', color: 'cyan' });
                      })
                      .catch(() => notifications.show({ title: 'Error', message: 'Failed to create key', color: 'red' }))
                      .finally(() => setDeviceKeyCreating(false));
                  }}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  GENERATE KEY
                </Button>
              </Group>

              {/* Show newly created key */}
              {newDeviceKey && (
                <Card padding="sm" radius="sm" mb="md" style={{ background: '#0a1628', border: '1px solid #00d4ff' }}>
                  <Text size="xs" c="#00d4ff" fw={700} mb={4} style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                    NEW API KEY — COPY NOW (shown once)
                  </Text>
                  <TextInput
                    value={newDeviceKey}
                    readOnly
                    styles={{ input: { background: '#050608', borderColor: '#00d4ff', color: '#00ff88', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px' } }}
                    onClick={(e) => (e.target as HTMLInputElement).select()}
                  />
                  <Button
                    size="xs"
                    variant="light"
                    color="cyan"
                    mt={8}
                    onClick={() => {
                      const copy = (text: string) => {
                        if (navigator.clipboard?.writeText) {
                          return navigator.clipboard.writeText(text);
                        }
                        // Fallback for non-secure contexts (HTTP tunnels, older browsers)
                        const ta = document.createElement('textarea');
                        ta.value = text;
                        ta.style.position = 'fixed';
                        ta.style.opacity = '0';
                        document.body.appendChild(ta);
                        ta.focus();
                        ta.select();
                        document.execCommand('copy');
                        document.body.removeChild(ta);
                        return Promise.resolve();
                      };
                      copy(newDeviceKey)
                        .then(() => notifications.show({ title: 'Copied', message: 'API key copied to clipboard', color: 'cyan' }))
                        .catch(() => notifications.show({ title: 'Copy failed', message: 'Please select the key and copy manually', color: 'orange' }));
                    }}
                    styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
                  >
                    COPY TO CLIPBOARD
                  </Button>
                </Card>
              )}

              {/* Key list */}
              {deviceKeys.length > 0 ? (
                <Table
                  highlightOnHover
                  styles={{
                    table: { color: '#e8edf2' },
                    th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                    td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                  }}
                >
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>DEVICE</Table.Th>
                      <Table.Th>CREATED</Table.Th>
                      <Table.Th>LAST USED</Table.Th>
                      <Table.Th w={60}></Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {deviceKeys.map((dk) => (
                      <Table.Tr key={dk.id}>
                        <Table.Td>
                          <Text size="sm" fw={500}>{dk.label}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                            {new Date(dk.created_at).toLocaleDateString()}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                            {dk.last_used_at ? new Date(dk.last_used_at).toLocaleDateString() : 'Never'}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            size="sm"
                            onClick={() => {
                              api.delete(`/settings/device-keys/${dk.id}`)
                                .then(() => {
                                  setDeviceKeys((prev) => prev.filter((k) => k.id !== dk.id));
                                  notifications.show({ title: 'Revoked', message: `Key "${dk.label}" has been revoked`, color: 'orange' });
                                })
                                .catch(() => notifications.show({ title: 'Error', message: 'Failed to revoke key', color: 'red' }));
                            }}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              ) : (
                <Text c="#5a6478" size="sm" ta="center" py="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  No device keys yet. Generate one to connect DroneOpsSync.
                </Text>
              )}
            </Card>

            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconDrone size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>DRONEOPSSYNC SETUP</Title>
              </Group>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                On your Android device, configure DroneOpsSync with:
              </Text>
              <Stack gap={4} mt="sm">
                <Text c="#e8edf2" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  1. Server URL: your server's LAN IP and port (e.g. http://192.168.1.50:3080)
                </Text>
                <Text c="#e8edf2" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  2. API Key: paste the key generated above
                </Text>
                <Text c="#e8edf2" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  3. Connect the device to the same network as the server
                </Text>
              </Stack>
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
                    <Table.Th w={50}></Table.Th>
                    <Table.Th>MODEL</Table.Th>
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
        onClose={() => { setAircraftModal(false); setEditingAircraftImage(null); }}
        title={editingAircraftId ? 'Edit Aircraft' : 'New Aircraft'}
        styles={{ header: { background: '#0e1117' }, content: { background: '#0e1117' }, title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif" } }}
      >
        <form onSubmit={aircraftForm.onSubmit(handleSaveAircraft)}>
          <Stack gap="sm">
            <TextInput label="Model Name" required {...aircraftForm.getInputProps('model_name')} styles={inputStyles} />
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
      {/* Purge Flights Confirmation Modal */}
      <Modal
        opened={purgeConfirmOpen}
        onClose={() => setPurgeConfirmOpen(false)}
        title={<Text fw={700} c="#ff4444" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>CONFIRM FLIGHT DATA PURGE</Text>}
        styles={{ header: { background: '#0e1117', borderBottom: '1px solid rgba(255,68,68,0.3)' }, body: { background: '#0e1117' }, content: { background: '#0e1117' } }}
        size="sm"
      >
        <Stack gap="md">
          <Text c="#e8edf2" size="sm">
            This will permanently delete <strong>all flights, batteries, and battery logs</strong> from the local database for a clean re-sync.
          </Text>
          <Text c="#ff6b1a" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            This action cannot be undone. You can re-import from OpenDroneLog after purging.
          </Text>
          <Checkbox
            label="I understand this will delete all flight and battery data"
            checked={purgeChecked}
            onChange={(e) => setPurgeChecked(e.currentTarget.checked)}
            styles={{
              input: { borderColor: '#ff4444', '&:checked': { backgroundColor: '#ff4444', borderColor: '#ff4444' } },
              label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px' },
            }}
          />
          <Group>
            <Button variant="default" onClick={() => setPurgeConfirmOpen(false)}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              CANCEL
            </Button>
            <Button
              color="red"
              disabled={!purgeChecked}
              loading={purging}
              onClick={handlePurgeFlights}
              leftSection={<IconTrash size={14} />}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
            >
              PURGE ALL FLIGHTS
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
