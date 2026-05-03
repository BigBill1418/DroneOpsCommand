/**
 * Client mission detail — single mission view with deliverables,
 * mission-progress stepper, and the ADR-0009 two-phase invoice
 * (deposit + balance) payment table with post-Stripe-redirect
 * polling.
 *
 * Customer-facing — wrapped in <CustomerLayout> with the BarnardHQ
 * brand pass (v2.65.0). All payment functionality preserved verbatim
 * from agent A's deposit-feature commit `b8ead48`; this pass only
 * lifts the visual layer.
 */
import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  Center,
  Divider,
  Group,
  Loader,
  Paper,
  Stack,
  Stepper,
  Table,
  Text,
  Title,
} from '@mantine/core';
import {
  IconArrowLeft,
  IconCalendar,
  IconCheck,
  IconDrone,
  IconFileText,
  IconMapPin,
  IconPackage,
  IconReceipt,
} from '@tabler/icons-react';
import { useClientAuth } from '../../hooks/useClientAuth';
import clientApi from '../../api/clientPortalApi';
import CustomerLayout from '../../components/CustomerLayout';
import { customerBrand, customerStyles } from '../../lib/customerTheme';
import { customerNotify } from '../../lib/customerNotify';

interface ClientMissionData {
  id: string;
  title: string;
  mission_type: string;
  description: string | null;
  mission_date: string | null;
  location_name: string | null;
  status: string;
  client_notes: string | null;
  created_at: string;
  image_count: number;
}

interface ClientInvoiceLineItem {
  description: string;
  quantity: number;
  unit_price: number;
  total: number;
}

interface ClientInvoiceData {
  id: string;
  total: number;
  paid_in_full: boolean;
  paid_at: string | null;
  payment_method: string | null;
  line_items: ClientInvoiceLineItem[];
  // ADR-0009 deposit fields.
  deposit_required: boolean;
  deposit_amount: number;
  deposit_paid: boolean;
  deposit_paid_at: string | null;
  deposit_payment_method: string | null;
  balance_amount: number;
  payment_phase: 'deposit_due' | 'awaiting_completion' | 'balance_due' | 'paid_in_full';
}

const PAYMENT_PHASE_LABELS: Record<ClientInvoiceData['payment_phase'], string> = {
  deposit_due: 'Deposit Due',
  awaiting_completion: 'Awaiting Mission Completion',
  balance_due: 'Balance Due',
  paid_in_full: 'Paid in Full',
};

const PAYMENT_PHASE_ORDER: ClientInvoiceData['payment_phase'][] = [
  'deposit_due',
  'awaiting_completion',
  'balance_due',
  'paid_in_full',
];

function fmtMoney(n: number): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

const STATUS_PIPELINE = [
  { key: 'scheduled', label: 'Scheduled', icon: IconCalendar },
  { key: 'in_progress', label: 'In Progress', icon: IconDrone },
  { key: 'processing', label: 'Processing', icon: IconFileText },
  { key: 'review', label: 'Review', icon: IconCheck },
  { key: 'delivered', label: 'Delivered', icon: IconPackage },
];

const statusColor: Record<string, string> = {
  draft: 'gray',
  scheduled: 'blue',
  in_progress: 'yellow',
  processing: 'orange',
  review: 'cyan',
  delivered: 'green',
  completed: 'green',
  sent: 'teal',
};

const statusLabel: Record<string, string> = {
  draft: 'DRAFT',
  scheduled: 'SCHEDULED',
  in_progress: 'IN PROGRESS',
  processing: 'PROCESSING',
  review: 'READY FOR REVIEW',
  delivered: 'DELIVERED',
  completed: 'COMPLETED',
  sent: 'SENT',
};

const typeLabel: Record<string, string> = {
  sar: 'Search & Rescue',
  videography: 'Videography',
  lost_pet: 'Lost Pet',
  inspection: 'Inspection',
  mapping: 'Mapping',
  photography: 'Photography',
  survey: 'Survey',
  security_investigations: 'Security',
  other: 'Other',
};

