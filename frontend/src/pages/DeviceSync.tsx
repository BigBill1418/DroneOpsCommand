import { useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Progress,
  ScrollArea,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertCircle,
  IconCloudUpload,
  IconFolder,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import api from '../api/client';
import { cardStyle, monoFont } from '../components/shared/styles';

// Parser accepts only these two formats
const VALID_EXTS = new Set(['txt', 'csv']);

type FileStatus = 'pending' | 'uploading' | 'imported' | 'skipped' | 'error' | 'unsupported';

interface QueueEntry {
  id: number;
  file: File;
  status: FileStatus;
  note: string;
}

// Matches the FileResult schema returned by the backend
interface FileResult {
  filename: string;
  status: string;
  note: string;
}

let _nextId = 0;

function ext(name: string) {
  return name.split('.').pop()?.toLowerCase() ?? '';
}

function fmtSize(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1_048_576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1_048_576).toFixed(1)} MB`;
}

const statusMeta: Record<FileStatus, { color: string; label: string }> = {
  pending:     { color: 'gray',   label: 'Pending' },
  uploading:   { color: 'yellow', label: 'Uploading' },
  imported:    { color: 'cyan',   label: 'Imported' },
  skipped:     { color: 'violet', label: 'Already on server' },
  error:       { color: 'red',    label: 'Error' },
  unsupported: { color: 'orange', label: 'Wrong format' },
};

export default function DeviceSync() {
  const [queue, setQueue]         = useState<QueueEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress]   = useState(0);
  const [parserOnline, setParserOnline] = useState<boolean | null>(null);

  const fileRef   = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);

  // ── Parser health probe (run once on first render) ───────────────────
  const checkedRef = useRef(false);
  if (!checkedRef.current) {
    checkedRef.current = true;
    api.get('/flight-library/parser/status')
      .then(r => setParserOnline(r.data?.status === 'healthy'))
      .catch(() => setParserOnline(false));
  }

  // ── File intake ─────────────────────────────────────────────────────
  function addFiles(raw: FileList | null) {
    if (!raw) return;

    const toAdd: QueueEntry[] = [];
    let rejected = 0;

    for (const f of Array.from(raw)) {
      const e = ext(f.name);
      if (!VALID_EXTS.has(e)) { rejected++; continue; }
      // Local dedup by name + size
      if (queue.some(q => q.file.name === f.name && q.file.size === f.size)) continue;
      toAdd.push({ id: _nextId++, file: f, status: 'pending', note: '' });
    }

    if (rejected > 0) {
      notifications.show({
        color: 'orange',
        title: 'Files skipped',
        message: `${rejected} file(s) ignored — only .txt (DJI) and .csv (Litchi/Airdata) are supported`,
      });
    }
    if (toAdd.length === 0 && rejected === 0) {
      notifications.show({ color: 'yellow', message: 'No new files found (duplicates already queued).' });
      return;
    }
    setQueue(prev => [...prev, ...toAdd]);
  }

  function remove(id: number) {
    setQueue(prev => prev.filter(q => q.id !== id));
  }

  function clearAll() {
    setQueue([]);
  }

  function retryErrors() {
    setQueue(prev => prev.map(q =>
      q.status === 'error' ? { ...q, status: 'pending', note: '' } : q
    ));
  }

  // ── Upload ─────────────────────────────────────────────────────────
  async function uploadAll() {
    const pending = queue.filter(q => q.status === 'pending');
    if (pending.length === 0) return;

    setUploading(true);
    setProgress(5);

    // Mark all pending → uploading
    setQueue(prev => prev.map(q =>
      q.status === 'pending' ? { ...q, status: 'uploading' } : q
    ));

    try {
      const form = new FormData();
      pending.forEach(q => form.append('files', q.file, q.file.name));

      setProgress(20);
      const resp = await api.post('/flight-library/upload', form);
      setProgress(95);

      const {
        imported = 0,
        skipped  = 0,
        errors   = [],
        file_results = [] as FileResult[],
      } = resp.data;

      // ── Map per-file results back to queue entries ──────────────────
      // Build a lookup: filename → FileResult (last one wins on collision)
      const resultMap = new Map<string, FileResult>();
      for (const r of file_results) resultMap.set(r.filename, r);

      setQueue(prev => prev.map(q => {
        if (q.status !== 'uploading') return q;

        const result = resultMap.get(q.file.name);
        if (result) {
          const s = result.status as FileStatus;
          return { ...q, status: s, note: result.note ?? '' };
        }

        // Fallback: no per-file result (shouldn't happen with updated backend)
        if (errors.length > 0 && imported === 0 && skipped === 0) {
          return { ...q, status: 'error', note: errors[0] ?? 'Unknown error' };
        }
        return { ...q, status: 'imported', note: '' };
      }));

      setProgress(100);

      // ── Summary notification ────────────────────────────────────────
      const parts: string[] = [];
      if (imported > 0) parts.push(`${imported} imported`);
      if (skipped  > 0) parts.push(`${skipped} already on server`);
      if (errors.length > 0) parts.push(`${errors.length} error(s)`);

      notifications.show({
        color:   errors.length > 0 && imported === 0 ? 'red' : 'cyan',
        title:   'Upload complete',
        message: parts.join(' · ') || 'No files processed',
      });

      // Show each individual error as its own notification so nothing is buried
      if (errors.length > 0) {
        errors.slice(0, 5).forEach((e: string) =>
          notifications.show({ color: 'red', title: 'Parse error', message: e })
        );
        if (errors.length > 5) {
          notifications.show({
            color: 'red',
            message: `…and ${errors.length - 5} more error(s). Check the table above.`,
          });
        }
      }
    } catch (err: any) {
      const status  = err?.response?.status;
      const detail  = err?.response?.data?.detail;
      const msg =
        status === 401 ? 'Not authorised — please log in again' :
        status === 404 ? 'Upload endpoint not found — is the backend running?' :
        status === 503 ? 'flight-parser service unavailable — check container logs' :
        detail ?? err?.message ?? 'Upload failed';

      setQueue(prev => prev.map(q =>
        q.status === 'uploading' ? { ...q, status: 'error', note: msg } : q
      ));
      notifications.show({ color: 'red', title: 'Upload failed', message: msg });
    } finally {
      setUploading(false);
      setTimeout(() => setProgress(0), 800);
    }
  }

  // ── Derived counts ──────────────────────────────────────────────────
  const pendingCount = queue.filter(q => q.status === 'pending').length;
  const hasErrors    = queue.some(q => q.status === 'error');

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2} c="#e8edf2" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: 1 }}>
            Device Sync
          </Title>
          <Text size="sm" c="#5a6478" style={monoFont}>
            Upload .txt (DJI) or .csv (Litchi / Airdata) logs from a controller or SD card
          </Text>
        </div>
      </Group>

      {/* ── Parser status banner ── */}
      {parserOnline === false && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" variant="light">
          <Text size="sm" fw={600}>flight-parser service is offline</Text>
          <Text size="xs" c="dimmed" mt={4} style={monoFont}>
            Uploads will fail until the parser container is running.
            Run: <code>docker compose up flight-parser</code>
          </Text>
        </Alert>
      )}

      {/* ── Picker card ── */}
      <Card style={cardStyle} p="lg">
        <Text size="xs" c="#5a6478" style={{ ...monoFont, letterSpacing: 1, marginBottom: 12 }}>
          SELECT FILES — accepts .txt (DJI) and .csv (Litchi / Airdata) only
        </Text>
        <Group gap="sm" wrap="wrap">
          <Button
            leftSection={<IconUpload size={16} />}
            variant="default"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            Add Files
          </Button>
          <Button
            leftSection={<IconFolder size={16} />}
            variant="default"
            disabled={uploading}
            onClick={() => folderRef.current?.click()}
          >
            Add Folder
          </Button>
          <Button
            leftSection={<IconTrash size={16} />}
            variant="subtle"
            color="red"
            disabled={uploading || queue.length === 0}
            onClick={clearAll}
          >
            Clear
          </Button>
        </Group>

        {/* Hidden native inputs */}
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".txt,.csv"
          style={{ display: 'none' }}
          onChange={e => { addFiles(e.target.files); e.target.value = ''; }}
        />
        <input
          ref={folderRef}
          type="file"
          // @ts-ignore — webkitdirectory not in React's typedefs but works in all target browsers
          webkitdirectory=""
          multiple
          style={{ display: 'none' }}
          onChange={e => { addFiles(e.target.files); e.target.value = ''; }}
        />
      </Card>

      {/* ── Queue card ── */}
      <Card style={cardStyle} p="lg">
        <Group justify="space-between" mb="sm">
          <Text size="xs" c="#5a6478" style={{ ...monoFont, letterSpacing: 1 }}>
            QUEUE — {queue.length} FILE{queue.length !== 1 ? 'S' : ''}
          </Text>
          <Group gap="xs">
            {hasErrors && (
              <Button size="xs" variant="subtle" color="red" onClick={retryErrors} disabled={uploading}>
                Retry Errors
              </Button>
            )}
            <Button
              leftSection={<IconCloudUpload size={16} />}
              color="cyan"
              size="sm"
              loading={uploading}
              disabled={pendingCount === 0}
              onClick={uploadAll}
            >
              Upload {pendingCount > 0 ? `${pendingCount} File${pendingCount !== 1 ? 's' : ''}` : 'All'}
            </Button>
          </Group>
        </Group>

        {uploading && progress > 0 && (
          <Progress value={progress} color="cyan" size="xs" mb="sm" animated />
        )}

        {queue.length === 0 ? (
          <Text size="sm" c="#5a6478" ta="center" py="xl">
            No files queued. Use Add Files or Add Folder above.
          </Text>
        ) : (
          <ScrollArea mah={460}>
            <Table verticalSpacing="xs" styles={{ th: { color: '#5a6478', ...monoFont, fontSize: 11 } }}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>FILE</Table.Th>
                  <Table.Th>SIZE</Table.Th>
                  <Table.Th>STATUS</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {queue.map(q => {
                  const meta = statusMeta[q.status];
                  const displayPath = (q.file as any).webkitRelativePath || q.file.name;
                  return (
                    <Table.Tr key={q.id}>
                      <Table.Td>
                        <Text size="sm" c="#e8edf2" style={monoFont} truncate maw={380}>
                          {displayPath}
                        </Text>
                        {q.note && (
                          <Text size="xs" c={q.status === 'skipped' ? '#5a6478' : 'red'} style={monoFont}>
                            {q.note.slice(0, 120)}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="#5a6478" style={monoFont}>{fmtSize(q.file.size)}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={meta.color} variant="light" size="sm">{meta.label}</Badge>
                      </Table.Td>
                      <Table.Td>
                        {q.status === 'pending' && (
                          <Text
                            size="xs"
                            c="#5a6478"
                            style={{ cursor: 'pointer' }}
                            onClick={() => remove(q.id)}
                          >
                            ✕
                          </Text>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Card>
    </Stack>
  );
}
