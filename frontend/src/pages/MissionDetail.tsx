import { useEffect, useState, useRef, useCallback } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  CopyButton,
  Group,
  Loader,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import PdfViewer from '../components/PDFPreview/PdfViewer';
import {
  IconCheck,
  IconCopy,
  IconDeviceFloppy,
  IconDownload,
  IconEdit,
  IconExternalLink,
  IconLink,
  IconMail,
  IconRobot,
  IconSend,
  IconTrash,
} from '@tabler/icons-react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Mission, Report, Aircraft, CoverageData, Invoice } from '../api/types';
import FlightMap from '../components/FlightMap/FlightMap';
import AircraftCard from '../components/AircraftCard/AircraftCard';
import RichTextEditor from '../components/RichTextEditor/RichTextEditor';
import { inputStyles, statusColors } from '../components/shared/styles';
import { IconAlertTriangle, IconCircleCheck, IconCircleMinus, IconReceipt2, IconFlagFilled } from '@tabler/icons-react';
import { Modal } from '@mantine/core';

export default function MissionDetail() {
  const { id } = useParams<{ id: string }>();
  const [mission, setMission] = useState<Mission | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [mapGeojson, setMapGeojson] = useState<{ type: string; features: Array<{ type: string; geometry: { type: string; coordinates: number[][] }; properties: Record<string, unknown> }> } | null>(null);
  const [coverage, setCoverage] = useState<CoverageData | null>(null);
  const [aircraftList, setAircraftList] = useState<Aircraft[]>([]);
  const [narrative, setNarrative] = useState('');
  const [reportContent, setReportContent] = useState('');
  const [generating, setGenerating] = useState(false);
  const [includeDownloadLink, setIncludeDownloadLink] = useState(false);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);
  const [portalUrl, setPortalUrl] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  const [portalSending, setPortalSending] = useState(false);
  const [portalSentTo, setPortalSentTo] = useState<string | null>(null);
  const [clientNotes, setClientNotes] = useState('');
  const [clientNotesDirty, setClientNotesDirty] = useState(false);
  // v2.66.0 frontend polish — Fix #1 (deposit indicator) + Fix #5 (Mark SENT)
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [markSentOpen, setMarkSentOpen] = useState(false);
  const [markingSent, setMarkingSent] = useState(false);
  const navigate = useNavigate();

  // Polling for Celery report generation
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!id) return;
    stopPolling();
    setGenerating(false);
    api.get(`/missions/${id}`).then((r) => {
      setMission(r.data);
      setClientNotes(r.data.client_notes || '');
    }).catch(() => navigate('/'));
    api.get(`/missions/${id}/report`).then((r) => {
      setReport(r.data);
      setNarrative(r.data.user_narrative || '');
      setReportContent(r.data.final_content || '');
      setIncludeDownloadLink(r.data.include_download_link || false);
    }).catch(() => {});
    api.get(`/missions/${id}/map?include_coverage=true`).then((r) => {
      setMapGeojson(r.data.geojson);
      setCoverage(r.data.coverage);
    }).catch(() => {});
    api.get('/aircraft').then((r) => setAircraftList(r.data)).catch(() => {});
    // v2.66.0 — invoice fetch for deposit-paid indicator (Fix #1).
    // 404 is expected for non-billable / pre-invoice missions; swallowed silently.
    api.get(`/missions/${id}/invoice`).then((r) => setInvoice(r.data)).catch(() => setInvoice(null));
    return () => {
      stopPolling();
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
    };
  }, [id, stopPolling]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!mission) return <Group justify="center" py="xl"><Loader color="cyan" /></Group>;

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const resp = await api.post(`/missions/${id}/report/generate`, { user_narrative: narrative, include_download_link: includeDownloadLink });
      const taskId = resp.data.task_id;
      if (!taskId) {
        // Synchronous fallback
        setReport(resp.data);
        setReportContent(resp.data.final_content || '');
        setGenerating(false);
        return;
      }

      notifications.show({ title: 'Generating Report', message: 'The AI is writing your report...', color: 'cyan' });

      pollIntervalRef.current = setInterval(async () => {
        try {
          const status = await api.get(`/missions/${id}/report/status/${taskId}`);
          if (status.data.status === 'complete') {
            stopPolling();
            try {
              const reportResp = await api.get(`/missions/${id}/report`);
              setReport(reportResp.data);
              setReportContent(reportResp.data.final_content || '');
              notifications.show({ title: 'Report Ready', message: 'Your AI report is ready for review', color: 'green' });
            } catch {
              notifications.show({ title: 'Report Generated', message: 'Report is ready — reload the page to view it', color: 'cyan' });
            }
            setGenerating(false);
          } else if (status.data.status === 'failed') {
            stopPolling();
            notifications.show({ title: 'Generation Failed', message: status.data.detail || 'Report generation failed', color: 'red' });
            setGenerating(false);
          }
        } catch {
          // Network blip during poll — keep trying
        }
      }, 3000);
    } catch {
      notifications.show({ title: 'Error', message: 'Generation failed', color: 'red' });
      setGenerating(false);
    }
  };

  const handleSaveReport = async () => {
    try {
      await api.put(`/missions/${id}/report`, {
        user_narrative: narrative || undefined,
        final_content: reportContent || undefined,
        include_download_link: includeDownloadLink,
      });
      notifications.show({ title: 'Saved', message: 'Report updated', color: 'cyan' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to save report', color: 'red' });
    }
  };

  const handleGeneratePDF = async () => {
    await handleSaveReport();
    try {
      const resp = await api.post(`/missions/${id}/report/pdf`, {}, { responseType: 'blob', timeout: 120000 });
      if (!resp.data || resp.data.size === 0) {
        notifications.show({ title: 'Error', message: 'PDF returned empty', color: 'red' });
        return;
      }
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
      const blobUrl = URL.createObjectURL(new Blob([resp.data], { type: 'application/pdf' }));
      setPdfBlobUrl(blobUrl);
      notifications.show({ title: 'PDF Generated', message: 'Preview loaded below', color: 'cyan' });
    } catch (err: unknown) {
      let message = 'PDF generation failed';
      try {
        const axiosErr = err as { response?: { data?: Blob | { detail?: string } } };
        const data = axiosErr.response?.data;
        if (data instanceof Blob) {
          const text = await data.text();
          const json = JSON.parse(text);
          if (json.detail) message = json.detail;
        } else if (data && typeof data === 'object' && 'detail' in data) {
          message = (data as { detail: string }).detail;
        }
      } catch { /* keep default message */ }
      notifications.show({ title: 'Error', message, color: 'red' });
    }
  };

  const handleSend = async () => {
    try {
      await api.post(`/missions/${id}/report/send`);
      notifications.show({ title: 'Sent', message: 'Report emailed to customer', color: 'green' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Send failed', color: 'red' });
    }
  };

  const handleGeneratePortalLink = async () => {
    setPortalLoading(true);
    try {
      const resp = await api.post(`/missions/${id}/client-link`, { expires_days: 30 });
      setPortalUrl(resp.data.portal_url);
      notifications.show({ title: 'Link Generated', message: 'Client portal link is ready', color: 'cyan' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to generate link', color: 'red' });
    } finally {
      setPortalLoading(false);
    }
  };

  const handleSendPortalEmail = async () => {
    setPortalSending(true);
    try {
      await api.post(`/missions/${id}/client-link/send`);
      setPortalSentTo(mission?.customer_id ? 'customer' : null);
      notifications.show({ title: 'Sent', message: 'Portal link emailed to customer', color: 'green' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to send email', color: 'red' });
    } finally {
      setPortalSending(false);
    }
  };

  // v2.66.0 — Fix #5: flip mission status COMPLETED → SENT.
  const handleMarkSent = async () => {
    setMarkingSent(true);
    try {
      const resp = await api.put(`/missions/${id}`, { status: 'sent' });
      setMission(resp.data);
      setMarkSentOpen(false);
      notifications.show({
        title: 'Mission marked SENT',
        message: 'Final state recorded. The customer portal will reflect this immediately.',
        color: 'cyan',
      });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({
        title: 'Error',
        message: axiosErr.response?.data?.detail || 'Failed to mark SENT',
        color: 'red',
      });
    } finally {
      setMarkingSent(false);
    }
  };

  const handleSaveClientNotes = async () => {
    try {
      await api.put(`/missions/${id}`, { client_notes: clientNotes || null });
      setClientNotesDirty(false);
      notifications.show({ title: 'Saved', message: 'Client notes updated', color: 'cyan' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to save notes', color: 'red' });
    }
  };

  // Find unique aircraft used in this mission's flights
  const usedAircraftIds = new Set(mission.flights.filter(f => f.aircraft_id).map(f => f.aircraft_id));
  const usedAircraft = aircraftList.filter(a => usedAircraftIds.has(a.id));

  const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

  return (
    <Stack gap="lg">
      <Group justify="space-between" wrap="wrap">
        <div>
          <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>{mission.title.toUpperCase()}</Title>
          <Group gap="xs" mt={4}>
            <Badge color={statusColors[mission.status]} variant="light">{mission.status}</Badge>
            <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              {mission.mission_type.replace(/_/g, ' ').toUpperCase()} | {mission.location_name || 'No location'} | {mission.mission_date || 'No date'}
            </Text>
          </Group>
        </div>
        <Group gap="xs">
          <Button
            leftSection={<IconEdit size={16} />}
            color="cyan"
            variant="light"
            onClick={() => navigate(`/missions/${id}/edit`)}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            EDIT MISSION
          </Button>
          {invoice && (
            <Button
              leftSection={<IconReceipt2 size={16} />}
              color="cyan"
              variant="outline"
              onClick={() => navigate(`/missions/${id}/invoice/edit`)}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              EDIT INVOICE
            </Button>
          )}
          {/* v2.66.0 Fix #5 — final-state flip COMPLETED → SENT (UI gap closed). */}
          {mission.status === 'completed' && (
            <Button
              leftSection={<IconFlagFilled size={16} />}
              color="teal"
              variant="filled"
              onClick={() => setMarkSentOpen(true)}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              MARK AS SENT
            </Button>
          )}
          <Button
            leftSection={<IconTrash size={16} />}
            color="red"
            variant="light"
            onClick={async () => {
              if (!window.confirm(`Delete "${mission.title}"? This cannot be undone.`)) return;
              try {
                await api.delete(`/missions/${id}`);
                notifications.show({ title: 'Deleted', message: 'Mission deleted', color: 'cyan' });
                navigate('/missions');
              } catch {
                notifications.show({ title: 'Error', message: 'Failed to delete', color: 'red' });
              }
            }}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            DELETE
          </Button>
        </Group>
      </Group>

      {/* v2.66.0 Fix #1 — deposit-paid indicator above invoice details. */}
      {invoice && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <Group justify="space-between" wrap="wrap" gap="md">
            <Stack gap={4}>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                INVOICE {invoice.invoice_number ?? '(no number)'}
              </Text>
              {invoice.deposit_required && invoice.deposit_paid && (
                <Badge
                  size="lg"
                  color="green"
                  variant="light"
                  leftSection={<IconCircleCheck size={16} />}
                  styles={{ root: { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', textTransform: 'none' } }}
                >
                  Deposit ${invoice.deposit_amount?.toFixed(2)} paid
                  {invoice.deposit_paid_at ? ` ${invoice.deposit_paid_at.slice(0, 10)}` : ''}
                </Badge>
              )}
              {invoice.deposit_required && !invoice.deposit_paid && (
                <Badge
                  size="lg"
                  color="yellow"
                  variant="light"
                  leftSection={<IconAlertTriangle size={16} />}
                  styles={{ root: { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', textTransform: 'none' } }}
                >
                  Deposit ${(invoice.deposit_amount ?? 0).toFixed(2)} DUE
                </Badge>
              )}
              {!invoice.deposit_required && (
                <Badge
                  size="lg"
                  color="gray"
                  variant="light"
                  leftSection={<IconCircleMinus size={16} />}
                  styles={{ root: { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', textTransform: 'none' } }}
                >
                  Deposit: not required (Emergent Services)
                </Badge>
              )}
            </Stack>
            <Stack gap={2} align="flex-end">
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                {invoice.paid_in_full ? 'PAID IN FULL' : 'BALANCE'}
              </Text>
              <Text c="#189cc6" size="xl" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                ${invoice.total.toFixed(2)}
              </Text>
            </Stack>
          </Group>
        </Card>
      )}

      {/* Aircraft */}
      {usedAircraft.length > 0 && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>AIRCRAFT DEPLOYED</Title>
          <Group wrap="wrap">{usedAircraft.map((a) => <AircraftCard key={a.id} aircraft={a} />)}</Group>
        </Card>
      )}

      {/* Flight Map */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>FLIGHT PATH MAP</Title>
        <FlightMap geojson={mapGeojson} coverage={coverage ?? undefined} height="min(400px, 60vw)" />
        {coverage && (
          <Group mt="sm" gap="xl" wrap="wrap">
            <div>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>AREA COVERED</Text>
              <Text c="#00d4ff" size="xl" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif" }}>
                {coverage.acres >= 1 ? `${coverage.acres.toFixed(2)} acres` : `${coverage.square_yards?.toFixed(0)} sq yd`}
              </Text>
            </div>
            <div>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>FLIGHTS</Text>
              <Text c="#00d4ff" size="xl" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif" }}>{coverage.num_flights}</Text>
            </div>
          </Group>
        )}
      </Card>

      {/* Download Link Status */}
      {mission.download_link_url && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <Group justify="space-between" align="center" wrap="wrap">
            <div>
              <Group gap="xs" mb={4} wrap="wrap">
                <IconLink size={16} color="#00d4ff" />
                <Title order={4} c="#e8edf2" style={{ letterSpacing: '1px' }}>MISSION FOOTAGE DOWNLOAD</Title>
              </Group>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", overflowWrap: 'break-word', wordBreak: 'break-all' }}>
                {mission.download_link_url}
              </Text>
            </div>
            <Badge
              color={mission.download_link_expires_at && new Date(mission.download_link_expires_at) > new Date() ? 'green' : 'red'}
              variant="light"
              size="lg"
            >
              {mission.download_link_expires_at && new Date(mission.download_link_expires_at) > new Date() ? 'LINK ACTIVE' : 'LINK EXPIRED'}
            </Badge>
          </Group>
          {mission.unas_folder_path && (
            <Text c="#5a6478" size="xs" mt="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              UNAS PATH: {mission.unas_folder_path}
            </Text>
          )}
          {mission.download_link_expires_at && (
            <Text c="#ff6b1a" size="xs" mt={4} style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              EXPIRES: {new Date(mission.download_link_expires_at).toLocaleString()}
            </Text>
          )}
        </Card>
      )}

      {/* Report Editor */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>OPERATIONS REPORT</Title>
        <Stack gap="md">
          <Textarea
            label="Operator Notes"
            value={narrative}
            onChange={(e) => setNarrative(e.target.value)}
            minRows={8}
            autosize
            styles={inputStyles}
          />
          <Switch
            label="Include download link in report"
            description={mission.download_link_url ? 'Client will see a download button for mission footage' : 'Set a download link URL when editing the mission first'}
            color="cyan"
            checked={includeDownloadLink}
            onChange={(e) => setIncludeDownloadLink(e.currentTarget.checked)}
            disabled={!mission.download_link_url}
          />
          <Button
            leftSection={generating ? <Loader size={14} color="white" /> : <IconRobot size={16} />}
            color="cyan"
            onClick={handleGenerate}
            disabled={generating || !narrative}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            {generating ? 'GENERATING...' : report ? 'REGENERATE REPORT' : 'GENERATE REPORT'}
          </Button>

          {reportContent && (
            <>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                REPORT CONTENT (EDITABLE)
              </Text>
              <RichTextEditor content={reportContent} onChange={setReportContent} minHeight="400px" />
            </>
          )}
          <Group>
            <Button
              leftSection={<IconDeviceFloppy size={16} />}
              color="cyan"
              variant="light"
              onClick={handleSaveReport}
              disabled={!narrative && !reportContent}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              SAVE REPORT
            </Button>
          </Group>
        </Stack>
      </Card>

      {/* Actions */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group wrap="wrap">
          <Button leftSection={<IconDownload size={16} />} color="cyan" onClick={handleGeneratePDF}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            GENERATE PDF
          </Button>
          <Button leftSection={<IconSend size={16} />} color="orange" variant="light" onClick={handleSend}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
            EMAIL TO CUSTOMER
          </Button>
        </Group>
      </Card>

      {/* Client Portal */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>CLIENT PORTAL</Title>
        {mission.customer_id ? (
          <Stack gap="md">
            <Group gap="sm" wrap="wrap">
              <Button
                leftSection={portalLoading ? <Loader size={14} color="white" /> : <IconExternalLink size={16} />}
                color="cyan"
                variant="light"
                onClick={handleGeneratePortalLink}
                loading={portalLoading}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                GENERATE CLIENT LINK
              </Button>
              <Button
                leftSection={portalSending ? <Loader size={14} color="white" /> : <IconMail size={16} />}
                color="orange"
                variant="light"
                onClick={handleSendPortalEmail}
                loading={portalSending}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                SEND TO CUSTOMER
              </Button>
            </Group>
            {portalUrl && (
              <TextInput
                label="Portal URL"
                value={portalUrl}
                readOnly
                styles={{
                  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px' },
                  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' },
                }}
                rightSection={
                  <CopyButton value={portalUrl}>
                    {({ copied, copy }) => (
                      <Tooltip label={copied ? 'Copied' : 'Copy link'}>
                        <ActionIcon color={copied ? 'green' : 'cyan'} variant="subtle" onClick={copy}>
                          {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </CopyButton>
                }
              />
            )}
            <Textarea
              label="Client Notes"
              description="Visible to the customer in their portal view"
              value={clientNotes}
              onChange={(e) => { setClientNotes(e.target.value); setClientNotesDirty(true); }}
              minRows={3}
              autosize
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' },
                description: { color: '#3d4557', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px' },
              }}
            />
            {clientNotesDirty && (
              <Button
                leftSection={<IconDeviceFloppy size={16} />}
                color="cyan"
                variant="light"
                size="xs"
                onClick={handleSaveClientNotes}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px', alignSelf: 'flex-start' } }}
              >
                SAVE NOTES
              </Button>
            )}
            <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              {portalSentTo ? 'Portal link sent to customer' : portalUrl ? 'Link generated — ready to share' : 'No link generated yet'}
            </Text>
          </Stack>
        ) : (
          <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            Assign a customer to enable the client portal
          </Text>
        )}
      </Card>

      {/* Inline PDF Preview */}
      {pdfBlobUrl && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
            PDF PREVIEW
          </Text>
          <PdfViewer url={pdfBlobUrl} height={700} downloadFilename={`Report_${mission.title.replace(/\s+/g, '_')}.pdf`} />
        </Card>
      )}

      {/* v2.66.0 Fix #5 — Mark SENT confirmation modal. */}
      <Modal
        opened={markSentOpen}
        onClose={() => !markingSent && setMarkSentOpen(false)}
        title="Mark this mission as SENT?"
        centered
        styles={{
          title: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', color: '#e8edf2' },
          content: { background: '#0e1117', border: '1px solid #1a1f2e' },
          header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' },
          body: { color: '#c0c8d4' },
        }}
      >
        <Stack gap="md">
          <Text c="#c0c8d4" size="sm">
            SENT is the final lifecycle state. Use it after the deliverables are
            transmitted to the customer. This does not undo by itself — you can
            still edit the mission record afterward.
          </Text>
          <Group justify="flex-end">
            <Button
              variant="subtle"
              color="gray"
              onClick={() => setMarkSentOpen(false)}
              disabled={markingSent}
            >
              Cancel
            </Button>
            <Button
              color="teal"
              onClick={handleMarkSent}
              loading={markingSent}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              CONFIRM — MARK SENT
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
