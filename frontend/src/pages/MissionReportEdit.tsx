/**
 * MissionReportEdit — facet editor for a mission's Report.
 *
 * v2.67.0 Mission Hub redesign. Extracted verbatim from
 * `MissionNew.tsx` Step 4 (narrative + AI generation + polling
 * + draft save) plus Step 6's Generate PDF + Send-to-Customer
 * actions which logically belong with the report.
 *
 * Mounted at `/missions/:id/report/edit` (Agent D wires the route).
 *
 * Per ADR-0014 / spec §3 §5.3:
 *   - This page edits the report on an EXISTING mission only.
 *   - The only mutation endpoints touched are
 *     `PUT /missions/{id}/report`,
 *     `POST /missions/{id}/report/generate`,
 *     `POST /missions/{id}/report/pdf`,
 *     `POST /missions/{id}/report/send`.
 *   - Cancel / Save returns to `/missions/:id` (the Hub).
 *
 * The AI-generation polling logic is preserved verbatim from
 * MissionNew.tsx (battle-tested; do not reinvent).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Switch,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconArrowLeft,
  IconDeviceFloppy,
  IconDownload,
  IconRobot,
  IconSend,
} from '@tabler/icons-react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Mission } from '../api/types';
import RichTextEditor from '../components/RichTextEditor/RichTextEditor';
import PdfViewer from '../components/PDFPreview/PdfViewer';
import UnsavedChangesModal from '../components/shared/UnsavedChangesModal';
import { useDirtyGuard } from '../hooks/useDirtyGuard';

const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: {
    color: '#5a6478',
    fontFamily: "'Share Tech Mono', monospace",
    fontSize: '13px',
    letterSpacing: '1px',
  },
};

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

/** Format a save timestamp into a "X min ago" inline string. */
function relativeAgo(ts: string | null): string | null {
  if (!ts) return null;
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return null;
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(ts).toLocaleString();
}

