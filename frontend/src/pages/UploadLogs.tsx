import { useRef, useState, useCallback } from 'react';
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

// Recursively extract files from DataTransferItem directory entries
async function extractFilesFromEntries(items: DataTransferItemList): Promise<File[]> {
  const files: File[] = [];

  async function readEntry(entry: FileSystemEntry): Promise<void> {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => {
        (entry as FileSystemFileEntry).file(resolve, reject);
      });
      files.push(file);
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const entries = await new Promise<FileSystemEntry[]>((resolve, reject) => {
        reader.readEntries(resolve, reject);
      });
      for (const e of entries) {
        await readEntry(e);
      }
    }
  }

  const entries: FileSystemEntry[] = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry?.();
    if (entry) entries.push(entry);
  }
  for (const entry of entries) {
    await readEntry(entry);
  }
  return files;
}

export default function UploadLogs() {
  const [queue, setQueue]       = useState<QueueEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);

  const fileRef   = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);

  // ── File intake ─────────────────────────────────────────────────────────
  const addFiles = useCallback((raw: File[] | FileList | null) => {
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
  }, [queue]);

  // ── Drag and drop handlers ────────────────────────────────────────────
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);

    if (uploading) return;

    // Try to extract entries (supports folders)
    if (e.dataTransfer.items?.length) {
      const hasDirectories = Array.from(e.dataTransfer.items).some(
        item => item.webkitGetAsEntry?.()?.isDirectory
      );
      if (hasDirectories) {
        const files = await extractFilesFromEntries(e.dataTransfer.items);
        addFiles(files);
        return;
      }
    }

    // Fallback: plain file list
    addFiles(e.dataTransfer.files);
  }, [uploading, addFiles]);

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

  // ── Upload (batched to avoid 413 / timeout on large sets) ────────────────
  const BATCH_MAX_BYTES = 40 * 1024 * 1024; // 40 MB per request

  function buildBatches(files: QueueEntry[]): QueueEntry[][] {
    const batches: QueueEntry[][] = [];
    let current: QueueEntry[] = [];
    let currentSize = 0;

    for (const q of files) {
      // Start a new batch if adding this file would exceed the limit
      // (but always allow at least one file per batch)
      if (current.length > 0 && currentSize + q.file.size > BATCH_MAX_BYTES) {
        batches.push(current);
        current = [];
        currentSize = 0;
      }
      current.push(q);
      currentSize += q.file.size;
    }
    if (current.length > 0) batches.push(current);
    return batches;
  }

  async function uploadAll() {
    const pending = queue.filter(q => q.status === 'pending');
    if (pending.length === 0) return;

    setUploading(true);
    setProgress(5);
    setQueue(prev => prev.map(q =>
      q.status === 'pending' ? { ...q, status: 'uploading' } : q
    ));

    const batches = buildBatches(pending);
    let totalImported = 0;
    let totalSkipped = 0;
    let totalErrors: string[] = [];
    const batchIdSets = batches.map(b => new Set(b.map(q => q.id)));

    for (let i = 0; i < batches.length; i++) {
      const batch = batches[i];
      const batchIds = batchIdSets[i];

      try {
        const form = new FormData();
        batch.forEach(q => form.append('files', q.file, q.file.name));

        const resp = await api.post('/flight-library/upload', form, {
          timeout: 120000, // 2 min per batch — flight logs can be large
        });
        const { imported = 0, skipped = 0, errors = [] }: {
          imported: number; skipped: number; errors: string[];
        } = resp.data;

        totalImported += imported;
        totalSkipped += skipped;
        totalErrors = totalErrors.concat(errors);

        // Mark this batch's files
        setQueue(prev => prev.map(q => {
          if (!batchIds.has(q.id) || q.status !== 'uploading') return q;
          if (errors.length > 0 && imported === 0) return { ...q, status: 'error', note: errors[0] ?? 'Parse error' };
          if (skipped > 0 && imported === 0)       return { ...q, status: 'skip', note: '' };
          return { ...q, status: 'done', note: '' };
        }));
      } catch (err: any) {
        const msg =
          err?.response?.status === 401 ? 'Not authorised — please log in again' :
          err?.response?.status === 404 ? 'Upload endpoint not found' :
          err?.response?.status === 413 ? 'Files too large — try uploading fewer at once' :
          err?.code === 'ECONNABORTED' ? 'Upload timed out — check connection' :
          err?.message ?? 'Upload failed';

        setQueue(prev => prev.map(q =>
          batchIds.has(q.id) && q.status === 'uploading'
            ? { ...q, status: 'error', note: msg }
            : q
        ));
        totalErrors.push(msg);
      }

      // Update progress across batches
      setProgress(Math.round(((i + 1) / batches.length) * 100));
    }

    // Summary notification
    const parts: string[] = [];
    if (totalImported > 0)     parts.push(`${totalImported} imported`);
    if (totalSkipped > 0)      parts.push(`${totalSkipped} already on server`);
    if (totalErrors.length > 0) parts.push(`${totalErrors.length} error(s)`);

    notifications.show({
      color:   totalErrors.length > 0 && totalImported === 0 ? 'red' : 'cyan',
      title:   batches.length > 1 ? `Upload complete (${batches.length} batches)` : 'Upload complete',
      message: parts.join(' · ') || 'No files processed',
    });

    setUploading(false);
    setProgress(0);
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

      {/* ── Drop zone + picker card ── */}
      <Card
        style={{
          ...cardStyle,
          border: dragOver ? '2px dashed #00d4ff' : '1px solid #1a1f2e',
          background: dragOver ? 'rgba(0, 212, 255, 0.04)' : '#0e1117',
          transition: 'border 0.15s, background 0.15s',
        }}
        p={0}
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {/* Drop target area */}
        <div
          style={{
            padding: '32px 24px',
            textAlign: 'center',
            cursor: uploading ? 'not-allowed' : 'pointer',
          }}
          onClick={() => !uploading && fileRef.current?.click()}
        >
          <IconCloudUpload
            size={48}
            color={dragOver ? '#00d4ff' : '#2a3040'}
            style={{ marginBottom: 12, transition: 'color 0.15s' }}
          />
          <Text c={dragOver ? '#00d4ff' : '#e8edf2'} fw={700} size="lg" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>
            {dragOver ? 'DROP FILES HERE' : 'DRAG & DROP FLIGHT LOGS'}
          </Text>
          <Text c="#5a6478" size="xs" style={monoFont} mt={4}>
            Drop files or folders here, or click to browse — accepts .txt, .csv, .dat, .log
          </Text>
        </div>

        {/* Button row */}
        <Group
          gap="sm"
          wrap="wrap"
          px="lg"
          pb="lg"
          justify="center"
          style={{ borderTop: '1px solid #1a1f2e', paddingTop: 16 }}
        >
          <Button
            leftSection={<IconUpload size={16} />}
            variant="default"
            disabled={uploading}
            onClick={(e) => { e.stopPropagation(); fileRef.current?.click(); }}
          >
            Add Files
          </Button>

          <Button
            leftSection={<IconFolder size={16} />}
            variant="default"
            disabled={uploading}
            onClick={(e) => { e.stopPropagation(); folderRef.current?.click(); }}
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
