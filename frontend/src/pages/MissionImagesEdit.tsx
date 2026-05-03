/**
 * MissionImagesEdit — Mission Hub redesign (v2.67.0, ADR-0014).
 *
 * Focused editor for the Images facet. Mounted at
 * `/missions/:id/images/edit`. Drag-and-drop image upload + per-image
 * delete with thumbnail preview.
 *
 * Endpoints:
 * - GET    /api/missions/{id}                         — load image list
 * - POST   /api/missions/{id}/images   (multipart)    — upload one image
 * - DELETE /api/missions/{id}/images/{image_id}       — remove
 *
 * NEVER calls POST /api/missions — see constraint comment on
 * `uploadFiles()` below.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Image,
  Loader,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { Dropzone, IMAGE_MIME_TYPE } from '@mantine/dropzone';
import type { FileWithPath } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import { IconPhoto, IconTrash, IconUpload, IconX } from '@tabler/icons-react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Mission, MissionImage } from '../api/types';
import { cardStyle } from '../components/shared/styles';

interface RowState {
  /** server image id (after upload completes) */
  imageId?: string;
  /** raw filename basename, for display */
  name: string;
  /** display URL for thumbnail (real /uploads URL, or blob: while uploading) */
  thumbUrl: string;
  status: 'uploading' | 'done' | 'error';
}

function basename(p: string): string {
  return p.split('/').pop() || p;
}

function imageThumbUrl(img: MissionImage): string {
  // backend returns absolute disk path; the static /uploads route serves
  // by basename (matches AircraftCard + Maintenance patterns).
  return `/uploads/${basename(img.file_path)}`;
}

