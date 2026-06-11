import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Modal,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import {
  IconCheck,
  IconDatabaseImport,
  IconDrone,
  IconKey,
  IconPlugConnected,
  IconRadar2,
  IconTrash,
  IconX,
} from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';

/**
 * FLIGHT DATA tab — OpenDroneLog connection + import, purge, DJI API key,
 * OpenSky credentials, and DJI flight-log re-processing. Fetches
 * /settings/opendronelog, /settings/dji, /settings/opensky, and
 * /flight-library/reprocess/status on mount. All payloads/field names unchanged.
 */
export default function FlightDataTab() {
  const [odlSaving, setOdlSaving] = useState(false);
  const [odlTesting, setOdlTesting] = useState(false);
  const [odlStatus, setOdlStatus] = useState<{ status: string; message?: string } | null>(null);
  const [odlImporting, setOdlImporting] = useState(false);
  const [odlImportProgress, setOdlImportProgress] = useState({ current: 0, total: 0, imported: 0, skipped: 0, errors: 0, currentFlight: '' });

  const [djiSaving, setDjiSaving] = useState(false);
  const [djiTesting, setDjiTesting] = useState(false);
  const [djiStatus, setDjiStatus] = useState<{
    status: string; message?: string; parser_online?: boolean;
    dji_api_reachable?: boolean; key_source?: string;
  } | null>(null);

  const [openskySaving, setOpenskySaving] = useState(false);
  const [openskyTesting, setOpenskyTesting] = useState(false);
  const [openskyStatus, setOpenskyStatus] = useState<{ status: string; message?: string } | null>(null);

  const [reprocessStatus, setReprocessStatus] = useState<{ reprocessable: number; total_dji: number; stored_on_disk: number; need_manual_upload: number } | null>(null);
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessingAll, setReprocessingAll] = useState(false);
  const [reprocessResult, setReprocessResult] = useState<{ updated: number; imported?: number; skipped_no_file?: number; errors: string[] } | null>(null);

  const [purgeConfirmOpen, setPurgeConfirmOpen] = useState(false);
  const [purgeChecked, setPurgeChecked] = useState(false);
  const [purging, setPurging] = useState(false);

  const odlForm = useForm({ initialValues: { opendronelog_url: '' } });
  const djiForm = useForm({ initialValues: { dji_api_key: '' } });
  const openskyForm = useForm({ initialValues: { opensky_client_id: '', opensky_client_secret: '' } });

  useEffect(() => {
    api.get('/settings/opendronelog').then((r) => odlForm.setValues(r.data)).catch(() => {});
    api.get('/settings/dji').then((r) => djiForm.setValues(r.data)).catch(() => {});
    api.get('/settings/opensky').then((r) => openskyForm.setValues(r.data)).catch(() => {});
    api.get('/flight-library/reprocess/status').then((r) => setReprocessStatus(r.data)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  return (
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
