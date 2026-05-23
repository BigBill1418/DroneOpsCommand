/**
 * Standalone "Edit Invoice" page (v2.66.0 frontend polish — Fix #4).
 *
 * Pre-v2.66, the only path to a mission's line items was Step 5 of the
 * MissionNew wizard. Once you saved the mission, fixing a typo in the
 * invoice meant re-walking 5 wizard steps. This page mounts a focused
 * invoice form for an existing mission, reusing the same controls
 * (line items, deposit toggle, deposit amount, paid-in-full, notes)
 * and the same backend endpoints as the wizard.
 *
 * Mounted at: /missions/:id/invoice/edit
 * Backend:    GET / POST / PUT / DELETE /missions/{id}/invoice + /items
 *
 * Behavior parity with MissionNew Step 5:
 *  - On save, if no invoice row exists yet, POST to create one (with
 *    deposit_required + deposit_amount + tax + notes), then add line
 *    items via /invoice/items.
 *  - If one exists, PUT to update + replace all line items
 *    (delete-then-recreate, mirroring MissionNew.handleSaveInvoice).
 *  - Deposit fields lock once `deposit_paid` is true (backend rejects
 *    edits anyway; we just gray them out with explanation).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconArrowLeft, IconDeviceFloppy, IconPlus } from '@tabler/icons-react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Invoice, Mission, RateTemplate } from '../api/types';
import { LineItemFields } from '../components/invoice/LineItemFields';
import type { EditableLineItem } from '../components/invoice/LineItemFields';
import { cardStyle, inputStyles, monoFont } from '../components/shared/styles';
import UnsavedChangesModal from '../components/shared/UnsavedChangesModal';
import { useDirtyGuard } from '../hooks/useDirtyGuard';

// Same enum as MissionNew — kept in sync intentionally; a shared constant
// would be the right refactor when there are 3+ consumers.
const lineItemCategories = [
  { value: 'travel', label: 'Travel' },
  { value: 'billed_time', label: 'Billed Time' },
  { value: 'rapid_deployment', label: 'Rapid Deployment' },
  { value: 'equipment', label: 'Equipment' },
  { value: 'special', label: 'Special Circumstances' },
  { value: 'other', label: 'Other' },
];

/**
 * Serialize the operator-editable invoice state into a string for
 * dirty-comparison. Stable key order via explicit object construction
 * (JSON.stringify on a hand-built object is deterministic in V8/JSC).
 */
function serializeInvoiceState(state: {
  lineItems: EditableLineItem[];
  taxRate: number;
  notes: string;
  paidInFull: boolean;
  depositRequired: boolean;
  depositAmount: number | null;
}): string {
  return JSON.stringify({
    lineItems: state.lineItems.map((li) => ({
      description: li.description,
      category: li.category,
      quantity: li.quantity,
      unit_price: li.unit_price,
    })),
    taxRate: state.taxRate,
    notes: state.notes,
    paidInFull: state.paidInFull,
    depositRequired: state.depositRequired,
    depositAmount: state.depositAmount,
  });
}

