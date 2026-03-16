import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Card,
  Group,
  Loader,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconCash,
  IconChartBar,
  IconDrone,
  IconReceipt,
  IconSearch,
  IconTrendingUp,
  IconCalendar,
} from '@tabler/icons-react';
import api from '../api/client';

// --- Design tokens (matching Flights page) ---

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };
const monoFont = { fontFamily: "'Share Tech Mono', monospace" };
const DRONE_COLORS = ['#00d4ff', '#ff6b1a', '#2ecc40', '#ff6b6b', '#b57edc', '#ffd43b', '#20c997', '#ff8787'];
const CATEGORY_LABELS: Record<string, string> = {
  billed_time: 'Billed Time',
  travel: 'Travel',
  rapid_deployment: 'Rapid Deploy',
  equipment: 'Equipment',
  special: 'Special',
  other: 'Other',
};
const TYPE_LABELS: Record<string, string> = {
  sar: 'Search & Rescue',
  videography: 'Videography',
  lost_pet: 'Lost Pet',
  inspection: 'Inspection',
  mapping: 'Mapping',
  photography: 'Photography',
  survey: 'Survey',
  security_investigations: 'Security / Investigations',
  other: 'Other',
};

// --- Formatters ---

function formatCurrency(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return String(dateStr);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return String(dateStr);
  }
}

function formatMonth(ym: string): string {
  try {
    const [y, m] = ym.split('-');
    const d = new Date(Number(y), Number(m) - 1);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short' });
  } catch {
    return ym;
  }
}

// --- StatCard ---

function StatCard({ icon: Icon, label, value, sub, color = '#00d4ff' }: {
  icon: any; label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Group gap="sm" wrap="nowrap">
        <Icon size={22} color={color} style={{ flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <Text size="11px" c="#5a6478" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
            {label}
          </Text>
          <Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '26px', lineHeight: 1.1 }}>
            {value}
          </Text>
          {sub && (
            <Text size="xs" c="#5a6478" style={monoFont}>{sub}</Text>
          )}
        </div>
      </Group>
    </Card>
  );
}

// --- Bar chart component ---

function HorizontalBars({ title, items, colorKey }: {
  title: string;
  items: { label: string; value: number; sub?: string }[];
  colorKey?: boolean;
}) {
  if (items.length === 0) return null;
  const maxVal = Math.max(...items.map((i) => i.value));

  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Text size="11px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
        {title}
      </Text>
      <Stack gap={8}>
        {items.map((item, i) => {
          const pct = maxVal > 0 ? (item.value / maxVal) * 100 : 0;
          const color = DRONE_COLORS[i % DRONE_COLORS.length];
          return (
            <div key={item.label}>
              <Group justify="space-between" mb={2}>
                <Text size="xs" c="#e8edf2" fw={500}>{item.label}</Text>
                <Group gap={8}>
                  {item.sub && <Text size="xs" c="#5a6478" style={monoFont}>{item.sub}</Text>}
                  <Text size="xs" c={color} style={monoFont} fw={600}>{formatCurrency(item.value)}</Text>
                </Group>
              </Group>
              <div style={{ background: '#1a1f2e', borderRadius: 3, height: 6, overflow: 'hidden' }}>
                <div style={{ background: color, width: `${pct}%`, height: '100%', borderRadius: 3, transition: 'width 0.3s' }} />
              </div>
            </div>
          );
        })}
      </Stack>
    </Card>
  );
}

// --- Monthly revenue mini-chart ---

