import { useState, useRef, useEffect, useCallback } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import { Box, Group, ActionIcon, Text, Loader, Center, Button, Stack } from '@mantine/core';
import { IconChevronLeft, IconChevronRight, IconDownload, IconZoomIn, IconZoomOut, IconRefresh } from '@tabler/icons-react';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Configure PDF.js worker — use stable path (copied by Vite plugin, no content hash)
pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

interface PdfViewerProps {
  url: string;
  height?: number;
  showToolbar?: boolean;
  downloadFilename?: string;
}

export default function PdfViewer({ url, height = 500, showToolbar = true, downloadFilename }: PdfViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1.0);
  const [containerWidth, setContainerWidth] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  const onDocumentLoadSuccess = ({ numPages }: { numPages: number }) => {
    setNumPages(numPages);
    setPageNumber(1);
    setLoadError(null);
  };

  const onDocumentLoadError = useCallback((error: Error) => {
    console.error('[PdfViewer] Load error:', error.message);
    setLoadError(error.message || 'Failed to load PDF document');
  }, []);

  const handleRetry = useCallback(() => {
    setLoadError(null);
    setNumPages(0);
    setRetryKey((k) => k + 1);
  }, []);

  const pageWidth = containerWidth > 0 ? (containerWidth - 2) * scale : undefined;

  return (
    <Box
      ref={containerRef}
      style={{
        border: '1px solid #1a1f2e',
        borderRadius: 6,
        overflow: 'hidden',
        background: '#2a2a2a',
      }}
    >
      <Box
        style={{
          height,
          overflow: 'auto',
          display: 'flex',
          justifyContent: 'center',
        }}
      >
        <Document
          key={retryKey}
          file={url}
          onLoadSuccess={onDocumentLoadSuccess}
          onLoadError={onDocumentLoadError}
          loading={
            <Center style={{ height }}>
              <Loader color="cyan" size="sm" />
              <Text c="#5a6478" size="sm" ml="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Loading document...
              </Text>
            </Center>
          }
          error={
            <Center style={{ height, flexDirection: 'column', gap: 12, padding: 20 }}>
              <Stack align="center" gap="sm">
                <Text c="#5a6478" size="sm" ta="center" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  {loadError || 'Could not render PDF.'}
                </Text>
                <Group gap="xs">
                  <Button
                    variant="light"
                    color="cyan"
                    size="xs"
                    leftSection={<IconRefresh size={14} />}
                    onClick={handleRetry}
                    styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
                  >
                    RETRY
                  </Button>
                  {downloadFilename && (
                    <Button
                      variant="light"
                      color="cyan"
                      size="xs"
                      component="a"
                      href={url}
                      download={downloadFilename}
                      leftSection={<IconDownload size={14} />}
                      styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
                    >
                      DOWNLOAD PDF
                    </Button>
                  )}
                </Group>
              </Stack>
            </Center>
          }
        >
          <Page
            pageNumber={pageNumber}
            width={pageWidth}
            renderTextLayer={true}
            renderAnnotationLayer={true}
          />
        </Document>
      </Box>

      {showToolbar && numPages > 0 && (
        <Group
          justify="space-between"
          px="sm"
          py={6}
          style={{ background: '#0e1117', borderTop: '1px solid #1a1f2e' }}
        >
          <Group gap="xs">
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              onClick={() => setScale((s) => Math.max(0.5, s - 0.15))}
              disabled={scale <= 0.5}
            >
              <IconZoomOut size={14} />
            </ActionIcon>
            <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", minWidth: 40, textAlign: 'center' }}>
              {Math.round(scale * 100)}%
            </Text>
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              onClick={() => setScale((s) => Math.min(2.5, s + 0.15))}
              disabled={scale >= 2.5}
            >
              <IconZoomIn size={14} />
            </ActionIcon>
          </Group>

          <Group gap="xs">
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
              disabled={pageNumber <= 1}
            >
              <IconChevronLeft size={14} />
            </ActionIcon>
            <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              {pageNumber} / {numPages}
            </Text>
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))}
              disabled={pageNumber >= numPages}
            >
              <IconChevronRight size={14} />
            </ActionIcon>
          </Group>

          {downloadFilename && (
            <Button
              variant="subtle"
              color="cyan"
              size="xs"
              component="a"
              href={url}
              download={downloadFilename}
              leftSection={<IconDownload size={14} />}
              styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
            >
              DOWNLOAD
            </Button>
          )}
          {!downloadFilename && <Box style={{ width: 1 }} />}
        </Group>
      )}
    </Box>
  );
}
