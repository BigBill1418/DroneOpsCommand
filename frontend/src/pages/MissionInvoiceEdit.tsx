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
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowLeft, IconDeviceFloppy, IconPlus, IconTrash } from '@tabler/icons-react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import type { Invoice, Mission, RateTemplate } from '../api/types';
import { cardStyle, inputStyles } from '../components/shared/styles';

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

interface EditableLineItem {
  description: string;
  category: string;
  quantity: number;
  unit_price: number;
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
          setTaxRate(inv.tax_rate ?? 0);
          setNotes(inv.notes ?? '');
          setPaidInFull(inv.paid_in_full);
          setDepositRequired(inv.deposit_required ?? false);
          setDepositAmount(inv.deposit_amount ?? null);
          setDepositPaid(inv.deposit_paid ?? false);
          setLineItems(
            (inv.line_items || []).map((li) => ({
              description: li.description,
              category: li.category,
              quantity: li.quantity,
              unit_price: li.unit_price,
            })),
          );
        } else {
          // 404 — no invoice yet for this mission. Form starts empty so
          // the operator can build one.
          setInvoiceExists(false);
          setInvoiceId(null);
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

  const subtotal = useMemo(
    () => lineItems.reduce((sum, li) => sum + (li.quantity || 0) * (li.unit_price || 0), 0),
    [lineItems],
  );

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
        updatePayload.deposit_amount = depositRequired ? depositAmount : 0;
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
            deposit_amount: depositRequired ? depositAmount : 0,
          });
          hasInvoice = true;
        } else {
          throw err;
        }
      }

      // Replace line items: fetch existing IDs then delete + re-add.
      // Same delete-then-recreate strategy MissionNew uses; cheaper than
      // diff-and-patch and the backend recalculates totals each step.
      if (hasInvoice) {
        const refresh = await api.get(`/missions/${id}/invoice`);
        const existing: { id: string }[] = refresh.data.line_items || [];
        for (const li of existing) {
          await api.delete(`/missions/${id}/invoice/items/${li.id}`);
        }
        for (let i = 0; i < validItems.length; i++) {
          const li = validItems[i];
          await api.post(`/missions/${id}/invoice/items`, {
            description: li.description,
            category: li.category,
            quantity: li.quantity,
            unit_price: li.unit_price,
            sort_order: i,
          });
        }

        // After items change, total may have moved — re-PUT deposit_amount
        // when operator left auto-fill mode (null) so backend recomputes.
        if (!depositPaid && depositRequired && depositAmount === null) {
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

  if (loading || !mission) {
    return (
      <Group justify="center" py="xl">
        <Loader color="cyan" />
      </Group>
    );
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" wrap="wrap">
        <div>
          <Group gap="xs" mb={4}>
            <Button
              variant="subtle"
              size="xs"
              color="gray"
              leftSection={<IconArrowLeft size={14} />}
              onClick={() => navigate(`/missions/${id}`)}
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
          <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
            EDIT INVOICE — {mission.title.toUpperCase()}
          </Title>
        </div>
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

      <Card padding="lg" radius="md" style={cardStyle}>
        <Stack gap="md">
          <Group justify="space-between" wrap="wrap">
            <Text c="#e8edf2" fw={600}>
              Line Items
            </Text>
            <Group gap="xs" wrap="wrap">
              <Select
                placeholder="Add from template..."
                data={rateTemplates.map((t) => ({
                  value: t.id,
                  label: `${t.name} ($${t.default_rate}/${t.default_unit || 'ea'})`,
                }))}
                clearable
                size="xs"
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
                    width: 280,
                  },
                }}
              />
              <Button
                leftSection={<IconPlus size={14} />}
                size="xs"
                color="cyan"
                variant="light"
                onClick={handleAddLineItem}
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
            <Group key={i} align="end">
              <TextInput
                label="Description"
                style={{ flex: 2 }}
                value={item.description}
                onChange={(e) => {
                  const updated = [...lineItems];
                  updated[i].description = e.target.value;
                  setLineItems(updated);
                }}
                styles={inputStyles}
              />
              <Select
                label="Category"
                data={lineItemCategories}
                value={item.category}
                onChange={(val) => {
                  const updated = [...lineItems];
                  updated[i].category = val || 'other';
                  setLineItems(updated);
                }}
                styles={inputStyles}
                style={{ flex: 1 }}
              />
              <NumberInput
                label="Qty"
                value={item.quantity}
                onChange={(val) => {
                  const updated = [...lineItems];
                  updated[i].quantity = typeof val === 'number' ? val : 1;
                  setLineItems(updated);
                }}
                min={0}
                styles={inputStyles}
                style={{ width: 80 }}
              />
              <NumberInput
                label="Price"
                value={item.unit_price}
                onChange={(val) => {
                  const updated = [...lineItems];
                  updated[i].unit_price = typeof val === 'number' ? val : 0;
                  setLineItems(updated);
                }}
                min={0}
                decimalScale={2}
                prefix="$"
                styles={inputStyles}
                style={{ width: 110 }}
              />
              <ActionIcon
                color="red"
                variant="subtle"
                onClick={() => setLineItems(lineItems.filter((_, j) => j !== i))}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
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
              <NumberInput
                label="Deposit Amount"
                description={
                  depositPaid
                    ? 'Locked — deposit already collected.'
                    : 'TOS §6.2 default. Leave blank to auto-fill 50% of total. Uncheck above for Emergent Services per §6.3.'
                }
                value={depositAmount ?? ''}
                onChange={(val) => {
                  if (val === '' || val === null || val === undefined) {
                    setDepositAmount(null);
                  } else {
                    const num = typeof val === 'number' ? val : parseFloat(String(val));
                    setDepositAmount(Number.isFinite(num) ? num : null);
                  }
                }}
                disabled={depositPaid}
                min={0}
                decimalScale={2}
                prefix="$"
                placeholder="auto-fill 50% of total"
                styles={{
                  input: {
                    background: '#050608',
                    borderColor: '#1a1f2e',
                    color: '#e8edf2',
                    maxWidth: 220,
                  },
                  label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace" },
                  description: { color: '#5a6478', fontStyle: 'italic' },
                }}
              />
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
            styles={{
              input: {
                background: '#050608',
                borderColor: '#1a1f2e',
                color: '#e8edf2',
                maxWidth: 180,
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

      <Group justify="flex-end">
        <Button
          variant="subtle"
          color="gray"
          onClick={() => navigate(`/missions/${id}`)}
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
    </Stack>
  );
}