function MonthlyChart({ data }: { data: { month: string; revenue: number }[] }) {
  if (data.length === 0) return null;
  const maxVal = Math.max(...data.map((d) => d.revenue));

  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Text size="11px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
        MONTHLY REVENUE
      </Text>
      <Group gap={8} align="end" style={{ height: 120 }}>
        {data.map((d, i) => {
          const pct = maxVal > 0 ? (d.revenue / maxVal) * 100 : 0;
          return (
            <div
              key={d.month}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'flex-end',
                height: '100%',
                minWidth: 0,
              }}
            >
              <Text size="xs" c="#00d4ff" style={monoFont} mb={2}>{formatCurrency(d.revenue)}</Text>
              <div
                style={{
                  width: '100%',
                  maxWidth: 40,
                  height: `${Math.max(pct, 4)}%`,
                  background: DRONE_COLORS[i % DRONE_COLORS.length],
                  borderRadius: '3px 3px 0 0',
                  transition: 'height 0.3s',
                }}
              />
              <Text size="11px" c="#5a6478" style={monoFont} mt={4} ta="center" lineClamp={1}>
                {formatMonth(d.month)}
              </Text>
            </div>
          );
        })}
      </Group>
    </Card>
  );
}

// --- Top customers ---

function TopCustomers({ customers }: { customers: { name: string; company: string; total: number; missions: number }[] }) {
  if (customers.length === 0) return null;

  return (
    <Card padding="md" radius="md" style={cardStyle}>
      <Text size="11px" c="#5a6478" mb="sm" style={{ ...monoFont, letterSpacing: '1px' }} tt="uppercase">
        TOP CUSTOMERS
      </Text>
      <Stack gap={8}>
        {customers.slice(0, 5).map((c, i) => (
          <Group key={c.name} justify="space-between">
            <Group gap="xs">
              <Badge size="xs" color={i === 0 ? 'yellow' : 'gray'} variant="filled" w={20} style={{ textAlign: 'center' }}>
                {i + 1}
              </Badge>
              <div>
                <Text size="xs" c="#e8edf2" fw={500} lineClamp={1}>{c.name}</Text>
                {c.company && <Text size="xs" c="#5a6478" style={monoFont}>{c.company}</Text>}
              </div>
            </Group>
            <div style={{ textAlign: 'right' }}>
              <Text size="xs" c="#00d4ff" style={monoFont} fw={600}>{formatCurrency(c.total)}</Text>
              <Text size="xs" c="#5a6478" style={monoFont}>{c.missions} mission{c.missions !== 1 ? 's' : ''}</Text>
            </div>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}

// === Main Component ===

export default function Financials() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const loadData = async () => {
    setLoading(true);
    try {
      const resp = await api.get('/financials/summary');
      setData(resp.data);
    } catch (err: any) {
      notifications.show({
        title: 'Financials',
        message: err.response?.data?.detail || 'Failed to load financial data',
        color: 'red',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const filtered = useMemo(() => {
    if (!data?.missions) return [];
    if (!search) return data.missions;
    const q = search.toLowerCase();
    return data.missions.filter((m: any) => {
      const searchable = [
        m.title, m.customer_name, m.location, m.mission_type,
        m.invoice_number, m.mission_date,
      ].filter(Boolean).join(' ').toLowerCase();
      return searchable.includes(q);
    });
  }, [data, search]);

  return (
    <Stack gap="lg">
      <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>FINANCIALS</Title>

      {loading ? (
        <Group justify="center" py="xl">
          <Loader color="cyan" size="lg" />
          <Text c="#5a6478">Loading financial data...</Text>
        </Group>
      ) : !data ? (
        <Card padding="xl" radius="md" style={cardStyle}>
          <Stack align="center" gap="md">
            <IconCash size={48} color="#5a6478" />
            <Text c="#5a6478" ta="center">No financial data available.</Text>
          </Stack>
        </Card>
      ) : (
        <>
          {/* ===== Summary Stats ===== */}
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }}>
            <StatCard
              icon={IconCash}
              label="Total Billed"
              value={formatCurrency(data.total_revenue)}
              sub={`${data.invoiced_count} mission${data.invoiced_count !== 1 ? 's' : ''}`}
            />
            <StatCard
              icon={IconTrendingUp}
              label="Avg / Mission"
              value={formatCurrency(data.avg_per_mission)}
            />
            <StatCard
              icon={IconReceipt}
              label="Billable Missions"
              value={String(data.billable_missions)}
              sub={`${data.invoiced_count} invoiced`}
            />
            <StatCard
              icon={IconCalendar}
              label="Prepaid"
              value={String(data.paid_count)}
              sub={data.paid_count > 0 ? formatCurrency(data.total_paid) : 'none'}
              color="#2ecc40"
            />
          </SimpleGrid>

          {/* ===== Revenue by Drone & Category ===== */}
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <HorizontalBars
              title="Revenue by Drone"
              items={data.drone_revenue.map((d: any) => ({
                label: d.name,
                value: d.revenue,
              }))}
            />
            <HorizontalBars
              title="Revenue by Category"
              items={Object.entries(data.category_totals)
                .sort((a, b) => (b[1] as number) - (a[1] as number))
                .map(([cat, val]) => ({
                  label: CATEGORY_LABELS[cat] || cat,
                  value: val as number,
                }))}
            />
          </SimpleGrid>

          {/* ===== Monthly Revenue & Mission Type & Top Customers ===== */}
          <SimpleGrid cols={{ base: 1, md: 3 }}>
            <MonthlyChart data={data.monthly_revenue} />
            <HorizontalBars
              title="Revenue by Mission Type"
              items={data.mission_type_revenue.map((t: any) => ({
                label: TYPE_LABELS[t.type] || t.type,
                value: t.revenue,
              }))}
            />
            <TopCustomers customers={data.customer_revenue} />
          </SimpleGrid>

          {/* ===== Mission Invoices Table ===== */}
          <Card padding="lg" radius="md" style={cardStyle}>
            <Group justify="space-between" mb="md">
              <Text size="sm" c="#5a6478" style={monoFont}>
                {filtered.length} INVOICE{filtered.length !== 1 ? 'S' : ''}
              </Text>
              <TextInput
                placeholder="Search invoices..."
                leftSection={<IconSearch size={14} />}
                size="xs"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                styles={{
                  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', width: 260 },
                }}
              />
            </Group>

            <ScrollArea>
              <Table
                highlightOnHover
                styles={{
                  table: { color: '#e8edf2' },
                  th: {
                    color: '#00d4ff',
                    fontFamily: "'Share Tech Mono', monospace",
                    fontSize: '12px',
                    letterSpacing: '1px',
                    borderBottom: '1px solid #1a1f2e',
                    padding: '10px 12px',
                    whiteSpace: 'nowrap',
                  },
                  td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
                }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>MISSION</Table.Th>
                    <Table.Th>DATE</Table.Th>
                    <Table.Th>TYPE</Table.Th>
                    <Table.Th>CUSTOMER</Table.Th>
                    <Table.Th>LOCATION</Table.Th>
                    <Table.Th style={{ textAlign: 'right' }}>AMOUNT</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {filtered.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={6}>
                        <Text c="#5a6478" ta="center" py="md">No invoiced missions found.</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    filtered.map((m: any) => (
                      <Table.Tr key={m.id}>
                        <Table.Td>
                          <Text size="sm" fw={500} lineClamp={1}>{m.title}</Text>
                          {m.invoice_number && (
                            <Text size="xs" c="#5a6478" style={monoFont}>#{m.invoice_number}</Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="#5a6478" style={monoFont}>{formatDate(m.mission_date)}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="#e8edf2">{TYPE_LABELS[m.mission_type] || m.mission_type}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="#e8edf2">{m.customer_name || '—'}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="#5a6478" lineClamp={1}>{m.location || '—'}</Text>
                        </Table.Td>
                        <Table.Td style={{ textAlign: 'right' }}>
                          <Group gap={6} justify="flex-end" wrap="nowrap">
                            <Text size="sm" c="#00d4ff" style={monoFont} fw={600}>
                              {formatCurrency(m.invoice_total)}
                            </Text>
                            {m.paid && (
                              <Badge size="xs" color="green" variant="light">PREPAID</Badge>
                            )}
                          </Group>
                        </Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </>
      )}
    </Stack>
  );
}
