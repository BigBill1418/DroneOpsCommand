import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Image,
  Loader,
  Select,
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
import { IconCheck, IconX, IconPlus, IconEdit, IconTrash, IconCurrencyDollar, IconMail, IconSend, IconBrandPaypal, IconCash, IconDrone, IconPlugConnected, IconMapPin, IconSearch, IconSignature, IconUpload, IconSettings, IconReceipt, IconPlane, IconPalette, IconWorldWww, IconKey, IconUser, IconLock, IconDatabaseExport, IconDatabaseImport, IconShieldCheck, IconDownload, IconAlertTriangle, IconPhoto, IconRadar2, IconUsers, IconTool, IconClock, IconCalendar, IconRefresh, IconPlayerPlay, IconRobot } from '@tabler/icons-react';
import api from '../api/client';
import { Aircraft, RateTemplate } from '../api/types';
import { inputStyles, cardStyle } from '../components/shared/styles';
import { invalidateBrandingCache } from '../hooks/useBranding';
import PasswordStrengthMeter, { isPasswordValid } from '../components/PasswordStrengthMeter';

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
  const [reprocessStatus, setReprocessStatus] = useState<{ reprocessable: number; total_dji: number; stored_on_disk: number; need_manual_upload: number } | null>(null);
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessingAll, setReprocessingAll] = useState(false);
  const [reprocessResult, setReprocessResult] = useState<{ updated: number; imported?: number; skipped_no_file?: number; errors: string[] } | null>(null);
  const [djiSaving, setDjiSaving] = useState(false);
  const [djiTesting, setDjiTesting] = useState(false);
  const [djiStatus, setDjiStatus] = useState<{
    status: string; message?: string; parser_online?: boolean;
    dji_api_reachable?: boolean; key_source?: string;
  } | null>(null);
  const [llmSaving, setLlmSaving] = useState(false);

  const [openskySaving, setOpenskySaving] = useState(false);
  const [openskyTesting, setOpenskyTesting] = useState(false);
  const [openskyStatus, setOpenskyStatus] = useState<{ status: string; message?: string } | null>(null);
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

  // Pilots
  const [pilots, setPilots] = useState<any[]>([]);
  const [pilotModal, setPilotModal] = useState(false);
  const [editingPilotId, setEditingPilotId] = useState<string | null>(null);
  const [pilotSaving, setPilotSaving] = useState(false);

  // Maintenance
  const [maintenanceStatus, setMaintenanceStatus] = useState<any[]>([]);
  const [maintenanceLoading, setMaintenanceLoading] = useState(false);
  const [seedingDefaults, setSeedingDefaults] = useState<string | null>(null);

  // Scheduled Backup
  const [backupSchedule, setBackupSchedule] = useState<{ enabled: boolean; retention_days: number; backup_time: string }>({ enabled: false, retention_days: 30, backup_time: '02:00' });
  const [backupHistory, setBackupHistory] = useState<any[]>([]);
  const [backupScheduleSaving, setBackupScheduleSaving] = useState(false);
  const [backupRunning, setBackupRunning] = useState(false);

  const aircraftForm = useForm({
    initialValues: { model_name: '', manufacturer: 'DJI', serial_number: '', specs_json: '{}' },
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

  const openskyForm = useForm({
    initialValues: { opensky_client_id: '', opensky_client_secret: '' },
  });

  const llmForm = useForm({
    initialValues: { llm_provider: 'ollama', anthropic_api_key: '' },
  });

  const accountForm = useForm({
    initialValues: { current_password: '', new_username: '', new_password: '', confirm_password: '' },
    validate: {
      current_password: (v) => (v.length === 0 ? 'Current password is required' : null),
      new_password: (v) => (v && v.length > 0 && !isPasswordValid(v) ? 'Password does not meet complexity requirements' : null),
      confirm_password: (v, values) => (v !== values.new_password ? 'Passwords do not match' : null),
    },
  });

  const weatherForm = useForm({
    initialValues: { weather_lat: '', weather_lon: '', weather_label: '', weather_airport_icao: '' },
  });

  const pilotForm = useForm({
    initialValues: { name: '', email: '', phone: '', faa_certificate_number: '', faa_certificate_expiry: '', notes: '' },
  });

  const brandingForm = useForm({
    initialValues: {
      company_name: '',
      company_tagline: '',
      company_website: '',
      company_social_url: '',
      company_contact_email: '',
      brand_primary_color: '#050608',
      brand_accent_color: '#00d4ff',
    },
  });
  const [companyLogo, setCompanyLogo] = useState<string>('');
  const [logoUploading, setLogoUploading] = useState(false);

  useEffect(() => {
    api.get('/llm/status').then((r) => setLlmStatus(r.data)).catch(() => setLlmStatus({ status: 'offline' })).finally(() => setLlmLoading(false));
    api.get('/aircraft').then((r) => setAircraft(Array.isArray(r.data) ? r.data : [])).catch(() => setAircraft([]));
    api.get('/rate-templates').then((r) => setRateTemplates(Array.isArray(r.data) ? r.data : [])).catch(() => setRateTemplates([]));
    api.get('/settings/smtp').then((r) => smtpForm.setValues(r.data)).catch(() => {});
    api.get('/settings/payment').then((r) => paymentForm.setValues(r.data)).catch(() => {});
    api.get('/settings/opendronelog').then((r) => odlForm.setValues(r.data)).catch(() => {});
    api.get('/settings/dji').then((r) => djiForm.setValues(r.data)).catch(() => {});
    api.get('/settings/opensky').then((r) => openskyForm.setValues(r.data)).catch(() => {});
    api.get('/settings/llm').then((r) => llmForm.setValues(r.data)).catch(() => {});
    api.get('/flight-library/reprocess/status').then((r) => setReprocessStatus(r.data)).catch(() => {});
    api.get('/auth/account').then((r) => { setCurrentUsername(r.data.username); accountForm.setFieldValue('new_username', r.data.username); }).catch(() => {});
    api.get('/settings/weather').then((r) => weatherForm.setValues(r.data)).catch(() => {});
    api.get('/intake/default-tos-status').then((r) => setTosUploaded(r.data.uploaded)).catch(() => {});
    api.get('/settings/branding').then((r) => { brandingForm.setValues(r.data); setCompanyLogo(r.data.company_logo || ''); }).catch(() => {});
    api.get('/settings/device-keys').then((r) => setDeviceKeys(Array.isArray(r.data) ? r.data : [])).catch(() => {});
    api.get('/pilots').then((r) => setPilots(Array.isArray(r.data) ? r.data : [])).catch(() => {});
    api.get('/backup/schedule').then((r) => setBackupSchedule(r.data)).catch(() => {});
    api.get('/backup/history').then((r) => setBackupHistory(Array.isArray(r.data) ? r.data : [])).catch(() => {});
    setMaintenanceLoading(true);
    api.get('/maintenance/status').then((r) => setMaintenanceStatus(Array.isArray(r.data) ? r.data : [])).catch(() => {}).finally(() => setMaintenanceLoading(false));
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
      api.get('/aircraft').then((r) => setAircraft(Array.isArray(r.data) ? r.data : []));
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
      api.get('/rate-templates').then((r) => setRateTemplates(Array.isArray(r.data) ? r.data : []));
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

  const handleSaveLlm = async (values: typeof llmForm.values) => {
    setLlmSaving(true);
    try {
      await api.put('/settings/llm', values);
      notifications.show({ title: 'Saved', message: 'LLM settings updated', color: 'cyan' });
      const r = await api.get('/settings/llm');
      llmForm.setValues(r.data);
      // Refresh LLM status to reflect new provider
      setLlmLoading(true);
      api.get('/llm/status').then((s) => setLlmStatus(s.data)).catch(() => setLlmStatus({ status: 'offline' })).finally(() => setLlmLoading(false));
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save LLM settings', color: 'red' });
    } finally {
      setLlmSaving(false);
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

  const handleSaveOpenSky = async (values: typeof openskyForm.values) => {
    setOpenskySaving(true);
    try {
      await api.put('/settings/opensky', values);
      notifications.show({ title: 'Saved', message: 'OpenSky credentials updated', color: 'cyan' });
      const r = await api.get('/settings/opensky');
      openskyForm.setValues(r.data);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save OpenSky credentials', color: 'red' });
    } finally {
      setOpenskySaving(false);
    }
  };

  const handleTestOpenSky = async () => {
    setOpenskyTesting(true);
    try {
      const r = await api.post('/settings/opensky/test');
      setOpenskyStatus(r.data);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setOpenskyStatus({ status: 'error', message: axiosErr.response?.data?.detail || 'Test failed' });
    } finally {
      setOpenskyTesting(false);
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

  const handleReprocessAll = async () => {
    setReprocessingAll(true);
    setReprocessResult(null);
    try {
      const r = await api.post('/flight-library/reprocess/all', {}, { timeout: 600000 });
      setReprocessResult(r.data);
      const msg = r.data.skipped_no_file > 0
        ? `${r.data.updated} updated, ${r.data.skipped_no_file} skipped (no stored file), ${r.data.errors.length} errors`
        : `${r.data.updated} updated, ${r.data.errors.length} errors`;
      notifications.show({
        title: 'Re-process Complete',
        message: msg,
        color: r.data.errors.length > 0 ? 'yellow' : 'green',
      });
      api.get('/flight-library/reprocess/status').then((r2) => setReprocessStatus(r2.data)).catch(() => {});
    } catch {
      notifications.show({ title: 'Error', message: 'Re-processing failed', color: 'red' });
    } finally {
      setReprocessingAll(false);
    }
  };

  const handleReprocessUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setReprocessing(true);
    setReprocessResult(null);
    try {
      const formData = new FormData();
      for (let i = 0; i < fileList.length; i++) {
        formData.append('files', fileList[i]);
      }
      const r = await api.post('/flight-library/reprocess', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 300000,
      });
      setReprocessResult(r.data);
      const msg = `${r.data.updated} updated, ${r.data.imported} new, ${r.data.errors.length} errors`;
      notifications.show({
        title: 'Re-process Complete',
        message: msg,
        color: r.data.errors.length > 0 ? 'yellow' : 'green',
      });
      api.get('/flight-library/reprocess/status').then((r2) => setReprocessStatus(r2.data)).catch(() => {});
    } catch {
      notifications.show({ title: 'Error', message: 'Re-processing failed', color: 'red' });
    } finally {
      setReprocessing(false);
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
      notifications.show({ title: 'Saved', message: `Home location set to ${values.weather_label || 'configured coordinates'}`, color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save home location', color: 'red' });
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

  // ── Pilot handlers ──────────────────────────────────
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
      api.get('/pilots').then((r) => setPilots(Array.isArray(r.data) ? r.data : []));
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
      api.get('/pilots').then((r) => setPilots(Array.isArray(r.data) ? r.data : []));
      notifications.show({ title: 'Deactivated', message: 'Pilot deactivated', color: 'yellow' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to deactivate pilot', color: 'red' });
    }
  };

  // ── Maintenance handlers ────────────────────────────
  const handleSeedDefaults = async (aircraftId: string) => {
    setSeedingDefaults(aircraftId);
    try {
      const resp = await api.post('/maintenance/seed-defaults', { aircraft_id: aircraftId });
      notifications.show({ title: 'Seeded', message: resp.data.message, color: 'cyan' });
      api.get('/maintenance/status').then((r) => setMaintenanceStatus(Array.isArray(r.data) ? r.data : []));
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to seed defaults', color: 'red' });
    } finally {
      setSeedingDefaults(null);
    }
  };

  // ── Backup schedule handlers ────────────────────────
  const handleSaveBackupSchedule = async () => {
    setBackupScheduleSaving(true);
    try {
      await api.put('/backup/schedule', backupSchedule);
      notifications.show({ title: 'Saved', message: 'Backup schedule updated', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save backup schedule', color: 'red' });
    } finally {
      setBackupScheduleSaving(false);
    }
  };

  const handleRunBackupNow = async () => {
    setBackupRunning(true);
    try {
      const resp = await api.post('/backup/run-now');
      notifications.show({ title: 'Backup Complete', message: `${resp.data.filename} created (${resp.data.toc_entries} objects)`, color: 'green' });
      api.get('/backup/history').then((r) => setBackupHistory(Array.isArray(r.data) ? r.data : []));
    } catch (err: any) {
      notifications.show({ title: 'Backup Failed', message: err.response?.data?.detail || 'Failed to run backup', color: 'red' });
    } finally {
      setBackupRunning(false);
    }
  };

  const handleDeleteBackup = async (filename: string) => {
    try {
      await api.delete(`/backup/history/${filename}`);
      setBackupHistory((prev) => prev.filter((b: any) => b.filename !== filename));
      notifications.show({ title: 'Deleted', message: `${filename} removed`, color: 'yellow' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete backup', color: 'red' });
    }
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
          <Tabs.Tab value="ai" leftSection={<IconRobot size={14} />}>
            AI / REPORTS
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
          <Tabs.Tab value="pilots" leftSection={<IconUsers size={14} />}>
            PILOTS
          </Tabs.Tab>
          <Tabs.Tab value="maintenance" leftSection={<IconTool size={14} />}>
            MAINTENANCE
          </Tabs.Tab>
          <Tabs.Tab value="backups" leftSection={<IconDatabaseExport size={14} />}>
            BACKUPS
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
              {/* Logo Upload */}
              <Text size="sm" fw={600} c="#e8edf2" mb={4}>Company Logo</Text>
              <Text size="xs" c="#5a6478" mb="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Used in PDF reports and email headers. Recommended: PNG with transparent background, max 400px wide.
              </Text>
              <Group gap="md" mb="md">
                {companyLogo ? (
                  <Group gap="sm" align="center">
                    <Image src={`/uploads/${companyLogo}`} h={60} fit="contain" radius="sm" style={{ background: '#1a1f2e', padding: 8, borderRadius: 6 }} />
                    <ActionIcon
                      variant="light"
                      color="red"
                      size="sm"
                      onClick={async () => {
                        try {
                          await api.delete('/settings/branding/logo');
                          setCompanyLogo('');
                          notifications.show({ title: 'Deleted', message: 'Logo removed', color: 'cyan' });
                        } catch {
                          notifications.show({ title: 'Error', message: 'Failed to delete logo', color: 'red' });
                        }
                      }}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Group>
                ) : (
                  <Text size="xs" c="#5a6478" fs="italic">No logo uploaded</Text>
                )}
                <Button
                  size="xs"
                  variant="light"
                  color="cyan"
                  leftSection={<IconPhoto size={14} />}
                  loading={logoUploading}
                  onClick={() => {
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.accept = 'image/png,image/jpeg,image/webp,image/svg+xml';
                    input.onchange = async (e) => {
                      const file = (e.target as HTMLInputElement).files?.[0];
                      if (!file) return;
                      setLogoUploading(true);
                      try {
                        const formData = new FormData();
                        formData.append('file', file);
                        const resp = await api.post('/settings/branding/logo', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
                        setCompanyLogo(resp.data.company_logo);
                        invalidateBrandingCache();
                        notifications.show({ title: 'Uploaded', message: 'Company logo saved', color: 'cyan' });
                      } catch {
                        notifications.show({ title: 'Error', message: 'Failed to upload logo', color: 'red' });
                      } finally {
                        setLogoUploading(false);
                      }
                    };
                    input.click();
                  }}
                >
                  {companyLogo ? 'Replace Logo' : 'Upload Logo'}
                </Button>
              </Group>

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

                  {/* Brand Colors */}
                  <Text size="sm" fw={600} c="#e8edf2" mt="xs">PDF Report Colors</Text>
                  <Group gap="md">
                    <div>
                      <Text size="xs" c="#5a6478" mb={4}>Header Background</Text>
                      <Group gap="xs">
                        <input
                          type="color"
                          value={brandingForm.values.brand_primary_color || '#050608'}
                          onChange={(e) => brandingForm.setFieldValue('brand_primary_color', e.target.value)}
                          style={{ width: 36, height: 36, border: '1px solid #1a1f2e', borderRadius: 4, cursor: 'pointer', background: 'transparent', padding: 2 }}
                        />
                        <TextInput
                          size="xs"
                          w={90}
                          {...brandingForm.getInputProps('brand_primary_color')}
                          styles={inputStyles}
                        />
                      </Group>
                    </div>
                    <div>
                      <Text size="xs" c="#5a6478" mb={4}>Accent Color</Text>
                      <Group gap="xs">
                        <input
                          type="color"
                          value={brandingForm.values.brand_accent_color || '#00d4ff'}
                          onChange={(e) => brandingForm.setFieldValue('brand_accent_color', e.target.value)}
                          style={{ width: 36, height: 36, border: '1px solid #1a1f2e', borderRadius: 4, cursor: 'pointer', background: 'transparent', padding: 2 }}
                        />
                        <TextInput
                          size="xs"
                          w={90}
                          {...brandingForm.getInputProps('brand_accent_color')}
                          styles={inputStyles}
                        />
                      </Group>
                    </div>
                  </Group>

                  <Button type="submit" color="cyan" loading={brandingSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE BRANDING
                  </Button>
                </Stack>
              </form>
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* ═══ GENERAL TAB ═══ */}
        {/* ═══ AI / REPORT GENERATION TAB ═══ */}
        <Tabs.Panel value="ai" pt="md">
          <Stack gap="md">
            {/* LLM Provider Settings */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconRobot size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>AI / REPORT GENERATION</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Choose the LLM provider used for generating after-action reports. Claude API is faster and higher quality; Ollama runs locally on your hardware.
              </Text>
              <form onSubmit={llmForm.onSubmit(handleSaveLlm)}>
                <Stack gap="sm">
                  <Select
                    label="LLM Provider"
                    data={[
                      { value: 'claude', label: 'Claude API (Anthropic)' },
                      { value: 'ollama', label: 'Ollama (Local)' },
                    ]}
                    {...llmForm.getInputProps('llm_provider')}
                    styles={inputStyles}
                  />
                  {llmForm.values.llm_provider === 'claude' && (
                    <PasswordInput
                      label="Anthropic API Key"
                      placeholder="sk-ant-..."
                      {...llmForm.getInputProps('anthropic_api_key')}
                      styles={inputStyles}
                    />
                  )}
                  <Button type="submit" color="cyan" loading={llmSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE LLM SETTINGS
                  </Button>
                </Stack>
              </form>
            </Card>

            {/* LLM Status */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>LLM STATUS</Title>
              {llmLoading ? (
                <Loader color="cyan" size="sm" />
              ) : (
                <Stack gap="sm">
                  <Group>
                    <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>PROVIDER:</Text>
                    <Badge color="cyan" variant="light">
                      {(llmStatus as any)?.provider === 'claude' ? 'Claude API' : 'Ollama'}
                    </Badge>
                  </Group>
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
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="general" pt="md">
          <Stack gap="md">
            {/* Weather / Airspace Location */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconMapPin size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>HOME LOCATION</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Your home base for dashboard weather, METAR, TFR, NOTAM monitoring, and the default center for airspace tracking. The airspace page will also use GPS on mobile when available.
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
                    SAVE HOME LOCATION
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
                Required for decrypting DJI flight logs (v13+ encryption). Register at developer.dji.com to obtain a key.
                Without a key, basic flight summary data (duration, distance, altitude) is still extracted from log headers.
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
                      color={djiStatus?.status === 'online' ? 'green' : djiStatus?.status === 'error' ? 'red' : djiStatus?.status === 'warning' ? 'yellow' : 'gray'}
                      loading={djiTesting}
                      onClick={handleTestDji}
                      leftSection={djiStatus?.status === 'online' ? <IconCheck size={14} /> : djiStatus?.status === 'error' ? <IconX size={14} /> : <IconPlugConnected size={14} />}
                      styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                    >
                      VALIDATE KEY
                    </Button>
                  </Group>
                  {djiStatus && (
                    <Stack gap={6}>
                      <Badge
                        color={djiStatus.status === 'online' ? 'green' : djiStatus.status === 'warning' ? 'yellow' : 'red'}
                        variant="light"
                        size="lg"
                        leftSection={djiStatus.status === 'online' ? <IconCheck size={12} /> : djiStatus.status === 'warning' ? <IconPlugConnected size={12} /> : <IconX size={12} />}
                      >
                        {djiStatus.message || djiStatus.status}
                      </Badge>
                      <Group gap="xs">
                        <Badge
                          color={djiStatus.parser_online ? 'green' : 'red'}
                          variant="dot"
                          size="sm"
                        >
                          Parser {djiStatus.parser_online ? 'Online' : 'Offline'}
                        </Badge>
                        {djiStatus.dji_api_reachable !== undefined && (
                          <Badge
                            color={djiStatus.dji_api_reachable ? 'green' : 'yellow'}
                            variant="dot"
                            size="sm"
                          >
                            DJI API {djiStatus.dji_api_reachable ? 'Reachable' : 'Unreachable'}
                          </Badge>
                        )}
                        {djiStatus.key_source && (
                          <Badge variant="dot" color="gray" size="sm">
                            Key: {djiStatus.key_source === 'settings_db' ? 'Settings' : djiStatus.key_source === 'environment' ? '.env' : djiStatus.key_source}
                          </Badge>
                        )}
                      </Group>
                    </Stack>
                  )}
                </Stack>
              </form>
            </Card>

            {/* OpenSky Network */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconRadar2 size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>OPENSKY NETWORK</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Real-time air traffic data for Airspace Awareness. Free account at opensky-network.org.
                Works without credentials (anonymous) but authenticated gets better rate limits.
              </Text>
              <form onSubmit={openskyForm.onSubmit(handleSaveOpenSky)}>
                <Stack gap="sm">
                  <TextInput
                    label="CLIENT ID"
                    placeholder="your-client-id"
                    {...openskyForm.getInputProps('opensky_client_id')}
                    styles={inputStyles}
                  />
                  <TextInput
                    label="CLIENT SECRET"
                    placeholder="your-client-secret"
                    {...openskyForm.getInputProps('opensky_client_secret')}
                    styles={inputStyles}
                  />
                  <Group gap="sm">
                    <Button type="submit" color="cyan" loading={openskySaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                      SAVE
                    </Button>
                    <Button
                      variant="light"
                      color="cyan"
                      loading={openskyTesting}
                      onClick={handleTestOpenSky}
                      styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                    >
                      TEST CONNECTION
                    </Button>
                  </Group>
                  {openskyStatus && (
                    <Badge
                      color={openskyStatus.status === 'ok' ? 'green' : 'red'}
                      variant="light"
                      size="lg"
                      style={{ fontFamily: "'Share Tech Mono', monospace" }}
                    >
                      {openskyStatus.message || openskyStatus.status}
                    </Badge>
                  )}
                </Stack>
              </form>
            </Card>

            {/* Re-process DJI Flights */}
            <Card padding="lg" radius="md" style={{ ...cardStyle, border: '1px solid rgba(0, 212, 255, 0.15)' }}>
              <Group gap="sm" mb="md">
                <IconDatabaseImport size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>RE-PROCESS FLIGHT LOGS</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Re-parse flight logs with the current DJI API key to get full decrypted data
                (GPS tracks, telemetry, battery curves). Original files are now saved on upload —
                flights uploaded going forward can be re-processed automatically.
              </Text>
              {reprocessStatus && (
                <Stack gap="xs" mb="sm">
                  <Badge
                    color={reprocessStatus.reprocessable > 0 ? 'yellow' : 'green'}
                    variant="light"
                    size="lg"
                    style={{ fontFamily: "'Share Tech Mono', monospace" }}
                  >
                    {reprocessStatus.reprocessable > 0
                      ? `${reprocessStatus.reprocessable} of ${reprocessStatus.total_dji} DJI flights missing GPS data`
                      : `All ${reprocessStatus.total_dji} DJI flights have full data`}
                  </Badge>
                  {reprocessStatus.reprocessable > 0 && (
                    <Group gap="xs">
                      {reprocessStatus.stored_on_disk > 0 && (
                        <Badge color="cyan" variant="dot" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                          {reprocessStatus.stored_on_disk} have stored files (auto re-process)
                        </Badge>
                      )}
                      {reprocessStatus.need_manual_upload > 0 && (
                        <Badge color="orange" variant="dot" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                          {reprocessStatus.need_manual_upload} need manual re-upload
                        </Badge>
                      )}
                    </Group>
                  )}
                </Stack>
              )}
              <Group gap="sm">
                {/* Primary action: re-process all from stored files */}
                {reprocessStatus && reprocessStatus.stored_on_disk > 0 && (
                  <Button
                    color="cyan"
                    loading={reprocessingAll}
                    leftSection={<IconDatabaseImport size={14} />}
                    onClick={handleReprocessAll}
                    styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                  >
                    {reprocessingAll ? 'RE-PROCESSING...' : `RE-PROCESS ${reprocessStatus.stored_on_disk} FLIGHTS`}
                  </Button>
                )}
                {/* Fallback: manual re-upload for flights without stored files */}
                <Button
                  component="label"
                  color="gray"
                  variant="light"
                  loading={reprocessing}
                  leftSection={<IconDatabaseImport size={14} />}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}
                >
                  {reprocessing ? 'PROCESSING...' : 'MANUAL RE-UPLOAD'}
                  <input
                    type="file"
                    multiple
                    accept=".txt,.csv"
                    style={{ display: 'none' }}
                    onChange={(e) => handleReprocessUpload(e.target.files)}
                  />
                </Button>
              </Group>
              {reprocessResult && (
                <Stack gap={4} mt="sm">
                  <Group gap="xs">
                    {reprocessResult.updated > 0 && (
                      <Badge color="green" variant="light" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {reprocessResult.updated} updated
                      </Badge>
                    )}
                    {(reprocessResult.imported ?? 0) > 0 && (
                      <Badge color="cyan" variant="light" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {reprocessResult.imported} new
                      </Badge>
                    )}
                    {(reprocessResult.skipped_no_file ?? 0) > 0 && (
                      <Badge color="orange" variant="light" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {reprocessResult.skipped_no_file} skipped (no stored file)
                      </Badge>
                    )}
                    {reprocessResult.errors.length > 0 && (
                      <Badge color="red" variant="light" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        {reprocessResult.errors.length} errors
                      </Badge>
                    )}
                  </Group>
                  {reprocessResult.errors.length > 0 && (
                    <Text c="#ff6b6b" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {reprocessResult.errors.slice(0, 3).join('; ')}
                    </Text>
                  )}
                </Stack>
              )}
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
          </Stack>
        </Tabs.Panel>

        {/* ═══ ACCOUNT TAB ═══ */}
        {/* ═══ PILOTS TAB ═══ */}
        <Tabs.Panel value="pilots" pt="md">
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
          </Stack>
        </Tabs.Panel>

        {/* ═══ MAINTENANCE TAB ═══ */}
        <Tabs.Panel value="maintenance" pt="md">
          <Stack gap="md">
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconTool size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>MAINTENANCE STATUS</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Industry-standard DJI maintenance intervals. Seed defaults per aircraft, then track service records.
              </Text>

              {maintenanceLoading ? (
                <Group justify="center" py="xl"><Loader color="cyan" size="sm" /></Group>
              ) : maintenanceStatus.length === 0 ? (
                <Text c="#5a6478" size="sm" ta="center" py="md">No aircraft in fleet — add aircraft in Fleet & Rates tab first</Text>
              ) : (
                <Stack gap="lg">
                  {maintenanceStatus.map((ac: any) => (
                    <Card key={ac.aircraft_id} padding="md" radius="sm" style={{ background: '#0a0d12', border: '1px solid #1a1f2e' }}>
                      <Group justify="space-between" mb="sm">
                        <Group gap="sm">
                          <IconPlane size={16} color="#00d4ff" />
                          <Text fw={600} c="#e8edf2" size="sm">{ac.aircraft_name || 'Unknown Aircraft'}</Text>
                          <Badge size="xs" variant="light" color={ac.overall_status === 'overdue' ? 'red' : ac.overall_status === 'due_soon' ? 'yellow' : 'green'}>
                            {(ac.overall_status || 'ok').toUpperCase().replace('_', ' ')}
                          </Badge>
                        </Group>
                        <Group gap="xs">
                          <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                            {(ac.total_flight_hours ?? 0).toFixed(1)}h total
                          </Text>
                          {(ac.schedules?.length ?? 0) === 0 && (
                            <Button
                              size="xs"
                              variant="light"
                              color="cyan"
                              leftSection={<IconPlus size={12} />}
                              loading={seedingDefaults === ac.aircraft_id}
                              onClick={() => handleSeedDefaults(ac.aircraft_id)}
                            >
                              Seed Defaults
                            </Button>
                          )}
                        </Group>
                      </Group>

                      {(ac.schedules?.length ?? 0) > 0 && (
                        <Table styles={{
                          table: { color: '#e8edf2' },
                          th: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', borderBottom: '1px solid #1a1f2e', padding: '4px 8px' },
                          td: { borderBottom: '1px solid #0e1117', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px', padding: '4px 8px' },
                        }}>
                          <Table.Thead>
                            <Table.Tr>
                              <Table.Th>ITEM</Table.Th>
                              <Table.Th>INTERVAL</Table.Th>
                              <Table.Th>HOURS SINCE</Table.Th>
                              <Table.Th>REMAINING</Table.Th>
                              <Table.Th>STATUS</Table.Th>
                            </Table.Tr>
                          </Table.Thead>
                          <Table.Tbody>
                            {(ac.schedules || []).map((s: any) => (
                              <Table.Tr key={s.schedule_id}>
                                <Table.Td>{s.maintenance_type}</Table.Td>
                                <Table.Td>
                                  {s.interval_hours ? `${s.interval_hours}h` : ''}
                                  {s.interval_hours && s.interval_days ? ' / ' : ''}
                                  {s.interval_days ? `${s.interval_days}d` : ''}
                                </Table.Td>
                                <Table.Td>{s.hours_since_maintenance != null ? `${s.hours_since_maintenance.toFixed(1)}h` : '—'}</Table.Td>
                                <Table.Td>
                                  {s.hours_remaining != null ? (
                                    <Text c={s.hours_remaining < 0 ? '#ff4444' : s.hours_remaining < 20 ? '#ff6b1a' : '#4ade80'} size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                                      {s.hours_remaining.toFixed(1)}h
                                    </Text>
                                  ) : s.days_remaining != null ? (
                                    <Text c={s.days_remaining < 0 ? '#ff4444' : s.days_remaining < 7 ? '#ff6b1a' : '#4ade80'} size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                                      {s.days_remaining}d
                                    </Text>
                                  ) : '—'}
                                </Table.Td>
                                <Table.Td>
                                  <Badge size="xs" variant="light" color={s.status === 'overdue' ? 'red' : s.status === 'due_soon' ? 'yellow' : 'green'}>
                                    {(s.status || 'ok').toUpperCase().replace('_', ' ')}
                                  </Badge>
                                </Table.Td>
                              </Table.Tr>
                            ))}
                          </Table.Tbody>
                        </Table>
                      )}
                    </Card>
                  ))}
                </Stack>
              )}
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* ═══ BACKUPS TAB ═══ */}
        <Tabs.Panel value="backups" pt="md">
          <Stack gap="md">
            {/* Scheduled Backup Config */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconClock size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>SCHEDULED BACKUPS</Title>
              </Group>
              <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Automatic nightly database backups saved to /data/backups/ with configurable retention.
              </Text>
              <Stack gap="sm">
                <Switch
                  label="Enable Scheduled Backups"
                  checked={backupSchedule.enabled}
                  onChange={(e) => setBackupSchedule((prev) => ({ ...prev, enabled: e.currentTarget.checked }))}
                  styles={{
                    label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace" },
                    track: { borderColor: '#1a1f2e' },
                  }}
                />
                <Group gap="md">
                  <TextInput
                    label="Backup Time (HH:MM)"
                    value={backupSchedule.backup_time}
                    onChange={(e) => setBackupSchedule((prev) => ({ ...prev, backup_time: e.currentTarget.value }))}
                    styles={inputStyles}
                    w={160}
                    placeholder="02:00"
                  />
                  <NumberInput
                    label="Retention (days)"
                    value={backupSchedule.retention_days}
                    onChange={(v) => setBackupSchedule((prev) => ({ ...prev, retention_days: Number(v) || 30 }))}
                    styles={inputStyles}
                    w={160}
                    min={1}
                    max={365}
                  />
                </Group>
                <Group gap="sm">
                  <Button color="cyan" loading={backupScheduleSaving} onClick={handleSaveBackupSchedule} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    SAVE SCHEDULE
                  </Button>
                  <Button variant="light" color="green" loading={backupRunning} onClick={handleRunBackupNow} leftSection={<IconPlayerPlay size={14} />} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    RUN BACKUP NOW
                  </Button>
                </Group>
              </Stack>
            </Card>

            {/* Manual Backup & Restore */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group gap="sm" mb="md">
                <IconDatabaseExport size={20} color="#00d4ff" />
                <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>MANUAL BACKUP & RESTORE</Title>
              </Group>
              <Stack gap="sm">
                <Group gap="sm">
                  <Button color="cyan" loading={backupCreating} onClick={handleBackupAndDownload} leftSection={<IconDownload size={14} />} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    CREATE & DOWNLOAD BACKUP
                  </Button>
                  <Button variant="light" color="yellow" onClick={handleRestoreFileSelect} leftSection={<IconDatabaseImport size={14} />} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                    UPLOAD & RESTORE
                  </Button>
                </Group>

                {backupResult && (
                  <Card padding="sm" radius="sm" style={{ background: '#0a1a0a', border: '1px solid #1a3a1a' }}>
                    <Text c="#4ade80" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      Backup: {backupResult.filename} — {backupResult.objects} objects — SHA-256: {backupResult.sha256.slice(0, 16)}...
                    </Text>
                  </Card>
                )}

                {restoreValidation && (
                  <Card padding="sm" radius="sm" style={{ background: '#1a1a0a', border: '1px solid #3a3a1a' }}>
                    <Stack gap="xs">
                      <Text c="#ffd700" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                        Validated: {restoreValidation.filename} — {restoreValidation.toc_entries} objects — {(restoreValidation.size_bytes / 1024 / 1024).toFixed(1)}MB
                      </Text>
                      <Checkbox
                        label="I understand this will replace ALL database contents"
                        checked={restoreChecked}
                        onChange={(e) => setRestoreChecked(e.currentTarget.checked)}
                        styles={{ input: { borderColor: '#ff4444' }, label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px' } }}
                      />
                      <Button color="red" disabled={!restoreChecked} loading={restoreRunning} onClick={handleConfirmRestore} leftSection={<IconAlertTriangle size={14} />} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
                        CONFIRM RESTORE
                      </Button>
                    </Stack>
                  </Card>
                )}

                {restoreResult && (
                  <Card padding="sm" radius="sm" style={{ background: '#0a1a0a', border: '1px solid #1a3a1a' }}>
                    <Text c="#4ade80" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      Restored: {restoreResult.table_count} tables — SHA-256: {restoreResult.sha256.slice(0, 16)}...
                    </Text>
                  </Card>
                )}
              </Stack>
            </Card>

            {/* Backup History */}
            <Card padding="lg" radius="md" style={cardStyle}>
              <Group justify="space-between" mb="md">
                <Group gap="sm">
                  <IconCalendar size={20} color="#00d4ff" />
                  <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>BACKUP HISTORY</Title>
                </Group>
                <Button variant="subtle" color="cyan" size="xs" onClick={() => api.get('/backup/history').then((r) => setBackupHistory(Array.isArray(r.data) ? r.data : []))} leftSection={<IconRefresh size={12} />}>
                  Refresh
                </Button>
              </Group>

              <Table styles={{
                table: { color: '#e8edf2' },
                th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', borderBottom: '1px solid #1a1f2e' },
                td: { borderBottom: '1px solid #1a1f2e', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px' },
              }}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>FILENAME</Table.Th>
                    <Table.Th>SIZE</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>ACTIONS</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {backupHistory.length === 0 && (
                    <Table.Tr><Table.Td colSpan={4}><Text c="#5a6478" size="sm" ta="center" py="md">No backups found</Text></Table.Td></Table.Tr>
                  )}
                  {backupHistory.map((b: any) => (
                    <Table.Tr key={b.filename}>
                      <Table.Td>{b.filename || '—'}</Table.Td>
                      <Table.Td>{((b.size_bytes ?? 0) / 1024 / 1024).toFixed(1)} MB</Table.Td>
                      <Table.Td>{b.modified_at ? new Date(b.modified_at).toLocaleString() : '—'}</Table.Td>
                      <Table.Td>
                        <ActionIcon variant="subtle" color="red" size="sm" onClick={() => handleDeleteBackup(b.filename)}>
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>
          </Stack>
        </Tabs.Panel>

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
                  <PasswordStrengthMeter password={accountForm.values.new_password} />
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
