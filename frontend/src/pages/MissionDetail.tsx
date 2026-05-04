/**
 * MissionDetail — Mission Hub (v2.67.0).
 *
 * Spec: docs/superpowers/specs/2026-05-03-mission-hub-redesign-design.md
 *
 * The Hub is a READ + NAVIGATE view. It never writes mission data
 * (other than status transitions via PATCH). Each facet (Details,
 * Flights, Images, Report, Invoice) renders as a `MissionFacetCard`
 * with a summary + Edit button that routes to a focused per-facet
 * editor. This makes the duplicate-mission class physically impossible
 * — only the slim `MissionCreateModal` ever POSTs to `/api/missions`.
 *
 * Lifecycle controls live in the header row:
 *   * `Mark COMPLETED` — visible while status < COMPLETED
 *   * `Mark SENT`      — visible while status === COMPLETED (locks the mission)
 *   * `Reopen Mission` — visible while status === SENT (per spec §8.5)
 *   * `DELETE`         — preserved from prior MissionDetail
 *
 * Lockdown semantics (spec §8.5): when status === SENT, every facet's
 * Edit button is disabled with the tooltip "Mission sent — locked".
 *
 * Hub Invoice card additions (spec §8.6): the deposit indicator stays
 * inline; Issue Portal Link / Send Email / Copy Link sit alongside the
 * Edit button as `extraActions` so the canonical operator workflow is
 * a single click from the Hub.
 */
import { useEffect, useState, useCallback } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  CopyButton,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconCircleCheck,
  IconCircleMinus,
  IconCopy,
  IconExternalLink,
  IconFlagFilled,
  IconHistory,
  IconLink,
  IconMail,
  IconRefresh,
  IconTrash,
} from '@tabler/icons-react';
import { useParams, useNavigate, Link } from 'react-router-dom';

import api from '../api/client';
import type { Customer, Invoice, Mission, Report } from '../api/types';
import MissionFacetCard from '../components/MissionFacetCard';
import MissionStatusBadge from '../components/MissionStatusBadge';

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