export default function MissionImagesEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [rows, setRows] = useState<RowState[]>([]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = async () => {
      try {
        const resp = await api.get<Mission>(`/missions/${id}`);
        if (cancelled) return;
        const initial: RowState[] = resp.data.images.map((img) => ({
          imageId: img.id,
          name: basename(img.file_path),
          thumbUrl: imageThumbUrl(img),
          status: 'done' as const,
        }));
        setRows(initial);
      } catch (err) {
        console.error('[MissionImagesEdit] load failed', err);
        notifications.show({
          title: 'Load failed',
          message: 'Could not load mission images — returning to mission list.',
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
  }, [id, navigate]);

  const uploadFiles = useCallback(
    async (files: FileWithPath[]) => {
      // CONSTRAINT: this page edits an EXISTING mission only.
      // POST /missions is forbidden here per ADR-0013 / spec §2.
      // Only the per-mission images subresource is touched.
      if (!id || files.length === 0) return;

      const imageFiles = files.filter((f) => f.type.startsWith('image/'));
      if (imageFiles.length === 0) {
        notifications.show({
          title: 'No images',
          message: 'No image files in selection',
          color: 'yellow',
        });
        return;
      }

      setUploading(true);
      setProgress(0);

      // Optimistic: add 'uploading' rows with object-URL thumbnails so
      // the operator sees instant feedback.
      const optimistic: RowState[] = imageFiles.map((f) => ({
        name: f.name,
        thumbUrl: URL.createObjectURL(f),
        status: 'uploading' as const,
      }));
      const baseLen = rows.length;
      setRows((prev) => [...prev, ...optimistic]);

      let success = 0;
      for (let i = 0; i < imageFiles.length; i++) {
        const f = imageFiles[i];
        const fd = new FormData();
        fd.append('file', f);
        fd.append('caption', '');
        try {
          const resp = await api.post<MissionImage>(`/missions/${id}/images`, fd, {
            timeout: 120_000,
          });
          success++;
          setRows((prev) => {
            const copy = [...prev];
            const idx = baseLen + i;
            // Free the object URL we used while uploading.
            const old = copy[idx];
            if (old && old.thumbUrl.startsWith('blob:')) URL.revokeObjectURL(old.thumbUrl);
            copy[idx] = {
              imageId: resp.data.id,
              name: basename(resp.data.file_path),
              thumbUrl: imageThumbUrl(resp.data),
              status: 'done',
            };
            return copy;
          });
        } catch (err) {
          console.error('[MissionImagesEdit] upload failed', err);
          setRows((prev) => {
            const copy = [...prev];
            const idx = baseLen + i;
            if (copy[idx]) copy[idx] = { ...copy[idx], status: 'error' };
            return copy;
          });
        }
        setProgress(Math.round(((i + 1) / imageFiles.length) * 100));
      }

      setUploading(false);
      notifications.show({
        title: 'Upload Complete',
        message: `${success} of ${imageFiles.length} image(s) uploaded`,
        color: success === imageFiles.length ? 'cyan' : 'yellow',
      });
    },
    [id, rows.length],
  );

  const handleDelete = async (index: number) => {
    // CONSTRAINT: see uploadFiles above — no POST /missions here either.
    if (!id) return;
    const row = rows[index];
    if (row.imageId) {
      try {
        await api.delete(`/missions/${id}/images/${row.imageId}`);
      } catch (err) {
        console.error('[MissionImagesEdit] delete failed', err);
        notifications.show({
          title: 'Error',
          message: 'Failed to delete image',
          color: 'red',
        });
        return;
      }
    }
    setRows((prev) => {
      const copy = [...prev];
      const [removed] = copy.splice(index, 1);
      if (removed?.thumbUrl.startsWith('blob:')) URL.revokeObjectURL(removed.thumbUrl);
      return copy;
    });
  };

  const handleDone = () => {
    navigate(`/missions/${id}`);
  };

  // Free any leftover blob URLs on unmount.
  useEffect(
    () => () => {
      rows.forEach((r) => {
        if (r.thumbUrl.startsWith('blob:')) URL.revokeObjectURL(r.thumbUrl);
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  if (loading) {
    return (
      <Stack gap="lg" align="center" py="xl">
        <Loader color="cyan" size="lg" />
        <Text c="#5a6478">Loading images...</Text>
      </Stack>
    );
  }

  const doneCount = rows.filter((r) => r.status === 'done').length;

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          EDIT IMAGES
        </Title>
        <Button
          color="cyan"
          onClick={handleDone}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          DONE
        </Button>
      </Group>

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <Dropzone
            onDrop={uploadFiles}
            onReject={(files) => {
              console.warn('[MissionImagesEdit] drop rejected', files);
              notifications.show({
                title: 'Rejected',
                message: 'Some files were rejected (must be images, max 50MB)',
                color: 'yellow',
              });
            }}
            maxSize={50 * 1024 * 1024}
            accept={IMAGE_MIME_TYPE}
            loading={uploading}
            aria-label="Drop image files here"
            styles={{
              root: { background: '#050608', borderColor: '#1a1f2e' },
            }}
          >
            <Group justify="center" gap="md" mih={120} style={{ pointerEvents: 'none' }}>
              <Dropzone.Accept>
                <IconUpload size={42} color="#00d4ff" />
              </Dropzone.Accept>
              <Dropzone.Reject>
                <IconX size={42} color="#ff6b6b" />
              </Dropzone.Reject>
              <Dropzone.Idle>
                <IconPhoto size={42} color="#5a6478" />
              </Dropzone.Idle>
              <Box>
                <Text c="#e8edf2" size="lg" inline>
                  Drag images here or click to browse
                </Text>
                <Text c="#5a6478" size="sm" mt={4} inline>
                  JPG, PNG, WebP, TIFF — up to 50MB each
                </Text>
              </Box>
            </Group>
          </Dropzone>

          {uploading && (
            <Progress value={progress} color="cyan" size="sm" animated striped />
          )}

          <Group justify="space-between">
            <Text
              c="#00d4ff"
              fw={600}
              size="sm"
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}
            >
              IMAGES ({doneCount})
            </Text>
            {rows.length > doneCount && (
              <Badge color="yellow" variant="light" size="sm">
                {rows.length - doneCount} pending
              </Badge>
            )}
          </Group>

          {rows.length === 0 ? (
            <Text c="#5a6478" size="sm">
              No images yet. Drop some above.
            </Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 5 }} spacing="xs">
              {rows.map((row, i) => (
                <Box
                  key={row.imageId ?? `pending-${i}`}
                  style={{
                    position: 'relative',
                    background: '#050608',
                    border: '1px solid #1a1f2e',
                    borderRadius: 6,
                    overflow: 'hidden',
                    aspectRatio: '1 / 1',
                  }}
                >
                  <Image
                    src={row.thumbUrl}
                    alt={row.name}
                    fit="cover"
                    h="100%"
                    w="100%"
                    fallbackSrc="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100'><rect width='100' height='100' fill='%23050608'/><text x='50%' y='50%' fill='%235a6478' font-size='10' text-anchor='middle' dominant-baseline='middle'>no preview</text></svg>"
                    style={{ opacity: row.status === 'uploading' ? 0.5 : 1 }}
                  />
                  {row.status === 'uploading' && (
                    <Box
                      style={{
                        position: 'absolute',
                        inset: 0,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: 'rgba(0,0,0,0.4)',
                      }}
                    >
                      <Loader color="cyan" size="sm" />
                    </Box>
                  )}
                  {row.status === 'error' && (
                    <Badge
                      color="red"
                      size="xs"
                      style={{ position: 'absolute', top: 4, left: 4 }}
                    >
                      Failed
                    </Badge>
                  )}
                  <ActionIcon
                    color="red"
                    variant="filled"
                    size="sm"
                    onClick={() => handleDelete(i)}
                    aria-label={`Delete ${row.name}`}
                    style={{
                      position: 'absolute',
                      top: 4,
                      right: 4,
                      opacity: 0.9,
                    }}
                  >
                    <IconTrash size={12} />
                  </ActionIcon>
                  <Text
                    c="#e8edf2"
                    size="xs"
                    truncate
                    style={{
                      position: 'absolute',
                      bottom: 0,
                      left: 0,
                      right: 0,
                      padding: '2px 6px',
                      background: 'rgba(0,0,0,0.6)',
                      fontFamily: "'Share Tech Mono', monospace",
                    }}
                  >
                    {row.name}
                  </Text>
                </Box>
              ))}
            </SimpleGrid>
          )}
        </Stack>
      </Card>

      <Group justify="flex-end">
        <Button
          variant="default"
          onClick={handleDone}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          BACK TO MISSION
        </Button>
      </Group>
    </Stack>
  );
}