export default function MissionReportEdit() {
  const { id: missionId } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [missionTitle, setMissionTitle] = useState<string>('');
  const [downloadLinkUrl, setDownloadLinkUrl] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);

  // Report state
  const [narrative, setNarrative] = useState<string>('');
  const [reportContent, setReportContent] = useState<string>('');
  const [includeDownloadLink, setIncludeDownloadLink] = useState<boolean>(false);

  // Baseline snapshot for dirty-guard. Populated on initial load and
  // re-baselined after a successful Save Draft / AI generate / PDF
  // generate (each persists at least one of these fields).
  const [baseline, setBaseline] = useState({
    narrative: '',
    reportContent: '',
    includeDownloadLink: false,
  });

  // Action state
  const [generating, setGenerating] = useState<boolean>(false);
  const [savingDraft, setSavingDraft] = useState<boolean>(false);
  const [generatingPdf, setGeneratingPdf] = useState<boolean>(false);
  const [sending, setSending] = useState<boolean>(false);

  // PDF preview
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);

  // Last-saved/sent indicators
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [lastSentAt, setLastSentAt] = useState<string | null>(null);
  const [lastGeneratedAt, setLastGeneratedAt] = useState<string | null>(null);

  // AI generation polling — preserved verbatim from MissionNew.tsx.
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  // Cleanup polling and PDF blob URL on unmount.
  useEffect(
    () => () => {
      stopPolling();
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
    },
    [stopPolling, pdfBlobUrl],
  );

  // Initial load — mission context + existing report.
  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;

    (async () => {
      try {
        const [missionResp, reportResult] = await Promise.allSettled([
          api.get(`/missions/${missionId}`),
          api.get(`/missions/${missionId}/report`),
        ]);

        if (cancelled) return;

        if (missionResp.status === 'fulfilled') {
          const m: Mission = missionResp.value.data;
          setMissionTitle(m.title || '');
          setDownloadLinkUrl(m.download_link_url || '');
        } else {
          notifications.show({
            title: 'Error',
            message: 'Failed to load mission',
            color: 'red',
          });
          navigate(`/missions/${missionId}`);
          return;
        }

        if (reportResult.status === 'fulfilled') {
          const r = reportResult.value.data || {};
          const initialNarrative = r.user_narrative || '';
          const initialContent = r.final_content || '';
          const initialIncludeLink = Boolean(r.include_download_link);
          setNarrative(initialNarrative);
          setReportContent(initialContent);
          setIncludeDownloadLink(initialIncludeLink);
          setBaseline({
            narrative: initialNarrative,
            reportContent: initialContent,
            includeDownloadLink: initialIncludeLink,
          });
          setLastSavedAt(r.updated_at || r.generated_at || null);
          setLastSentAt(r.sent_at || null);
          setLastGeneratedAt(r.generated_at || null);
        }
      } catch (err) {
        if (!cancelled) {
          console.error('[MissionReportEdit] initial load failed', err);
          notifications.show({
            title: 'Error',
            message: 'Failed to load mission report',
            color: 'red',
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [missionId, navigate]);

  /** Save the current narrative + final content as a draft. */
  const handleSaveDraft = async () => {
    if (!missionId) return;
    // CONSTRAINT: this page edits the report on an EXISTING mission.
    // POST /missions is forbidden here per ADR-0013 / spec §2.
    setSavingDraft(true);
    try {
      const resp = await api.put(`/missions/${missionId}/report`, {
        user_narrative: narrative || undefined,
        final_content: reportContent || undefined,
        include_download_link: includeDownloadLink,
      });
      const ts =
        resp?.data?.updated_at ||
        resp?.data?.generated_at ||
        new Date().toISOString();
      setLastSavedAt(ts);
      // Re-baseline so a subsequent Cancel doesn't prompt for changes
      // we just persisted.
      setBaseline({
        narrative,
        reportContent,
        includeDownloadLink,
      });
      notifications.show({
        title: 'Draft Saved',
        message: 'Report draft has been saved',
        color: 'cyan',
      });
    } catch (err: any) {
      notifications.show({
        title: 'Error',
        message:
          err?.response?.data?.detail ||
          'Failed to save draft. Try restarting the backend.',
        color: 'red',
      });
    } finally {
      setSavingDraft(false);
    }
  };

  /** Trigger AI generation; poll for completion; populate final content. */
  const handleGenerate = async () => {
    if (!missionId) return;
    setGenerating(true);
    try {
      const resp = await api.post(`/missions/${missionId}/report/generate`, {
        user_narrative: narrative,
        include_download_link: includeDownloadLink,
      });
      const taskId = resp?.data?.task_id;
      if (!taskId) {
        // Fallback: synchronous response (older backends).
        const syncContent = resp?.data?.final_content || '';
        setReportContent(syncContent);
        setLastGeneratedAt(new Date().toISOString());
        setBaseline({
          narrative,
          reportContent: syncContent,
          includeDownloadLink,
        });
        setGenerating(false);
        return;
      }

      notifications.show({
        title: 'Generating Report',
        message:
          'The AI is writing your report. You can navigate away — it will keep going.',
        color: 'cyan',
      });

      // Poll for completion (3-second cadence — preserved verbatim).
      pollIntervalRef.current = setInterval(async () => {
        try {
          const status = await api.get(
            `/missions/${missionId}/report/status/${taskId}`,
          );
          if (status?.data?.status === 'complete') {
            stopPolling();
            try {
              const reportResp = await api.get(`/missions/${missionId}/report`);
              const generatedContent = reportResp?.data?.final_content || '';
              setReportContent(generatedContent);
              setLastGeneratedAt(
                reportResp?.data?.generated_at || new Date().toISOString(),
              );
              // AI generation persisted on the server; treat narrative
              // + new content as the new clean baseline so Cancel
              // doesn't fire after a successful Generate.
              setBaseline({
                narrative,
                reportContent: generatedContent,
                includeDownloadLink,
              });
              notifications.show({
                title: 'Report Ready',
                message: 'Your AI report is ready for review',
                color: 'green',
              });
            } catch {
              notifications.show({
                title: 'Report Generated',
                message: 'Report is ready — reload the page to view it',
                color: 'cyan',
              });
            }
            setGenerating(false);
          } else if (status?.data?.status === 'failed') {
            stopPolling();
            notifications.show({
              title: 'Generation Failed',
              message: status?.data?.detail || 'Report generation failed',
              color: 'red',
            });
            setGenerating(false);
          }
        } catch {
          // Network blip — keep polling.
        }
      }, 3000);
    } catch (err: any) {
      notifications.show({
        title: 'Generation Failed',
        message:
          err?.response?.data?.detail ||
          'Could not generate report. Is Ollama running?',
        color: 'red',
      });
      setGenerating(false);
    }
  };

  /** Render the report PDF and show inline preview. */
  const handleGeneratePDF = async () => {
    if (!missionId) return;
    setGeneratingPdf(true);
    try {
      // Persist final report content first so the PDF reflects edits.
      if (reportContent) {
        await api.put(`/missions/${missionId}/report`, {
          final_content: reportContent,
        });
        setLastSavedAt(new Date().toISOString());
        // The PUT only included final_content but the operator's
        // narrative + include-link toggle live alongside it. Re-
        // baseline all three so Cancel after a Generate-PDF doesn't
        // prompt; the narrative may genuinely still differ from server
        // but if the operator triggered Generate-PDF we treat that as
        // an explicit "ship it" intent.
        setBaseline({
          narrative,
          reportContent,
          includeDownloadLink,
        });
      }

      const resp = await api.post(
        `/missions/${missionId}/report/pdf`,
        {},
        { responseType: 'blob', timeout: 120000 },
      );
      if (!resp?.data || resp.data.size === 0) {
        notifications.show({
          title: 'Error',
          message: 'PDF returned empty',
          color: 'red',
        });
        return;
      }
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
      const blobUrl = URL.createObjectURL(
        new Blob([resp.data], { type: 'application/pdf' }),
      );
      setPdfBlobUrl(blobUrl);
      notifications.show({
        title: 'PDF Generated',
        message: 'Preview loaded below',
        color: 'cyan',
      });
    } catch (err: unknown) {
      let message = 'Failed to generate PDF';
      try {
        const axiosErr = err as {
          response?: { data?: Blob | { detail?: string } };
        };
        const data = axiosErr.response?.data;
        if (data instanceof Blob) {
          const text = await data.text();
          const json = JSON.parse(text);
          if (json.detail) message = json.detail;
        } else if (data && typeof data === 'object' && 'detail' in data) {
          message = (data as { detail: string }).detail;
        }
      } catch {
        /* keep default message */
      }
      notifications.show({ title: 'Error', message, color: 'red' });
    } finally {
      setGeneratingPdf(false);
    }
  };

  /** Email the rendered PDF to the customer of record. */
  const handleSend = async () => {
    if (!missionId) return;
    setSending(true);
    try {
      const resp = await api.post(`/missions/${missionId}/report/send`);
      const ts =
        resp?.data?.sent_at ||
        resp?.data?.updated_at ||
        new Date().toISOString();
      setLastSentAt(ts);
      notifications.show({
        title: 'Sent',
        message: 'Report emailed to customer',
        color: 'green',
      });
    } catch (err: any) {
      notifications.show({
        title: 'Send Failed',
        message: err?.response?.data?.detail || 'Failed to send email',
        color: 'red',
      });
    } finally {
      setSending(false);
    }
  };

  // Dirty calc: any of the 3 operator-editable fields drifted from the
  // baseline snapshot. Gate to false during initial load.
  const isDirty =
    !loading &&
    (narrative !== baseline.narrative ||
      reportContent !== baseline.reportContent ||
      includeDownloadLink !== baseline.includeDownloadLink);

  const { showConfirm, setShowConfirm, guardedNavigate, confirmAndNavigate } =
    useDirtyGuard({ isDirty, navigate });

  const handleCancel = () => {
    guardedNavigate(missionId ? `/missions/${missionId}` : '/missions');
  };

  if (!missionId) {
    return (
      <Stack gap="md">
        <Text c="red">Missing mission id in route.</Text>
      </Stack>
    );
  }

  if (loading) {
    return (
      <Stack gap="lg" align="center" py="xl">
        <Loader color="cyan" size="lg" />
        <Text c="#5a6478">Loading mission report...</Text>
      </Stack>
    );
  }

  const downloadFilename = `Report_${(missionTitle || 'Mission').replace(/\s+/g, '_')}.pdf`;
  const savedHint = relativeAgo(lastSavedAt);
  const sentHint = relativeAgo(lastSentAt);
  const generatedHint = relativeAgo(lastGeneratedAt);

  return (
    <Stack gap="lg">
      <Group justify="space-between" wrap="wrap">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          EDIT REPORT
        </Title>
        <Button
          variant="subtle"
          color="gray"
          leftSection={<IconArrowLeft size={16} />}
          onClick={handleCancel}
        >
          Cancel
        </Button>
      </Group>
      {missionTitle && (
        <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          {missionTitle}
        </Text>
      )}

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <Text c="#e8edf2" fw={600}>
            Operator Notes
          </Text>
          <Textarea
            placeholder="Describe what happened during the mission, conditions, findings, outcome..."
            value={narrative}
            onChange={(e) => setNarrative(e.target.value)}
            minRows={8}
            autosize
            styles={inputStyles}
          />
          <Switch
            label="Include download link in report"
            description={
              downloadLinkUrl
                ? 'Client will see a download button for mission footage'
                : 'Set a download link URL on the Details editor first'
            }
            color="cyan"
            checked={includeDownloadLink}
            onChange={(e) => setIncludeDownloadLink(e.currentTarget.checked)}
            disabled={!downloadLinkUrl}
          />
          <Group justify="space-between" wrap="wrap">
            <Button
              leftSection={
                generating ? (
                  <Loader size={16} color="white" />
                ) : (
                  <IconRobot size={16} />
                )
              }
              color="cyan"
              onClick={handleGenerate}
              disabled={generating || !narrative}
              styles={{
                root: {
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '1px',
                },
              }}
            >
              {generating
                ? 'GENERATING...'
                : reportContent
                  ? 'REGENERATE REPORT'
                  : 'GENERATE REPORT'}
            </Button>
            {generatedHint && (
              <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                AI generated {generatedHint}
              </Text>
            )}
          </Group>

          {reportContent && (
            <>
              <Text
                c="#00d4ff"
                fw={600}
                style={{
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '1px',
                }}
              >
                FINAL REPORT
              </Text>
              <RichTextEditor
                content={reportContent}
                onChange={setReportContent}
                minHeight="400px"
              />
            </>
          )}

          <Group justify="space-between" wrap="wrap">
            <Group>
              <Button
                leftSection={
                  savingDraft ? (
                    <Loader size={16} color="white" />
                  ) : (
                    <IconDeviceFloppy size={16} />
                  )
                }
                color="gray"
                variant="light"
                onClick={handleSaveDraft}
                disabled={savingDraft || (!narrative && !reportContent)}
                styles={{
                  root: {
                    fontFamily: "'Bebas Neue', sans-serif",
                    letterSpacing: '1px',
                  },
                }}
              >
                {savingDraft ? 'SAVING...' : 'SAVE DRAFT'}
              </Button>
              {savedHint && (
                <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  Last saved {savedHint}
                </Text>
              )}
            </Group>
          </Group>
        </Stack>
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <Text
            c="#e8edf2"
            fw={600}
            size="lg"
            style={{
              fontFamily: "'Bebas Neue', sans-serif",
              letterSpacing: '1px',
            }}
          >
            DELIVER REPORT
          </Text>
          <Group wrap="wrap">
            <Button
              leftSection={
                generatingPdf ? (
                  <Loader size={16} color="white" />
                ) : (
                  <IconDownload size={16} />
                )
              }
              color="cyan"
              onClick={handleGeneratePDF}
              disabled={generatingPdf || !reportContent}
              styles={{
                root: {
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '1px',
                },
              }}
            >
              {generatingPdf ? 'GENERATING...' : 'GENERATE PDF'}
            </Button>
            <Button
              leftSection={
                sending ? <Loader size={16} color="white" /> : <IconSend size={16} />
              }
              color="orange"
              variant="light"
              onClick={handleSend}
              disabled={sending || !reportContent}
              styles={{
                root: {
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '1px',
                },
              }}
            >
              {sending ? 'SENDING...' : 'SEND TO CUSTOMER'}
            </Button>
            {sentHint && (
              <Text size="xs" c="green" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Last sent {sentHint}
              </Text>
            )}
          </Group>

          {pdfBlobUrl && (
            <PdfViewer
              url={pdfBlobUrl}
              height={700}
              downloadFilename={downloadFilename}
            />
          )}
        </Stack>
      </Card>

      <UnsavedChangesModal
        opened={showConfirm}
        onKeepEditing={() => setShowConfirm(false)}
        onDiscard={confirmAndNavigate}
      />
    </Stack>
  );
}