function getStepperActive(status: string): number {
  const idx = STATUS_PIPELINE.findIndex((s) => s.key === status);
  if (idx >= 0) return idx;
  if (status === 'completed' || status === 'sent') return STATUS_PIPELINE.length;
  return -1;
}

const monoFont = { fontFamily: customerBrand.fontMono };
const displayFont = {
  fontFamily: customerBrand.fontDisplay,
  letterSpacing: customerBrand.trackMid,
  color: customerBrand.textPrimary,
};

const cardTitleStyle = {
  ...displayFont,
  fontSize: 20,
} as const;

export default function ClientMissionDetail() {
  const { missionId } = useParams<{ missionId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const auth = useClientAuth();
  const [mission, setMission] = useState<ClientMissionData | null>(null);
  const [invoice, setInvoice] = useState<ClientInvoiceData | null>(null);
  const [invoiceError, setInvoiceError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [paying, setPaying] = useState<null | 'deposit' | 'balance'>(null);
  const lastPhaseRef = useRef<ClientInvoiceData['payment_phase'] | null>(null);

  // Pull invoice — separate from mission so polling can re-pull just this.
  const fetchInvoice = async (): Promise<ClientInvoiceData | null> => {
    try {
      const resp = await clientApi.get(`/missions/${missionId}/invoice`);
      const data: ClientInvoiceData | null = resp.data || null;
      setInvoice(data);
      setInvoiceError(null);
      return data;
    } catch (err: any) {
      console.error('[ClientMissionDetail] Invoice load failed:', err);
      setInvoiceError(err?.response?.data?.detail || 'Failed to load invoice');
      return null;
    }
  };

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;
    (async () => {
      try {
        const missionResp = await clientApi.get(`/missions/${missionId}`);
        if (cancelled) return;
        setMission(missionResp.data);
        const inv = await fetchInvoice();
        if (cancelled) return;
        lastPhaseRef.current = inv?.payment_phase ?? null;
      } catch (err: any) {
        console.error('[ClientMissionDetail] Failed to load:', err);
        if (err.response?.status === 403) {
          customerNotify({
            title: 'Access Denied',
            message: 'You do not have access to this mission.',
            kind: 'danger',
          });
        } else {
          customerNotify({
            title: 'Error',
            message: 'Failed to load mission details.',
            kind: 'danger',
          });
        }
        navigate(-1);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missionId, navigate]);

  // ADR-0009 — post-Stripe-redirect polling. After ?payment=success
  // we hit the invoice every 3s for up to 30s, stopping early when
  // payment_phase changes. No SSE/WS — polling is sufficient at
  // human-payment cadence and avoids a stateful CF Access connection.
  useEffect(() => {
    if (!missionId) return;
    const paymentParam = searchParams.get('payment');
    if (paymentParam === 'cancel') {
      customerNotify({
        title: 'Payment Canceled',
        message: 'Your payment was canceled. You can retry whenever you are ready.',
        kind: 'warning',
      });
      searchParams.delete('payment');
      setSearchParams(searchParams, { replace: true });
      return;
    }
    if (paymentParam !== 'success') return;
    const startedPhase = lastPhaseRef.current;
    let stopped = false;
    let attempts = 0;
    const maxAttempts = 10; // 10 * 3s = 30s
    const tick = async () => {
      if (stopped) return;
      attempts += 1;
      const inv = await fetchInvoice();
      const newPhase = inv?.payment_phase ?? null;
      if (newPhase && newPhase !== startedPhase) {
        customerNotify({
          title: 'Payment Confirmed',
          message: `Status updated to: ${PAYMENT_PHASE_LABELS[newPhase]}`,
          kind: 'success',
        });
        lastPhaseRef.current = newPhase;
        searchParams.delete('payment');
        setSearchParams(searchParams, { replace: true });
        stopped = true;
        return;
      }
      if (attempts >= maxAttempts) {
        stopped = true;
        customerNotify({
          title: 'Still Processing',
          message:
            'Your payment is taking a moment to confirm. Refresh in a few seconds if the status does not update.',
          kind: 'warning',
        });
        return;
      }
      setTimeout(tick, 3000);
    };
    setTimeout(tick, 3000);
    return () => {
      stopped = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missionId, searchParams.get('payment')]);

  const handlePay = async (phase: 'deposit' | 'balance') => {
    if (!missionId) return;
    setPaying(phase);
    try {
      const resp = await clientApi.post(`/missions/${missionId}/invoice/pay/${phase}`);
      const url = resp.data?.checkout_url;
      if (url) {
        window.location.assign(url);
      } else {
        throw new Error('No checkout URL returned');
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to start payment';
      customerNotify({
        title: 'Payment Error',
        message: detail,
        kind: 'danger',
      });
      console.error('[ClientMissionDetail] Pay failed:', err);
    } finally {
      setPaying(null);
    }
  };

  if (auth.loading || loading) {
    return (
      <CustomerLayout>
        <Center py="xl" style={{ minHeight: '40vh' }}>
          <Loader color="cyan" size="lg" />
        </Center>
      </CustomerLayout>
    );
  }

  if (!auth.isAuthenticated) {
    navigate('/client/login');
    return null;
  }

  if (!mission) {
    return (
      <CustomerLayout>
        <Center py="xl" style={{ minHeight: '40vh' }}>
          <Text style={{ color: customerBrand.textMuted, fontFamily: customerBrand.fontMono }}>
            Mission not found.
          </Text>
        </Center>
      </CustomerLayout>
    );
  }

  const stepperActive = getStepperActive(mission.status);
  const showStepper = mission.status !== 'draft' && mission.status !== 'sent';
  const headerContext = (
    <span style={{ textTransform: 'uppercase' }}>
      Mission {mission.id.slice(0, 8)}
    </span>
  );

  // Reusable styles for the inner data tables.
  const tableStyles = {
    table: { background: 'transparent' },
    th: {
      color: customerBrand.textMuted,
      fontFamily: customerBrand.fontMono,
      fontSize: 11,
      letterSpacing: customerBrand.trackTight,
      borderColor: customerBrand.border,
    },
    td: {
      color: customerBrand.textPrimary,
      borderColor: customerBrand.border,
      fontFamily: customerBrand.fontBody,
    },
  };

  return (
    <CustomerLayout maxWidth={780} contextSlot={headerContext}>
      <Button
        variant="subtle"
        size="xs"
        leftSection={<IconArrowLeft size={14} />}
        onClick={() => navigate(-1)}
        styles={{
          root: {
            color: customerBrand.textMuted,
            background: 'transparent',
            fontFamily: customerBrand.fontMono,
            letterSpacing: customerBrand.trackTight,
            alignSelf: 'flex-start',
          },
        }}
      >
        BACK TO MISSIONS
      </Button>

      {/* ── Mission summary card ─────────────────────────────── */}
      <Card padding="lg" radius="md" style={customerStyles.card}>
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <div style={{ flex: 1, minWidth: 240 }}>
            <Title
              order={2}
              style={{
                ...displayFont,
                fontSize: 'clamp(24px, 4vw, 32px)',
              }}
            >
              {mission.title.toUpperCase()}
            </Title>
            <Group gap="xs" mt={6} wrap="wrap">
              <Badge
                color={statusColor[mission.status] || 'gray'}
                variant="light"
                size="lg"
                style={monoFont}
              >
                {statusLabel[mission.status] || mission.status.toUpperCase()}
              </Badge>
              <Text
                size="sm"
                style={{ color: customerBrand.textMuted, ...monoFont }}
              >
                {typeLabel[mission.mission_type] || mission.mission_type}
              </Text>
            </Group>
          </div>
        </Group>

        <Divider my="md" color={customerBrand.border} />

        <Group gap="xl" wrap="wrap">
          {mission.mission_date && (
            <Group gap={6}>
              <IconCalendar size={14} color={customerBrand.brandCyan} />
              <Text
                size="sm"
                style={{ color: customerBrand.textPrimary, ...monoFont }}
              >
                {mission.mission_date}
              </Text>
            </Group>
          )}
          {mission.location_name && (
            <Group gap={6}>
              <IconMapPin size={14} color={customerBrand.brandCyan} />
              <Text
                size="sm"
                style={{ color: customerBrand.textPrimary, ...monoFont }}
              >
                {mission.location_name}
              </Text>
            </Group>
          )}
        </Group>

        {mission.description && (
          <>
            <Divider my="md" color={customerBrand.border} />
            <Text
              size="sm"
              style={{
                color: customerBrand.textBody,
                fontFamily: customerBrand.fontBody,
                lineHeight: 1.7,
              }}
            >
              {mission.description}
            </Text>
          </>
        )}
      </Card>

      {/* ── Mission progress stepper ─────────────────────────── */}
      {showStepper && (
        <Card padding="lg" radius="md" style={customerStyles.card}>
          <Title order={4} mb="md" style={cardTitleStyle}>
            MISSION PROGRESS
          </Title>
          <Stepper
            active={stepperActive}
            color="cyan"
            size="sm"
            styles={{
              root: { padding: '0 4px' },
              step: { minWidth: 0 },
              stepIcon: {
                background: customerBrand.bgCard,
                borderColor: customerBrand.border,
              },
              stepLabel: {
                color: customerBrand.textPrimary,
                fontFamily: customerBrand.fontBody,
                fontSize: 13,
              },
              stepDescription: {
                color: customerBrand.textMuted,
                fontSize: 11,
                fontFamily: customerBrand.fontMono,
              },
              separator: { borderColor: customerBrand.border },
            }}
          >
            {STATUS_PIPELINE.map((step) => (
              <Stepper.Step
                key={step.key}
                label={step.label}
                icon={<step.icon size={16} />}
                completedIcon={<IconCheck size={16} />}
              />
            ))}
            <Stepper.Completed>
              <Center py="sm">
                <Badge color="green" variant="light" size="lg" style={monoFont}>
                  MISSION COMPLETE
                </Badge>
              </Center>
            </Stepper.Completed>
          </Stepper>
        </Card>
      )}

      {/* ── Operator notes ───────────────────────────────────── */}
      {mission.client_notes && (
        <Card padding="lg" radius="md" style={customerStyles.card}>
          <Title order={4} mb="sm" style={cardTitleStyle}>
            OPERATOR NOTES
          </Title>
          <Text
            size="sm"
            style={{
              color: customerBrand.textBody,
              fontFamily: customerBrand.fontBody,
              lineHeight: 1.7,
              whiteSpace: 'pre-wrap',
            }}
          >
            {mission.client_notes}
          </Text>
        </Card>
      )}

      {/* ── Deliverables placeholder ─────────────────────────── */}
      <Card padding="lg" radius="md" style={customerStyles.card}>
        <Group gap="xs" mb="sm">
          <IconPackage size={18} color={customerBrand.brandCyan} />
          <Title order={4} style={cardTitleStyle}>
            DELIVERABLES
          </Title>
        </Group>
        <Paper
          p="md"
          radius="sm"
          style={{
            background: customerBrand.bgDeep,
            border: `1px dashed ${customerBrand.border}`,
          }}
        >
          <Text
            size="sm"
            ta="center"
            style={{ color: customerBrand.textMuted, ...monoFont }}
          >
            Deliverables will be available here once your mission is complete.
          </Text>
        </Paper>
      </Card>

      {/* ── Invoice card (deposit feature ADR-0009) ──────────── */}
      <Card padding="lg" radius="md" style={customerStyles.card}>
        <Group gap="xs" mb="sm">
          <IconReceipt size={18} color={customerBrand.brandCyan} />
          <Title order={4} style={cardTitleStyle}>
            INVOICE
          </Title>
        </Group>

        {invoiceError ? (
          <Paper
            p="md"
            radius="sm"
            style={{
              background: customerBrand.bgDeep,
              border: `1px solid ${customerBrand.danger}`,
            }}
          >
            <Text
              size="sm"
              ta="center"
              style={{ color: customerBrand.danger, ...monoFont }}
            >
              {invoiceError}
            </Text>
          </Paper>
        ) : !invoice ? (
          <Paper
            p="md"
            radius="sm"
            style={{
              background: customerBrand.bgDeep,
              border: `1px dashed ${customerBrand.border}`,
            }}
          >
            <Text
              size="sm"
              ta="center"
              style={{ color: customerBrand.textMuted, ...monoFont }}
            >
              Invoice details will appear here when billing is ready.
            </Text>
          </Paper>
        ) : (
          <Stack gap="md">
            {/* ADR-0009 §3.6 — 4-step payment-phase progress strip. */}
            <Stepper
              active={Math.max(0, PAYMENT_PHASE_ORDER.indexOf(invoice.payment_phase))}
              color="cyan"
              size="xs"
              styles={{
                root: { padding: '0 4px' },
                step: { minWidth: 0 },
                stepIcon: {
                  background: customerBrand.bgCard,
                  borderColor: customerBrand.border,
                },
                stepLabel: {
                  color: customerBrand.textPrimary,
                  fontFamily: customerBrand.fontBody,
                  fontSize: 12,
                },
                separator: { borderColor: customerBrand.border },
              }}
            >
              {PAYMENT_PHASE_ORDER.map((phase) => (
                <Stepper.Step
                  key={phase}
                  label={PAYMENT_PHASE_LABELS[phase]}
                  completedIcon={<IconCheck size={14} />}
                />
              ))}
            </Stepper>

            {/* Two-row payment table: deposit row + balance row. */}
            <Paper
              radius="sm"
              style={{
                background: customerBrand.bgDeep,
                border: `1px solid ${customerBrand.border}`,
                padding: 12,
                overflowX: 'auto',
              }}
            >
              <Table styles={tableStyles}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>ITEM</Table.Th>
                    <Table.Th style={{ textAlign: 'right' }}>AMOUNT</Table.Th>
                    <Table.Th style={{ textAlign: 'center' }}>STATUS</Table.Th>
                    <Table.Th style={{ textAlign: 'right' }}>ACTION</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {invoice.deposit_required && (
                    <Table.Tr>
                      <Table.Td style={monoFont}>
                        Deposit (TOS &sect;6.2)
                      </Table.Td>
                      <Table.Td style={{ textAlign: 'right', ...monoFont, color: customerBrand.brandCyan }}>
                        {fmtMoney(invoice.deposit_amount)}
                      </Table.Td>
                      <Table.Td style={{ textAlign: 'center' }}>
                        {invoice.deposit_paid ? (
                          <Badge color="green" variant="light" style={monoFont}>
                            PAID{invoice.deposit_paid_at ? ` ${invoice.deposit_paid_at.slice(0, 10)}` : ''}
                          </Badge>
                        ) : (
                          <Badge color="cyan" variant="light" style={monoFont}>
                            DUE
                          </Badge>
                        )}
                      </Table.Td>
                      <Table.Td style={{ textAlign: 'right' }}>
                        {invoice.payment_phase === 'deposit_due' ? (
                          <Button
                            size="xs"
                            loading={paying === 'deposit'}
                            onClick={() => handlePay('deposit')}
                            styles={{
                              root: {
                                background: customerBrand.brandCyan,
                                color: customerBrand.brandNavyDeep,
                                fontFamily: customerBrand.fontDisplay,
                                letterSpacing: customerBrand.trackMid,
                                fontWeight: 700,
                                minHeight: 32,
                              },
                            }}
                          >
                            PAY DEPOSIT
                          </Button>
                        ) : (
                          <Text
                            size="xs"
                            style={{ color: customerBrand.textMuted, ...monoFont }}
                          >
                            —
                          </Text>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  )}
                  <Table.Tr>
                    <Table.Td style={monoFont}>
                      {invoice.deposit_required ? 'Balance' : 'Total'}
                    </Table.Td>
                    <Table.Td style={{ textAlign: 'right', ...monoFont, color: customerBrand.brandCyan }}>
                      {fmtMoney(
                        invoice.deposit_required ? invoice.balance_amount : invoice.total
                      )}
                    </Table.Td>
                    <Table.Td style={{ textAlign: 'center' }}>
                      {invoice.paid_in_full ? (
                        <Badge color="green" variant="light" style={monoFont}>
                          PAID{invoice.paid_at ? ` ${invoice.paid_at.slice(0, 10)}` : ''}
                        </Badge>
                      ) : invoice.payment_phase === 'awaiting_completion' ? (
                        <Badge color="gray" variant="light" style={monoFont}>
                          AWAITING COMPLETION
                        </Badge>
                      ) : invoice.payment_phase === 'balance_due' ? (
                        <Badge color="cyan" variant="light" style={monoFont}>
                          DUE
                        </Badge>
                      ) : (
                        <Badge color="gray" variant="light" style={monoFont}>
                          —
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td style={{ textAlign: 'right' }}>
                      {invoice.payment_phase === 'balance_due' ? (
                        <Button
                          size="xs"
                          loading={paying === 'balance'}
                          onClick={() => handlePay('balance')}
                          styles={{
                            root: {
                              background: customerBrand.brandCyan,
                              color: customerBrand.brandNavyDeep,
                              fontFamily: customerBrand.fontDisplay,
                              letterSpacing: customerBrand.trackMid,
                              fontWeight: 700,
                              minHeight: 32,
                            },
                          }}
                        >
                          PAY BALANCE
                        </Button>
                      ) : (
                        <Text
                          size="xs"
                          style={{ color: customerBrand.textMuted, ...monoFont }}
                        >
                          —
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                </Table.Tbody>
              </Table>
            </Paper>

            {/* Itemized line items (read-only). */}
            {invoice.line_items.length > 0 && (
              <>
                <Divider color={customerBrand.border} />
                <Text
                  size="xs"
                  style={{
                    color: customerBrand.textMuted,
                    ...monoFont,
                    letterSpacing: customerBrand.trackMid,
                    textTransform: 'uppercase',
                  }}
                >
                  Line Items
                </Text>
                <Paper
                  radius="sm"
                  style={{
                    background: customerBrand.bgDeep,
                    border: `1px solid ${customerBrand.border}`,
                    padding: 12,
                    overflowX: 'auto',
                  }}
                >
                  <Table styles={tableStyles}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>DESCRIPTION</Table.Th>
                        <Table.Th style={{ textAlign: 'right' }}>QTY</Table.Th>
                        <Table.Th style={{ textAlign: 'right' }}>UNIT</Table.Th>
                        <Table.Th style={{ textAlign: 'right' }}>TOTAL</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {invoice.line_items.map((li, i) => (
                        <Table.Tr key={i}>
                          <Table.Td>{li.description}</Table.Td>
                          <Table.Td style={{ textAlign: 'right', ...monoFont }}>
                            {li.quantity}
                          </Table.Td>
                          <Table.Td style={{ textAlign: 'right', ...monoFont }}>
                            {fmtMoney(li.unit_price)}
                          </Table.Td>
                          <Table.Td style={{ textAlign: 'right', ...monoFont }}>
                            {fmtMoney(li.total)}
                          </Table.Td>
                        </Table.Tr>
                      ))}
                      <Table.Tr>
                        <Table.Td
                          colSpan={3}
                          style={{
                            textAlign: 'right',
                            fontWeight: 700,
                            fontFamily: customerBrand.fontDisplay,
                            letterSpacing: customerBrand.trackMid,
                          }}
                        >
                          TOTAL
                        </Table.Td>
                        <Table.Td
                          style={{
                            textAlign: 'right',
                            ...monoFont,
                            color: customerBrand.brandCyan,
                            fontWeight: 700,
                            fontSize: 15,
                          }}
                        >
                          {fmtMoney(invoice.total)}
                        </Table.Td>
                      </Table.Tr>
                    </Table.Tbody>
                  </Table>
                </Paper>
              </>
            )}
          </Stack>
        )}
      </Card>
    </CustomerLayout>
  );
}