export default function MissionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [mission, setMission] = useState<Mission | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [customer, setCustomer] = useState<Customer | null>(null);

  const [transitioning, setTransitioning] = useState(false);
  const [markCompletedOpen, setMarkCompletedOpen] = useState(false);
  const [markSentOpen, setMarkSentOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);

  const [portalUrl, setPortalUrl] = useState<string | null>(null);
  const [portalSentAt, setPortalSentAt] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  const [portalSending, setPortalSending] = useState(false);
  const [portalModalOpen, setPortalModalOpen] = useState(false);

  const reload = useCallback(async () => {
    if (!id) return;
    const m = await api.get(`/missions/${id}`).catch(() => null);
    if (!m) {
      navigate('/missions');
      return;
    }
    setMission(m.data);
    if (m.data.customer_id) {
      api
        .get(`/customers/${m.data.customer_id}`)
        .then((r) => setCustomer(r.data))
        .catch(() => setCustomer(null));
    } else {
      setCustomer(null);
    }
    api
      .get(`/missions/${id}/report`)
      .then((r) => setReport(r.data))
      .catch(() => setReport(null));
    api
      .get(`/missions/${id}/invoice`)
      .then((r) => setInvoice(r.data))
      .catch(() => setInvoice(null));
  }, [id, navigate]);

  useEffect(() => {
    reload();
  }, [reload]);

  // v2.67.1 — Hub auto-refresh.
  // Until status flips to SENT, poll the mission every 30s so the
  // operator sees deposit-paid / balance-paid updates without a
  // manual refresh. Pauses when the tab is hidden (no battery drain
  // when the operator's on another tab) and stops entirely once
  // status === SENT (no further state changes expected on a
  // sent mission until Reopen is clicked, which fires its own reload).
  useEffect(() => {
    if (!mission || mission.status === 'sent') return;
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') return;
      reload().catch(() => {/* swallow — we'll retry next tick */});
    };
    const id = window.setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [mission?.status, reload]);

  if (!mission) {
    return (
      <Group justify="center" py="xl">
        <Loader color="cyan" />
      </Group>
    );
  }

  const isSent = mission.status === 'sent';
  const editsDisabled = isSent;

  // ── Status transitions ─────────────────────────────────────────────
  const patchStatus = async (newStatus: string, opts?: { reopen?: boolean }) => {
    setTransitioning(true);
    try {
      const url = opts?.reopen ? `/missions/${id}?reopen=true` : `/missions/${id}`;
      const resp = await api.patch(url, { status: newStatus });
      setMission(resp.data);
      notifications.show({
        title: 'Status updated',
        message: `Mission is now ${newStatus.toUpperCase()}`,
        color: 'cyan',
      });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({
        title: 'Error',
        message: axiosErr.response?.data?.detail || 'Status update failed',
        color: 'red',
      });
    } finally {
      setTransitioning(false);
    }
  };

  const handleMarkCompleted = async () => {
    await patchStatus('completed');
    setMarkCompletedOpen(false);
  };
  const handleMarkSent = async () => {
    await patchStatus('sent');
    setMarkSentOpen(false);
  };
  const handleReopen = async () => {
    await patchStatus('completed', { reopen: true });
    setReopenOpen(false);
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete "${mission.title}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/missions/${id}`);
      notifications.show({ title: 'Deleted', message: 'Mission deleted', color: 'cyan' });
      navigate('/missions');
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete', color: 'red' });
    }
  };

  // ── Portal-link actions (spec §8.6) ────────────────────────────────
  const handleIssuePortalLink = async () => {
    setPortalLoading(true);
    try {
      // Idempotent per ADR-0011 — repeated clicks return the same valid token.
      const resp = await api.post(`/missions/${id}/client-link`, { expires_days: 30 });
      setPortalUrl(resp.data.portal_url);
      setPortalModalOpen(true);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({
        title: 'Error',
        message: axiosErr.response?.data?.detail || 'Failed to issue portal link',
        color: 'red',
      });
    } finally {
      setPortalLoading(false);
    }
  };

  const handleSendPortalEmail = async () => {
    if (!customer?.email) {
      notifications.show({
        title: 'Customer email missing',
        message: 'Add an email to the customer record first',
        color: 'orange',
      });
      return;
    }
    setPortalSending(true);
    try {
      await api.post(`/missions/${id}/client-link/send`);
      setPortalSentAt(new Date().toISOString());
      notifications.show({
        title: 'Portal link emailed',
        message: `Sent to ${customer.email}`,
        color: 'green',
      });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({
        title: 'Error',
        message: axiosErr.response?.data?.detail || 'Failed to email portal link',
        color: 'red',
      });
    } finally {
      setPortalSending(false);
    }
  };

  // ── Facet summaries ────────────────────────────────────────────────
  const detailsSummary = (
    <Stack gap={2}>
      <Text c="#e8edf2" size="sm" fw={600}>{mission.title}</Text>
      <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
        {mission.mission_type.replace(/_/g, ' ').toUpperCase()}
        {mission.location_name ? ` · ${mission.location_name}` : ''}
        {mission.mission_date ? ` · ${mission.mission_date}` : ''}
      </Text>
      {customer && (
        <Group gap="xs" wrap="wrap">
          <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            Customer: {customer.name}
          </Text>
          {customer.tos_signed && (
            <Badge color="green" size="xs" variant="light">TOS SIGNED</Badge>
          )}
        </Group>
      )}
    </Stack>
  );

  const totalFlightSeconds = mission.flights.reduce((acc, f) => {
    const cache = f.flight_data_cache as Record<string, unknown> | null;
    const dur = cache && typeof cache.duration_secs === 'number' ? cache.duration_secs : 0;
    return acc + dur;
  }, 0);
  const flightsSummary = (
    <Text c="#c0c8d4" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
      {mission.flights.length === 0
        ? 'No flights yet'
        : `${mission.flights.length} flight${mission.flights.length === 1 ? '' : 's'} · ${Math.round(totalFlightSeconds / 60)} total minutes`}
    </Text>
  );

  const imagesSummary = (
    <Text c="#c0c8d4" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
      {mission.images.length === 0
        ? 'No images yet'
        : `${mission.images.length} image${mission.images.length === 1 ? '' : 's'} uploaded`}
    </Text>
  );

  const reportStatus = report
    ? report.final_content
      ? 'Drafting'
      : 'Started (no content)'
    : 'Not started';
  const reportSnippet = report?.final_content
    ? `${report.final_content.replace(/<[^>]*>/g, '').slice(0, 80)}…`
    : '—';
  const reportSummary = (
    <Stack gap={2}>
      <Text c="#c0c8d4" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
        {reportStatus}
      </Text>
      <Text c="#5a6478" size="xs">{reportSnippet}</Text>
    </Stack>
  );

  const invoiceSummary = (
    <Stack gap={4}>
      {invoice ? (
        <>
          <Group gap="xs" wrap="wrap">
            {invoice.deposit_required && invoice.deposit_paid && (
              <Badge color="green" variant="light" size="sm" leftSection={<IconCircleCheck size={12} />}>
                Deposit paid
              </Badge>
            )}
            {invoice.deposit_required && !invoice.deposit_paid && (
              <Badge color="yellow" variant="light" size="sm" leftSection={<IconAlertTriangle size={12} />}>
                Deposit due
              </Badge>
            )}
            {!invoice.deposit_required && (
              <Badge color="gray" variant="light" size="sm" leftSection={<IconCircleMinus size={12} />}>
                No deposit
              </Badge>
            )}
            {invoice.paid_in_full && (
              <Badge color="teal" variant="light" size="sm">PAID IN FULL</Badge>
            )}
          </Group>
          <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            {invoice.invoice_number ?? '(no invoice number)'}
          </Text>
          <Text c="#c0c8d4" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            Total: ${invoice.total.toFixed(2)}
            {invoice.deposit_required ? ` · Deposit: $${(invoice.deposit_amount ?? 0).toFixed(2)}` : ''}
            {invoice.deposit_required && !invoice.paid_in_full
              ? ` · Balance: $${(invoice.total - (invoice.deposit_paid ? invoice.deposit_amount ?? 0 : 0)).toFixed(2)}`
              : ''}
          </Text>
          <Text c="#5a6478" size="xs">
            {portalSentAt
              ? `Last portal link sent ${portalSentAt.slice(0, 19).replace('T', ' ')}`
              : 'No portal link issued yet.'}
          </Text>
        </>
      ) : (
        <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          No invoice yet — create one in the Invoice editor.
        </Text>
      )}
    </Stack>
  );

  const portalActions = (
    <Group gap="xs" wrap="nowrap">
      <Tooltip label="Issue or refresh client-portal magic link" withArrow>
        <Button
          size="sm"
          color="cyan"
          variant="filled"
          leftSection={portalLoading ? <Loader size={12} color="white" /> : <IconExternalLink size={14} />}
          onClick={handleIssuePortalLink}
          loading={portalLoading}
          disabled={!mission.customer_id || isSent}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          ISSUE LINK
        </Button>
      </Tooltip>
      <Tooltip label={customer?.email ? `Send to ${customer.email}` : 'Customer has no email'} withArrow>
        <Button
          size="sm"
          color="orange"
          variant="light"
          leftSection={portalSending ? <Loader size={12} color="white" /> : <IconMail size={14} />}
          onClick={handleSendPortalEmail}
          loading={portalSending}
          disabled={!customer?.email || isSent}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          EMAIL
        </Button>
      </Tooltip>
    </Group>
  );

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <Stack gap="lg" data-testid="mission-hub">
      {/* Header row: title + status badge + lifecycle controls */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" wrap="wrap" align="flex-start">
          <Stack gap={6}>
            <Text c="#e8edf2" fw={700} size="xl" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}>
              {mission.title.toUpperCase()}
            </Text>
            <Group gap="xs">
              <MissionStatusBadge status={mission.status} />
              {customer && (
                <Tooltip label="View TOS audit trail" withArrow>
                  <Link
                    to={`/tos-acceptances?customer_id=${customer.id}`}
                    style={{ display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}
                  >
                    <Badge
                      color="cyan"
                      variant="outline"
                      size="sm"
                      leftSection={<IconHistory size={12} />}
                      style={{ cursor: 'pointer' }}
                    >
                      TOS HISTORY
                    </Badge>
                  </Link>
                </Tooltip>
              )}
            </Group>
          </Stack>
          <Group gap="xs" wrap="wrap">
            {/* v2.67.1 — manual Refresh button. Hub auto-polls every 30s
                while tab is visible + status < SENT, but operator can
                force-refresh anytime (e.g., they just heard the ntfy
                push and want to see the deposit-paid update NOW). */}
            <Tooltip label="Refresh" withArrow>
              <ActionIcon
                color="cyan"
                variant="subtle"
                size="lg"
                aria-label="Refresh mission"
                onClick={() => reload()}
              >
                <IconRefresh size={18} />
              </ActionIcon>
            </Tooltip>
            {/* Mark COMPLETED — visible while status < COMPLETED, hidden when SENT (per §8.5). */}
            {!isSent &&
              mission.status !== 'completed' &&
              mission.status !== 'sent' && (
                <Button
                  leftSection={<IconCircleCheck size={16} />}
                  color="green"
                  variant="filled"
                  loading={transitioning}
                  onClick={() => setMarkCompletedOpen(true)}
                  data-testid="mark-completed-btn"
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  MARK COMPLETED
                </Button>
              )}
            {/* Mark SENT — visible only when COMPLETED (per §8.5). */}
            {mission.status === 'completed' && (
              <Button
                leftSection={<IconFlagFilled size={16} />}
                color="teal"
                variant="filled"
                loading={transitioning}
                onClick={() => setMarkSentOpen(true)}
                data-testid="mark-sent-btn"
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                MARK SENT
              </Button>
            )}
            {/* Reopen — visible only when SENT (per §8.5). */}
            {isSent && (
              <Button
                leftSection={<IconArrowBackUp size={16} />}
                color="red"
                variant="outline"
                loading={transitioning}
                onClick={() => setReopenOpen(true)}
                data-testid="reopen-btn"
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                REOPEN MISSION
              </Button>
            )}
            <Button
              leftSection={<IconTrash size={16} />}
              color="red"
              variant="light"
              onClick={handleDelete}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              DELETE
            </Button>
          </Group>
        </Group>
      </Card>

      {/* 5 facet cards (per spec §2) */}
      <MissionFacetCard
        title="Details"
        summary={detailsSummary}
        editPath={`/missions/${id}/details/edit`}
        disabled={editsDisabled}
      />
      <MissionFacetCard
        title="Flights"
        summary={flightsSummary}
        editPath={`/missions/${id}/flights/edit`}
        disabled={editsDisabled}
      />
      <MissionFacetCard
        title="Images"
        summary={imagesSummary}
        editPath={`/missions/${id}/images/edit`}
        disabled={editsDisabled}
      />
      <MissionFacetCard
        title="Report"
        summary={reportSummary}
        editPath={`/missions/${id}/report/edit`}
        disabled={editsDisabled}
      />
      <MissionFacetCard
        title="Invoice"
        summary={invoiceSummary}
        editPath={`/missions/${id}/invoice/edit`}
        disabled={editsDisabled}
        extraActions={portalActions}
      />

      {/* Lockdown banner when SENT (per §8.5) */}
      {isSent && (
        <Card padding="md" radius="md" style={{ background: '#0e1117', border: '1px dashed #1a1f2e' }}>
          <Group gap="xs" wrap="wrap">
            <IconAlertTriangle size={16} color="#5a6478" />
            <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Mission marked SENT — record is locked. Use Reopen Mission to correct billing or replace a delivered artifact.
            </Text>
          </Group>
        </Card>
      )}

      {/* Mark COMPLETED confirmation */}
      <Modal
        opened={markCompletedOpen}
        onClose={() => !transitioning && setMarkCompletedOpen(false)}
        title="Mark this mission COMPLETED?"
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
            COMPLETED means the operational work is done. The customer's portal flips to "Balance Due" and Mark SENT becomes available.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setMarkCompletedOpen(false)} disabled={transitioning}>
              Cancel
            </Button>
            <Button color="green" onClick={handleMarkCompleted} loading={transitioning}>
              CONFIRM
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Mark SENT confirmation */}
      <Modal
        opened={markSentOpen}
        onClose={() => !transitioning && setMarkSentOpen(false)}
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
            SENT is the lock-down state. After this, every Edit button is disabled. Use Reopen Mission only to correct a billing error or replace a delivered artifact.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setMarkSentOpen(false)} disabled={transitioning}>
              Cancel
            </Button>
            <Button color="teal" onClick={handleMarkSent} loading={transitioning}>
              CONFIRM — MARK SENT
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Reopen confirmation (audit-logged via [MISSION-REOPEN] WARN) */}
      <Modal
        opened={reopenOpen}
        onClose={() => !transitioning && setReopenOpen(false)}
        title="Reopen this mission?"
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
            Reopening reverts a SENT mission back to COMPLETED so you can edit billing or replace a delivered artifact. This action is logged in the audit trail.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setReopenOpen(false)} disabled={transitioning}>
              Cancel
            </Button>
            <Button color="red" onClick={handleReopen} loading={transitioning}>
              REOPEN
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Portal link modal (per spec §8.6) */}
      <Modal
        opened={portalModalOpen}
        onClose={() => setPortalModalOpen(false)}
        title="Client Portal Link"
        centered
        size="lg"
        styles={{
          title: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', color: '#e8edf2' },
          content: { background: '#0e1117', border: '1px solid #1a1f2e' },
          header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' },
          body: { color: '#c0c8d4' },
        }}
      >
        <Stack gap="md">
          {portalUrl ? (
            <>
              <Text c="#c0c8d4" size="sm">
                Magic link valid for 30 days. Idempotent — clicking Issue Link again returns the same token (per ADR-0011).
              </Text>
              <Card padding="sm" radius="sm" style={{ background: '#050608', border: '1px solid #1a1f2e' }}>
                <Text c="#e8edf2" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", wordBreak: 'break-all' }}>
                  {portalUrl}
                </Text>
              </Card>
              <Group>
                <CopyButton value={portalUrl}>
                  {({ copied, copy }) => (
                    <Button
                      leftSection={<IconCopy size={14} />}
                      color={copied ? 'green' : 'cyan'}
                      variant="light"
                      onClick={copy}
                      styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                    >
                      {copied ? 'COPIED' : 'COPY LINK'}
                    </Button>
                  )}
                </CopyButton>
                <Button
                  leftSection={<IconMail size={14} />}
                  color="orange"
                  variant="light"
                  onClick={handleSendPortalEmail}
                  loading={portalSending}
                  disabled={!customer?.email}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  EMAIL TO {customer?.email ?? 'customer'}
                </Button>
                <Button
                  variant="subtle"
                  color="gray"
                  leftSection={<IconLink size={14} />}
                  onClick={handleIssuePortalLink}
                >
                  Refresh
                </Button>
              </Group>
            </>
          ) : (
            <Text c="#5a6478" size="sm">No portal link issued yet.</Text>
          )}
        </Stack>
      </Modal>
    </Stack>
  );
}
