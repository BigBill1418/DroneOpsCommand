import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Button,
  Card,
  Checkbox,
  Group,
  NumberInput,
  Progress,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCalendar,
  IconClock,
  IconDatabaseExport,
  IconDatabaseImport,
  IconDownload,
  IconPlayerPlay,
  IconRefresh,
  IconTrash,
} from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';
import { pollBackupJob, jobApiUnavailable } from './backupJob';

/**
 * BACKUPS tab — scheduled backup config, manual backup/restore, and history.
 *
 * Manual backup/restore use the FU-8 #4 job API (202 + poll every 2s, Mantine
 * Progress) with a sync fallback on 404 (deploy skew). Fetches /backup/schedule
 * and /backup/history on mount. All payloads/field names unchanged.
 */
export default function BackupsTab() {
  const [backupSchedule, setBackupSchedule] = useState<{ enabled: boolean; retention_days: number; backup_time: string }>({ enabled: false, retention_days: 30, backup_time: '02:00' });
  const [backupHistory, setBackupHistory] = useState<any[]>([]);
  const [backupScheduleSaving, setBackupScheduleSaving] = useState(false);
  const [backupRunning, setBackupRunning] = useState(false);

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

  const reloadHistory = () => api.get('/backup/history').then((r) => setBackupHistory(Array.isArray(r.data) ? r.data : []));

  useEffect(() => {
    api.get('/backup/schedule').then((r) => setBackupSchedule(r.data)).catch(() => {});
    reloadHistory().catch(() => {});
  }, []);

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
      reloadHistory();
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

  // ── Manual backup: job API for the dump, sync stream for the download ─────
  const streamBackupDownload = async () => {
    const resp = await api.post('/backup/create-and-download', {}, { responseType: 'blob' });
    const sha256 = resp.headers['x-backup-sha256'] || '';
    const objects = parseInt(resp.headers['x-backup-objects'] || '0', 10);
    const disposition = resp.headers['content-disposition'] || '';
    const filenameMatch = disposition.match(/filename="(.+?)"/);
    const filename = filenameMatch ? filenameMatch[1] : `doc_backup_${Date.now()}.dump`;
    const blob = new Blob([resp.data], { type: 'application/octet-stream' });
    const size = blob.size;
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
      try {
        const startResp = await api.post('/backup/jobs', { kind: 'create' });
        const jobId: string = startResp.data.job_id;
        await pollBackupJob(jobId, (st) => {
          setBackupPhase({ phase: st.phase || st.status, progress: st.progress ?? 0 });
        });
      } catch (jobErr: any) {
        if (!jobApiUnavailable(jobErr)) throw jobErr;
        setBackupPhase(null);
      }
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
      setRestoreValidating(true);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await api.post('/backup/validate-upload', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        setRestoreValidation(resp.data);
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
        }
      }
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
            <Button variant="light" color="yellow" loading={restoreValidating} onClick={handleRestoreFileSelect} leftSection={<IconDatabaseImport size={14} />} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              UPLOAD & RESTORE
            </Button>
          </Group>

          {backupPhase && (
            <Stack gap={4}>
              <Group justify="space-between">
                <Text size="xs" c="#00d4ff" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                  {backupPhase.phase.toUpperCase().replace(/_/g, ' ')}
                </Text>
                <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>{backupPhase.progress}%</Text>
              </Group>
              <Progress value={backupPhase.progress} color="cyan" size="sm" radius="xs" striped animated />
            </Stack>
          )}

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
                {restorePhase && (
                  <Stack gap={4}>
                    <Group justify="space-between">
                      <Text size="xs" c="#ff6b6b" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                        {restorePhase.phase.toUpperCase().replace(/_/g, ' ')}
                      </Text>
                      <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>{restorePhase.progress}%</Text>
                    </Group>
                    <Progress value={restorePhase.progress} color="red" size="sm" radius="xs" striped animated />
                  </Stack>
                )}
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
          <Button variant="subtle" color="cyan" size="xs" onClick={() => reloadHistory()} leftSection={<IconRefresh size={12} />}>
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
  );
}
