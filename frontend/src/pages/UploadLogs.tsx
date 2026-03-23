import { useRef, useState } from 'react';
import {
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
  IconCloudUpload,
  IconFolder,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import api from '../api/client';
import { cardStyle, monoFont } from '../components/shared/styles';

const VALID_EXTS = new Set(['csv', 'dat', 'log', 'txt']);

type FileStatus = 'pending' | 'uploading' | 'done' | 'skip' | 'error';

interface QueueEntry {
  id: number;
  file: File;
  status: FileStatus;
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
  pending:   { color: 'gray',   label: 'Pending' },
  uploading: { color: 'yellow', label: 'Uploading' },
  done:      { color: 'cyan',   label: 'Imported' },
  skip:      { color: 'violet', label: 'Already on server' },
  error:     { color: 'red',    label: 'Error' },
};

export default function UploadLogs() {
  const [queue, setQueue]       = useState<QueueEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  const fileRef   = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);

  // ── File intake ─────────────────────────────────────────────────────────
  function addFiles(raw: FileList | null) {
    if (!raw) return;
    const incoming = Array.from(raw).filter(f => VALID_EXTS.has(ext(f.name)));
    const toAdd: QueueEntry[] = [];
    for (const f of incoming) {
      const dup = queue.find(q => q.file.name === f.name && q.file.size === f.size);
      if (!dup) toAdd.push({ id: _nextId++, file: f, status: 'pending', note: '' });
    }
    if (toAdd.length === 0) {
      notifications.show({ color: 'yellow', message: 'No new log files found (duplicates or wrong extension).' });
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

  // ── Upload ───────────────────────────────────────────────────────────────
  async function uploadAll() {
    const pending = queue.filter(q => q.status === 'pending');
    if (pending.length === 0) return;

    setUploading(true);
    setProgress(10);
    setQueue(prev => prev.map(q =>
      q.status === 'pending' ? { ...q, status: 'uploading' } : q
    ));

    try {
      const form = new FormData();
      pending.forEach(q => form.append('files', q.file, q.file.name));

      const resp = await api.post('/flight-library/upload', form);
      const { imported = 0, skipped = 0, errors = [] }: {
        imported: number; skipped: number; errors: string[];
      } = resp.data;

      setProgress(100);

      setQueue(prev => prev.map(q => {
        if (q.status !== 'uploading') return q;
        if (errors.length > 0 && imported === 0) return { ...q, status: 'error',  note: errors[0] ?? 'Parse error' };
        if (skipped > 0 && imported === 0)       return { ...q, status: 'skip',   note: '' };
        return { ...q, status: 'done', note: '' };
      }));

      const parts: string[] = [];
      if (imported > 0)     parts.push(`${imported} imported`);
      if (skipped  > 0)     parts.push(`${skipped} already on server`);
      if (errors.length > 0) parts.push(`${errors.length} parse error(s)`);

      notifications.show({
        color:   errors.length > 0 && imported === 0 ? 'red' : 'cyan',
        title:   'Upload complete',
        message: parts.join(' · ') || 'No files processed',
      });
    } catch (err: any) {
      const msg =
        err?.response?.status === 401 ? 'Not authorised — please log in again' :
        err?.response?.status === 404 ? 'Upload endpoint not found' :
        err?.message ?? 'Upload failed';

      setQueue(prev => prev.map(q =>
        q.status === 'uploading' ? { ...q, status: 'error', note: msg } : q
      ));
      notifications.show({ color: 'red', title: 'Upload failed', message: msg });
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }

  // ── Derived state ────────────────────────────────────────────────────────
  const pendingCount = queue.filter(q => q.status === 'pending').length;
  const hasErrors    = queue.some(q => q.status === 'error');

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>UPLOAD LOGS</Title>
          <Text size="sm" c="#5a6478" style={monoFont}>
            Import flight logs from your controller, SD card, or computer
          </Text>
        </div>
      </Group>

      {/* ── Picker card ── */}
      <Card style={cardStyle} p="lg">
        <Text size="xs" c="#5a6478" style={{ ...monoFont, letterSpacing: 1, marginBottom: 12 }}>
          SELECT FILES
        </Text>
        <Group gap="sm" wrap="wrap">
          {/* Individual files */}
          <Button
            leftSection={<IconUpload size={16} />}
            variant="default"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            Add Files
          </Button>

          {/* Entire folder — picks all matching files recursively */}
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
          style={{ display: 'none' }}
          onChange={e => { addFiles(e.target.files); e.target.value = ''; }}
        />
        <input
          ref={folderRef}
          type="file"
          // @ts-ignore — webkitdirectory is not in React's type defs but works in all target browsers
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
          <ScrollArea mah={420}>
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
                  return (
                    <Table.Tr key={q.id}>
                      <Table.Td>
                        <Text size="sm" c="#e8edf2" style={monoFont} truncate maw={360}>
                          {(q.file as any).webkitRelativePath || q.file.name}
                        </Text>
                        {q.note && (
                          <Text size="xs" c="red" style={monoFont}>{q.note.slice(0, 80)}</Text>
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
