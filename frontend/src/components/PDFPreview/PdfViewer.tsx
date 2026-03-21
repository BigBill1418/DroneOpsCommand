import { useState, useRef, useEffect, useCallback } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import { Box, Group, ActionIcon, Text, Loader, Center, Button, Stack, Tooltip } from '@mantine/core';
import {
  IconDownload,
  IconZoomIn,
  IconZoomOut,
  IconRefresh,
  IconArrowsMaximize,
} from '@tabler/icons-react';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Configure PDF.js worker — use Vite's ?url import so the worker file is
// handled as a static asset in both dev and production builds. This avoids
// the fragile custom copy plugin and works reliably through CDNs/proxies.
import pdfjsWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
pdfjs.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl;

interface PdfViewerProps {
  url: string;
  height?: number;
  showToolbar?: boolean;
  downloadFilename?: string;
}

export default function PdfViewer({ url, height = 600, showToolbar = true, downloadFilename }: PdfViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [scale, setScale] = useState(1.2);
  const [containerWidth, setContainerWidth] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());

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

  // Track which page is visible during scroll
  useEffect(() => {
    const scrollEl = scrollRef.current;
    if (!scrollEl || numPages === 0) return;
    const handleScroll = () => {
      const scrollTop = scrollEl.scrollTop;
      const scrollMid = scrollTop + scrollEl.clientHeight / 3;
      let closest = 1;
      let closestDist = Infinity;
      pageRefs.current.forEach((el, pageNum) => {
        const dist = Math.abs(el.offsetTop - scrollMid);
        if (dist < closestDist) {
          closestDist = dist;
          closest = pageNum;
        }
      });
      setCurrentPage(closest);
    };
    scrollEl.addEventListener('scroll', handleScroll, { passive: true });
    return () => scrollEl.removeEventListener('scroll', handleScroll);
  }, [numPages]);

  const onDocumentLoadSuccess = ({ numPages }: { numPages: number }) => {
    setNumPages(numPages);
    setCurrentPage(1);
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

  const resetZoom = useCallback(() => setScale(1.2), []);

  const pageWidth = containerWidth > 0 ? (containerWidth - 32) * scale : undefined;

  const setPageRef = useCallback((pageNum: number) => (el: HTMLDivElement | null) => {
    if (el) {
      pageRefs.current.set(pageNum, el);
    } else {
      pageRefs.current.delete(pageNum);
    }
  }, []);

  return (
    <Box
      ref={containerRef}
      style={{
        border: '1px solid #1a1f2e',
        borderRadius: 8,
        overflow: 'hidden',
        background: '#1a1a1a',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Toolbar at top for better visibility */}
      {showToolbar && numPages > 0 && (
        <Group
          justify="space-between"
          px="md"
          py={8}
          style={{
            background: '#0e1117',
            borderBottom: '1px solid #1a1f2e',
            flexShrink: 0,
          }}
        >
          <Group gap="sm">
            <Tooltip label="Zoom out" position="bottom" withArrow>
              <ActionIcon
                variant="subtle"
                color="gray"
                size="md"
                onClick={() => setScale((s) => Math.max(0.5, +(s - 0.15).toFixed(2)))}
                disabled={scale <= 0.5}
              >
                <IconZoomOut size={16} />
              </ActionIcon>
            </Tooltip>
            <Text
              c="#8a94a6"
              size="xs"
              style={{
                fontFamily: "'Share Tech Mono', monospace",
                minWidth: 44,
                textAlign: 'center',
                userSelect: 'none',
              }}
            >
              {Math.round(scale * 100)}%
            </Text>
            <Tooltip label="Zoom in" position="bottom" withArrow>
              <ActionIcon
                variant="subtle"
                color="gray"
                size="md"
                onClick={() => setScale((s) => Math.min(3.0, +(s + 0.15).toFixed(2)))}
                disabled={scale >= 3.0}
              >
                <IconZoomIn size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Fit to width" position="bottom" withArrow>
              <ActionIcon
                variant="subtle"
                color="gray"
                size="md"
                onClick={resetZoom}
              >
                <IconArrowsMaximize size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>

          <Text
            c="#8a94a6"
            size="xs"
            style={{ fontFamily: "'Share Tech Mono', monospace", userSelect: 'none' }}
          >
            Page {currentPage} of {numPages}
          </Text>

          {downloadFilename ? (
            <Tooltip label="Download PDF" position="bottom" withArrow>
              <ActionIcon
                variant="subtle"
                color="cyan"
                size="md"
                component="a"
                href={url}
                download={downloadFilename}
              >
                <IconDownload size={16} />
              </ActionIcon>
            </Tooltip>
          ) : (
            <Box style={{ width: 28 }} />
          )}
        </Group>
      )}

      {/* Continuous scroll document area */}
      <Box
        ref={scrollRef}
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
          <div style={{ padding: '12px 0' }}>
            {Array.from({ length: numPages }, (_, i) => (
              <div
                key={i + 1}
                ref={setPageRef(i + 1)}
                style={{
                  marginBottom: i < numPages - 1 ? 12 : 0,
                  display: 'flex',
                  justifyContent: 'center',
                  background: '#fff',
                  marginLeft: 'auto',
                  marginRight: 'auto',
                  width: 'fit-content',
                  boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
                  borderRadius: 2,
                }}
              >
                <Page
                  pageNumber={i + 1}
                  width={pageWidth}
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                />
              </div>
            ))}
          </div>
        </Document>
      </Box>
    </Box>
  );
}