export default function MissionInvoiceEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);

  const [mission, setMission] = useState<Mission | null>(null);
  const [invoiceExists, setInvoiceExists] = useState(false);
  const [invoiceId, setInvoiceId] = useState<string | null>(null);

  const [lineItems, setLineItems] = useState<EditableLineItem[]>([]);
  const [rateTemplates, setRateTemplates] = useState<RateTemplate[]>([]);
  const [taxRate, setTaxRate] = useState<number>(0);
  const [notes, setNotes] = useState<string>('');
  const [paidInFull, setPaidInFull] = useState(false);

  // ADR-0009 deposit state — same defaults as MissionNew.
  const [depositRequired, setDepositRequired] = useState(true);
  const [depositAmount, setDepositAmount] = useState<number | null>(null);
  const [depositPaid, setDepositPaid] = useState(false);

  // Serialized baseline for dirty-guard. Re-baselined on initial load
  // and after every successful save. depositPaid is excluded — it's
  // server-driven and not operator-editable.
  const [baselineSerialized, setBaselineSerialized] = useState<string>(
    serializeInvoiceState({
      lineItems: [],
      taxRate: 0,
      notes: '',
      paidInFull: false,
      depositRequired: true,
      depositAmount: null,
    }),
  );

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      try {
        const [missionResp, invoiceResult, templatesResult] = await Promise.allSettled([
          api.get(`/missions/${id}`),
          api.get(`/missions/${id}/invoice`),
          api.get('/rate-templates'),
        ]);

        if (cancelled) return;

        if (missionResp.status !== 'fulfilled') {
          notifications.show({ title: 'Error', message: 'Mission not found', color: 'red' });
          navigate('/missions');
          return;
        }
        setMission(missionResp.value.data as Mission);

        if (templatesResult.status === 'fulfilled') {
          setRateTemplates(templatesResult.value.data as RateTemplate[]);
        }

        if (invoiceResult.status === 'fulfilled') {
          const inv = invoiceResult.value.data as Invoice;
          setInvoiceExists(true);
          setInvoiceId(inv.id);
          const initialTaxRate = inv.tax_rate ?? 0;
          const initialNotes = inv.notes ?? '';
          const initialPaidInFull = inv.paid_in_full;
          const initialDepositRequired = inv.deposit_required ?? false;
          const initialDepositAmount = inv.deposit_amount ?? null;
          const initialLineItems: EditableLineItem[] = (inv.line_items || []).map((li) => ({
            description: li.description,
            category: li.category,
            quantity: li.quantity,
            unit_price: li.unit_price,
          }));
          setTaxRate(initialTaxRate);
          setNotes(initialNotes);
          setPaidInFull(initialPaidInFull);
          setDepositRequired(initialDepositRequired);
          setDepositAmount(initialDepositAmount);
          setDepositPaid(inv.deposit_paid ?? false);
          setLineItems(initialLineItems);
          setBaselineSerialized(
            serializeInvoiceState({
              lineItems: initialLineItems,
              taxRate: initialTaxRate,
              notes: initialNotes,
              paidInFull: initialPaidInFull,
              depositRequired: initialDepositRequired,
              depositAmount: initialDepositAmount,
            }),
          );
        } else {
          // 404 — no invoice yet for this mission. Form starts empty so
          // the operator can build one. Baseline matches the post-mount
          // empty state so an untouched form is NOT considered dirty.
          setInvoiceExists(false);
          setInvoiceId(null);
          setBaselineSerialized(
            serializeInvoiceState({
              lineItems: [],
              taxRate: 0,
              notes: '',
              paidInFull: false,
              depositRequired: true,
              depositAmount: null,
            }),
          );
        }
      } catch (err) {
        console.error('[MissionInvoiceEdit] load failed:', err);
        notifications.show({ title: 'Error', message: 'Failed to load invoice', color: 'red' });
        navigate(`/missions/${id}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, navigate]);

  const isMobile = useMediaQuery('(max-width: 768px)') ?? false;

  const subtotal = useMemo(
    () => lineItems.reduce((sum, li) => sum + (li.quantity || 0) * (li.unit_price || 0), 0),
    [lineItems],
  );

  // 50% deposit preview, mirroring the backend's round(total * 0.5, 2).
  // Tax is 0 in practice; subtotal == total. The saved value is
  // authoritative (recomputed server-side on save).
  const depositPreview = Math.round(subtotal * 50) / 100;

  const handleAddLineItem = () => {
    setLineItems((prev) => [
      ...prev,
      { description: '', category: 'other', quantity: 1, unit_price: 0 },
    ]);
  };

  const handleSave = async () => {
    if (!id) return;
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);

    const validItems = lineItems.filter((li) => li.description.trim());
    if (validItems.length === 0) {
      notifications.show({
        title: 'Invoice Required',
        message: 'Add at least one line item with a description before saving.',
        color: 'red',
      });
      savingRef.current = false;
      setSaving(false);
      return;
    }

    try {
      // Re-check existence on the server right before save to avoid
      // stale-state races (mirrors MissionNew.handleSaveInvoice).
      let hasInvoice = invoiceExists;
      const updatePayload: Record<string, unknown> = {
        tax_rate: taxRate,
        paid_in_full: paidInFull,
        notes: notes.trim() || null,
      };
      // Only send deposit fields when they're still mutable (backend
      // 400s if you send them after deposit_paid).
      if (!depositPaid) {
        updatePayload.deposit_required = depositRequired;
        // Deposit is always 50% of total, derived by the backend. Send
        // null (auto); any amount is ignored server-side.
        updatePayload.deposit_amount = null;
      }

      try {
        await api.put(`/missions/${id}/invoice`, updatePayload);
        hasInvoice = true;
      } catch (err: unknown) {
        const axiosErr = err as { response?: { status?: number } };
        if (axiosErr.response?.status === 404) {
          // No invoice row yet — create one. (Backend POST honors
          // deposit_required + deposit_amount in the create payload.)
          await api.post(`/missions/${id}/invoice`, {
            tax_rate: taxRate,
            notes: notes.trim() || null,
            paid_in_full: paidInFull,
            deposit_required: depositRequired,
            deposit_amount: null,
          });
          hasInvoice = true;
        } else {
          throw err;
        }
      }

      // Replace ALL line items in one atomic request (PUT /invoice/items).
      // The old per-item delete-then-add loop was non-transactional — an
      // interruption (e.g. a flaky field connection) left the line items
      // and the stored total out of sync. The backend recalculates the
      // total as part of the atomic replace.
      if (hasInvoice) {
        await api.put(
          `/missions/${id}/invoice/items`,
          validItems.map((li, i) => ({
            description: li.description,
            category: li.category,
            quantity: li.quantity,
            unit_price: li.unit_price,
            sort_order: i,
          })),
        );

        // The backend defers the deposit at create (total is 0 before line
        // items exist), so once items are in place re-assert deposit_required
        // — _recalculate_invoice then derives the deposit as 50% of the now-
        // complete total.
        if (!depositPaid && depositRequired) {
          await api.put(`/missions/${id}/invoice`, {
            deposit_required: true,
            deposit_amount: null,
          });
        }

        const finalResp = await api.get(`/missions/${id}/invoice`);
        const finalInv = finalResp.data as Invoice;
        setInvoiceExists(true);
        setInvoiceId(finalInv.id);
        setDepositAmount(finalInv.deposit_amount ?? null);
      }

      // Re-baseline from current in-memory state so a Cancel after a
      // successful Save doesn't re-prompt. We use the latest state we
      // posted (validItems, not the raw lineItems which may include
      // empty-description rows the backend dropped).
      setBaselineSerialized(
        serializeInvoiceState({
          lineItems: validItems,
          taxRate,
          notes,
          paidInFull,
          depositRequired,
          depositAmount,
        }),
      );
      notifications.show({
        title: 'Invoice Saved',
        message: 'Line items, deposit, and totals updated.',
        color: 'cyan',
      });
      navigate(`/missions/${id}`);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      const detail = axiosErr.response?.data?.detail || 'Failed to save invoice';
      notifications.show({ title: 'Error', message: detail, color: 'red' });
      console.error('[MissionInvoiceEdit] save failed:', err);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  // Dirty calc: serialized current state diverges from baseline.
  // Gated to false during initial load so the empty default state
  // doesn't false-positive before the GET response lands.
  const currentSerialized = serializeInvoiceState({
    lineItems,
    taxRate,
    notes,
    paidInFull,
    depositRequired,
    depositAmount,
  });
  const isDirty = !loading && currentSerialized !== baselineSerialized;

  const { showConfirm, setShowConfirm, guardedNavigate, confirmAndNavigate } =
    useDirtyGuard({ isDirty, navigate });

  const handleCancel = () => {
    guardedNavigate(`/missions/${id}`);
  };

  if (loading || !mission) {
    return (
      <Group justify="center" py="xl">
        <Loader color="cyan" />
      </Group>
    );
  }

  return (
    <Stack gap="lg" pb={isMobile ? 104 : 0}>
      <Group justify="space-between" wrap="wrap">
        <div>
          <Group gap="xs" mb={4}>
            <Button
              variant="subtle"
              size="xs"
              color="gray"
              leftSection={<IconArrowLeft size={14} />}
              onClick={handleCancel}
              styles={{ root: { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' } }}
            >
              BACK TO MISSION
            </Button>
            {depositPaid && (
              <Badge color="green" variant="light" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                DEPOSIT PAID
              </Badge>
            )}
            {invoiceId === null && (
              <Badge color="cyan" variant="light" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                NEW INVOICE
              </Badge>
            )}
          </Group>
          <Title order={isMobile ? 3 : 2} c="#e8edf2" style={{ letterSpacing: isMobile ? '1px' : '2px' }}>
            EDIT INVOICE — {mission.title.toUpperCase()}
          </Title>
        </div>
        {!isMobile && (
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            color="cyan"
            loading={saving}
            onClick={handleSave}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            SAVE INVOICE
          </Button>
        )}
      </Group>

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <Group justify="space-between" wrap="wrap">
            <Text c="#e8edf2" fw={600}>
              Line Items
            </Text>
            <Group gap="xs" wrap="wrap" style={{ flex: isMobile ? '1 1 100%' : undefined }}>
              <Select
                placeholder="Add from template..."
                data={rateTemplates.map((t) => ({
                  value: t.id,
                  label: `${t.name} ($${t.default_rate}/${t.default_unit || 'ea'})`,
                }))}
                clearable
                size={isMobile ? 'sm' : 'xs'}
                style={{ flex: 1, minWidth: isMobile ? 0 : 240 }}
                onChange={(val) => {
                  if (!val) return;
                  const tmpl = rateTemplates.find((t) => t.id === val);
                  if (tmpl) {
                    setLineItems((prev) => [
                      ...prev,
                      {
                        description:
                          tmpl.name + (tmpl.description ? ` — ${tmpl.description}` : ''),
                        category: tmpl.category,
                        quantity: tmpl.default_quantity,
                        unit_price: tmpl.default_rate,
                      },
                    ]);
                  }
                }}
                styles={{
                  input: {
                    background: '#050608',
                    borderColor: '#1a1f2e',
                    color: '#e8edf2',
                  },
                }}
              />
              <Button
                leftSection={<IconPlus size={14} />}
                size={isMobile ? 'sm' : 'xs'}
                color="cyan"
                variant="light"
                onClick={handleAddLineItem}
                styles={{ root: { flexShrink: 0 } }}
              >
                Blank Item
              </Button>
            </Group>
          </Group>

          {lineItems.length === 0 && (
            <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              No line items yet. Add one from a template or click "Blank Item".
            </Text>
          )}

          {lineItems.map((item, i) => (
            <LineItemFields
              key={i}
              item={item}
              index={i}
              isMobile={isMobile}
              categories={lineItemCategories}
              onChange={(patch) => {
                const updated = [...lineItems];
                updated[i] = { ...updated[i], ...patch };
                setLineItems(updated);
              }}
              onRemove={() => setLineItems(lineItems.filter((_, j) => j !== i))}
            />
          ))}

          <Text
            c="cyan"
            ta="right"
            fw={700}
            style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px' }}
          >
            SUBTOTAL: ${subtotal.toFixed(2)}
          </Text>
        </Stack>
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <Title order={4} c="#e8edf2" style={{ letterSpacing: '1px' }}>
            DEPOSIT &amp; TAX
          </Title>

          {/* ADR-0009 deposit toggle — same control as MissionNew Step 5. */}
          <Stack gap="xs" style={{ borderLeft: '2px solid #1a1f2e', paddingLeft: 12 }}>
            <Switch
              label="Require 50% deposit"
              color="cyan"
              checked={depositRequired}
              disabled={depositPaid}
              onChange={(e) => {
                const next = e.currentTarget.checked;
                setDepositRequired(next);
                if (!next) setDepositAmount(0);
                else if (depositAmount === 0) setDepositAmount(null);
              }}
              styles={{ label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace" } }}
            />
            {depositRequired && (
              <Stack gap={2}>
                <Text size="sm" style={{ color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace" }}>
                  {depositPaid ? 'Deposit Collected' : 'Deposit (50% of total)'}
                </Text>
                <Text fw={700} c="cyan" style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '22px' }}>
                  ${(depositPaid ? (depositAmount ?? 0) : Math.round(subtotal * 50) / 100).toFixed(2)}
                </Text>
                <Text size="xs" style={{ color: '#5a6478', fontStyle: 'italic' }}>
                  {depositPaid
                    ? 'Locked — deposit already collected.'
                    : 'Auto-calculated. Updates as you add or edit line items. Uncheck above for Emergent Services per TOS §6.3.'}
                </Text>
              </Stack>
            )}
          </Stack>

          <NumberInput
            label="Tax Rate (%)"
            description="Applied to subtotal at save time."
            value={taxRate}
            onChange={(val) => setTaxRate(typeof val === 'number' ? val : 0)}
            min={0}
            max={100}
            decimalScale={3}
            size={isMobile ? 'md' : 'sm'}
            styles={{
              input: {
                background: '#050608',
                borderColor: '#1a1f2e',
                color: '#e8edf2',
                maxWidth: isMobile ? undefined : 180,
              },
              label: {
                color: '#5a6478',
                fontFamily: "'Share Tech Mono', monospace",
                fontSize: '13px',
                letterSpacing: '1px',
              },
              description: { color: '#5a6478', fontStyle: 'italic' },
            }}
          />

          <Switch
            label="Paid in Full"
            color="green"
            checked={paidInFull}
            onChange={(e) => setPaidInFull(e.currentTarget.checked)}
            styles={{ label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace" } }}
          />

          <Textarea
            label="Notes"
            description="Optional — appears on the printed invoice / customer portal."
            value={notes}
            onChange={(e) => setNotes(e.currentTarget.value)}
            minRows={3}
            autosize
            styles={inputStyles}
          />
        </Stack>
      </Card>

      {!isMobile && (
        <Group justify="flex-end">
          <Button
            variant="subtle"
            color="gray"
            onClick={handleCancel}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            color="cyan"
            loading={saving}
            onClick={handleSave}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            SAVE INVOICE
          </Button>
        </Group>
      )}

      {/* Mobile: subtotal + live deposit + Save pinned to the bottom of the
          screen so they're always reachable without scrolling in the field. */}
      {isMobile && (
        <Box
          style={{
            position: 'fixed',
            bottom: 0,
            left: 0,
            right: 0,
            zIndex: 190,
            background: '#0e1117',
            borderTop: '1px solid #1a1f2e',
            padding: '8px 12px calc(8px + env(safe-area-inset-bottom))',
            boxShadow: '0 -6px 16px rgba(0,0,0,0.45)',
          }}
        >
          <Group justify="space-between" gap="xs" mb={6} wrap="nowrap">
            <Text size="xs" c="#5a6478" style={monoFont}>
              SUBTOTAL <Text span c="#e8edf2" fw={700}>${subtotal.toFixed(2)}</Text>
              {depositRequired && (
                <>
                  {'  ·  DEPOSIT '}
                  <Text span c="cyan" fw={700}>${depositPreview.toFixed(2)}</Text>
                </>
              )}
            </Text>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Button
              variant="default"
              color="gray"
              onClick={handleCancel}
              disabled={saving}
              styles={{ root: { flexShrink: 0 } }}
            >
              Cancel
            </Button>
            <Button
              flex={1}
              size="md"
              leftSection={<IconDeviceFloppy size={18} />}
              color="cyan"
              loading={saving}
              onClick={handleSave}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              SAVE INVOICE
            </Button>
          </Group>
        </Box>
      )}

      <UnsavedChangesModal
        opened={showConfirm}
        onKeepEditing={() => setShowConfirm(false)}
        onDiscard={confirmAndNavigate}
      />
    </Stack>
  );
}
