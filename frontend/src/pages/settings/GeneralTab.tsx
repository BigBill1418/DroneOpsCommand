import { useEffect, useState } from 'react';
import {
  Button,
  Card,
  Checkbox,
  Group,
  Progress,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconDatabaseExport,
  IconDatabaseImport,
  IconDownload,
  IconMapPin,
  IconSearch,
  IconShieldCheck,
} from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';
import { pollBackupJob, jobApiUnavailable } from './backupJob';

/**
 * GENERAL tab — home location (weather/airspace) + database backup & restore.
 *
 * Backup/restore was switched from the long-hanging synchronous endpoints to
 * the FU-8 #4 job API: start job (202) → poll GET /api/backup/jobs/{id} every
 * 2s → show phase/progress. The download still uses the sync
 * /backup/create-and-download stream (that is how the file reaches the
 * browser); the job API just moves the *heavy dump* off the request path and
 * gives progress. On a 404 from the job endpoints (older backend during a
 * deploy skew) we fall back to the original synchronous calls so backups never
 * break mid-deploy.
 */
export default function GeneralTab() {
  const [weatherSaving, setWeatherSaving] = useState(false);
  const [weatherLooking, setWeatherLooking] = useState(false);
  const [weatherQuery, setWeatherQuery] = useState('');

  const [backupCreating, setBackupCreating] = useState(false);
  const [backupResult, setBackupResult] = useState<{ filename: string; sha256: string; objects: number; size: number } | null>(null);
  const [backupPhase, setBackupPhase] = useState<{ phase: string; progress: number } | null>(null);

  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreTempPath, setRestoreTempPath] = useState<string | null>(null);
  const [restoreValidation, setRestoreValidation] = useState<{ valid: boolean; filename: string; sha256: string; size_bytes: number; toc_entries: number } | null>(null);
  const [restoreValidating, setRestoreValidating] = useState(false);
  const [restoreRunning, setRestoreRunning] = useState(false);
  const [restoreChecked, setRestoreChecked] = useState(false);
  const [restorePhase, setRestorePhase] = useState<{ phase: string; progress: number } | null>(null);
  const [restoreResult, setRestoreResult] = useState<{ restored: boolean; table_count: number; sha256: string } | null>(null);

  const weatherForm = useForm({
    initialValues: { weather_lat: '', weather_lon: '', weather_label: '', weather_airport_icao: '' },
  });

  useEffect(() => {
    api.get('/settings/weather').then((r) => weatherForm.setValues(r.data)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // ── Backup: create via job API (progress), then stream the file down ──────
  // The dump itself runs as a background job so the request no longer hangs for
  // the full dump. Once the job reports complete, we fetch the produced file
  // through the existing sync streaming endpoint (unchanged Save-As UX).
  const streamBackupDownload = async () => {
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

    return { filename, sha256, objects, size };
  };

  const handleBackupAndDownload = async () => {
    setBackupCreating(true);
    setBackupResult(null);
    setBackupPhase({ phase: 'queued', progress: 0 });
    try {
      // 1) Kick off the dump as a background job so the request path stays free.
      //    On a 404 (older backend) skip straight to the sync download.
      try {
        const startResp = await api.post('/backup/jobs', { kind: 'create' });
        const jobId: string = startResp.data.job_id;
        await pollBackupJob(jobId, (st) => {
          setBackupPhase({ phase: st.phase || st.status, progress: st.progress ?? 0 });
        });
      } catch (jobErr: any) {
        if (!jobApiUnavailable(jobErr)) throw jobErr;
        // job endpoint missing — fall through to sync-only path below
        setBackupPhase(null);
      }

      // 2) Stream the produced dump down to the browser (Save As). This sync
      //    endpoint always exists; with the job-API path the dump is already
      //    cached/fast, and it is the sole fallback on older backends.
      const result = await streamBackupDownload();
      setBackupResult(result);
      notifications.show({
        title: 'Backup Created & Verified',
        message: `${result.filename} — ${result.objects} objects, SHA-256 verified`,
        color: 'green',
        autoClose: 8000,
      });
    } catch (err: any) {
      notifications.show({ title: 'Backup Failed', message: err.response?.data?.detail || 'Failed to create backup', color: 'red' });
    } finally {
      setBackupCreating(false);
      setBackupPhase(null);
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
      setRestoreTempPath(null);

      // Immediately validate the uploaded file
      setRestoreValidating(true);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await api.post('/backup/validate-upload', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        setRestoreValidation(resp.data);
        // temp_path lets the restore run as a job against the already-uploaded
        // dump (no second upload). Older backends omit it — restore then falls
        // back to the synchronous restore-from-upload path.
        setRestoreTempPath(resp.data.temp_path ?? null);
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
    setRestorePhase({ phase: 'queued', progress: 0 });
    try {
      // Prefer the job API when we have a validated temp_path on this backend.
      if (restoreTempPath) {
        try {
          const startResp = await api.post('/backup/jobs', { kind: 'restore', temp_path: restoreTempPath });
          const jobId: string = startResp.data.job_id;
          const final = await pollBackupJob(jobId, (st) => {
            setRestorePhase({ phase: st.phase || st.status, progress: st.progress ?? 0 });
          });
          const result = (final.result || {}) as { table_count?: number; sha256?: string };
          const restored = { restored: true, table_count: result.table_count ?? 0, sha256: result.sha256 ?? (restoreValidation?.sha256 ?? '') };
          setRestoreResult(restored);
          notifications.show({
            title: 'Database Restored Successfully',
            message: `${restored.table_count} tables restored from ${restoreFile.name} — SHA-256: ${restored.sha256.slice(0, 12)}...`,
            color: 'green',
            autoClose: 10000,
          });
          return;
        } catch (jobErr: any) {
          if (!jobApiUnavailable(jobErr)) throw jobErr;
          setRestorePhase(null);
          // fall through to sync restore below
        }
      }

      // Synchronous fallback — re-uploads and restores in one request.
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
      notifications.show({ title: 'Restore Failed', message: err.response?.data?.detail || err.message || 'Restore failed', color: 'red' });
    } finally {
      setRestoreRunning(false);
      setRestorePhase(null);
    }
  };

  return (
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

        {backupPhase && (
          <Stack gap={4} mb="md">
            <Group justify="space-between">
              <Text size="xs" c="#00d4ff" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                {backupPhase.phase.toUpperCase().replace(/_/g, ' ')}
              </Text>
              <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                {backupPhase.progress}%
              </Text>
            </Group>
            <Progress value={backupPhase.progress} color="cyan" size="sm" radius="xs" striped animated />
          </Stack>
        )}

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
              {restorePhase && (
                <Stack gap={4} mb="sm">
                  <Group justify="space-between">
                    <Text size="xs" c="#ff6b6b" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                      {restorePhase.phase.toUpperCase().replace(/_/g, ' ')}
                    </Text>
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {restorePhase.progress}%
                    </Text>
                  </Group>
                  <Progress value={restorePhase.progress} color="red" size="sm" radius="xs" striped animated />
                </Stack>
              )}
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
                  onClick={() => { setRestoreFile(null); setRestoreValidation(null); setRestoreChecked(false); setRestoreResult(null); setRestoreTempPath(null); }}
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
  );
}
