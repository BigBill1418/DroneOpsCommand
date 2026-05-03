/**
 * Operator-side TOS audit browse UI (v2.66.0).
 *
 * Backs onto GET /api/tos/acceptances. Lets the operator answer
 * "did this customer sign?", "when?", "give me their PDF" without
 * dropping to psql. Search hits client_email / audit_id / client_name
 * via case-insensitive partial match server-side; pagination is
 * limit/offset (50 per page default).
 *
 * Visual contract: operator dark theme + cyan accents + Bebas Neue
 * for headings + Share Tech Mono for hashes/dates — same convention
 * as Customers / Flights / Missions. Explicitly NOT the customer-
 * portal brand.
 *
 * Failure handling: 401 → axios interceptor handles the refresh /
 * redirect-to-login. 5xx → Mantine notification + console.error.
 *
 * Anchored at /tos-acceptances (registered in App.tsx + AppShell.tsx).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Center,
  Group,
  Loader,
  Pagination,
  ScrollArea,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconFileCertificate,
  IconRefresh,
  IconSearch,
  IconUser,
  IconX,
} from '@tabler/icons-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '../api/client';
import { cardStyle, inputStyles, monoFont } from '../components/shared/styles';

// ── Types ────────────────────────────────────────────────────────────

interface TosAcceptanceListItem {
  id: string;
  audit_id: string;
  customer_id: string | null;
  intake_token: string | null;
  client_name: string;
  client_email: string;
  client_company: string;
  client_title: string;
  client_ip: string;
  user_agent: string;
  accepted_at: string;
  template_version: string;
  template_sha256: string;
  signed_sha256: string;
  signed_pdf_size: number;
  created_at: string;
  download_url: string;
}

interface TosAcceptanceListResponse {
  items: TosAcceptanceListItem[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE_SIZE = 50;

// ── Helpers ──────────────────────────────────────────────────────────

function formatLocalTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function copyToClipboard(value: string, label: string) {
  void (async () => {
    try {
      await navigator.clipboard.writeText(value);
      notifications.show({
        title: 'Copied',
        message: `${label} copied to clipboard.`,
        color: 'cyan',
        autoClose: 1500,
      });
    } catch {
      notifications.show({
        title: 'Copy failed',
        message: 'Clipboard not available — select the text manually.',
        color: 'orange',
      });
    }
  })();
}

// ── Page ─────────────────────────────────────────────────────────────

export default function TosAcceptancesAdmin() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // ?customer_id=<uuid> chip support — surfaces the per-customer
  // filter when the operator drills in from another page.
  const customerIdFilter = searchParams.get('customer_id');

  const [search, setSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(search, 300);

  const [page, setPage] = useState(1); // 1-indexed for Mantine Pagination
  const [items, setItems] = useState<TosAcceptanceListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Reset to page 1 whenever the search term or customer filter
  // changes, otherwise the operator could land on an out-of-range
  // page and see an empty table.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, customerIdFilter]);

  const loadAcceptances = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string | number> = {
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      };
      if (debouncedSearch.trim()) params.q = debouncedSearch.trim();
      if (customerIdFilter) params.customer_id = customerIdFilter;

      const { data } = await api.get<TosAcceptanceListResponse>(
        '/tos/acceptances',
        { params },
      );
      setItems(data.items);
      setTotal(data.total);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { status?: number } })?.response?.status === 500
          ? 'Server error loading TOS acceptances. Check backend logs.'
          : 'Failed to load TOS acceptances.';
      setError(msg);
      notifications.show({
        title: 'Load failed',
        message: msg,
        color: 'red',
      });
      // eslint-disable-next-line no-console
      console.error('[TOS-LIST] load failed', err);
    } finally {
      setLoading(false);
    }
  }, [page, debouncedSearch, customerIdFilter]);

  useEffect(() => {
    void loadAcceptances();
  }, [loadAcceptances]);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil(total / PAGE_SIZE)),
    [total],
  );

  const clearCustomerFilter = () => {
    const next = new URLSearchParams(searchParams);
    next.delete('customer_id');
    setSearchParams(next);
  };

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <Stack gap="lg">
      <Group justify="space-between" wrap="wrap" align="center">
        <Group gap="sm" align="center">
          <IconFileCertificate size={28} color="#00d4ff" />
          <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
            TOS AUDIT
          </Title>
          <Badge
            color="cyan"
            variant="outline"
            size="sm"
            styles={{ root: { ...monoFont, letterSpacing: '1px' } }}
          >
            {total} ROWS
          </Badge>
        </Group>
        <Tooltip label="Refresh">
          <ActionIcon
            variant="subtle"
            color="cyan"
            size="lg"
            onClick={() => {
              void loadAcceptances();
            }}
            aria-label="Refresh"
          >
            <IconRefresh size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>

      <Card style={cardStyle} radius="md" p="md">
        <Group gap="sm" align="flex-end" wrap="wrap">
          <TextInput
            placeholder="Search email, audit ID, or name…"
            leftSection={<IconSearch size={16} />}
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            styles={{
              ...inputStyles,
              root: { flex: 1, minWidth: 240 },
            }}
            aria-label="Search TOS acceptances"
          />
          {customerIdFilter && (
            <Badge
              size="lg"
              variant="light"
              color="cyan"
              rightSection={
                <ActionIcon
                  size="xs"
                  variant="transparent"
                  color="cyan"
                  onClick={clearCustomerFilter}
                  aria-label="Clear customer filter"
                >
                  <IconX size={12} />
                </ActionIcon>
              }
              styles={{ root: { ...monoFont, letterSpacing: '0.5px' } }}
            >
              CUSTOMER {customerIdFilter.slice(0, 8)}…
            </Badge>
          )}
        </Group>
      </Card>

      <Card style={cardStyle} radius="md" p={0}>
        {loading && items.length === 0 ? (
          <Center py="xl">
            <Loader color="cyan" />
          </Center>
        ) : error ? (
          <Center py="xl">
            <Stack align="center" gap="xs">
              <Text c="red.4" style={monoFont}>
                {error}
              </Text>
              <Button
                size="xs"
                variant="light"
                color="cyan"
                onClick={() => {
                  void loadAcceptances();
                }}
              >
                RETRY
              </Button>
            </Stack>
          </Center>
        ) : items.length === 0 ? (
          <Center py="xl">
            <Stack align="center" gap="xs" px="md">
              <IconFileCertificate size={36} color="#3a4252" />
              <Text c="#5a6478" ta="center" size="sm">
                {debouncedSearch || customerIdFilter
                  ? 'No TOS acceptances match this filter.'
                  : "No TOS acceptances yet — they'll appear here after the first customer signs."}
              </Text>
            </Stack>
          </Center>
        ) : (
          <ScrollArea>
            <Table
              striped
              highlightOnHover
              verticalSpacing="sm"
              horizontalSpacing="md"
              styles={{
                table: { minWidth: 1100 },
                th: {
                  background: '#0a0d14',
                  color: '#5a6478',
                  fontFamily: "'Share Tech Mono', monospace",
                  fontSize: '11px',
                  letterSpacing: '1.5px',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid #1a1f2e',
                },
                td: {
                  color: '#e8edf2',
                  borderBottom: '1px solid #15191f',
                },
              }}
            >
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Accepted At</Table.Th>
                  <Table.Th>Audit ID</Table.Th>
                  <Table.Th>Client</Table.Th>
                  <Table.Th>Email</Table.Th>
                  <Table.Th>Company</Table.Th>
                  <Table.Th>Tpl Ver</Table.Th>
                  <Table.Th>Signed SHA</Table.Th>
                  <Table.Th>Size</Table.Th>
                  <Table.Th>Actions</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {items.map((item) => (
                  <Table.Tr key={item.id}>
                    <Table.Td>
                      <Text size="sm" style={monoFont}>
                        {formatLocalTime(item.accepted_at)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={`Click to copy: ${item.audit_id}`}>
                        <Text
                          size="sm"
                          style={{ ...monoFont, cursor: 'pointer', color: '#00d4ff' }}
                          onClick={() =>
                            copyToClipboard(item.audit_id, 'Audit ID')
                          }
                        >
                          {item.audit_id.length > 24
                            ? `${item.audit_id.slice(0, 24)}…`
                            : item.audit_id}
                        </Text>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{item.client_name}</Text>
                      {item.client_title && (
                        <Text size="xs" c="#5a6478">
                          {item.client_title}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" style={monoFont}>
                        {item.client_email}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c={item.client_company ? '#e8edf2' : '#5a6478'}>
                        {item.client_company || '—'}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge
                        size="xs"
                        color="gray"
                        variant="outline"
                        styles={{ root: { ...monoFont } }}
                      >
                        {item.template_version}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={item.signed_sha256} multiline w={520}>
                        <Text
                          size="sm"
                          style={{
                            ...monoFont,
                            cursor: 'pointer',
                            color: '#7fdbff',
                          }}
                          onClick={() =>
                            copyToClipboard(item.signed_sha256, 'Signed SHA-256')
                          }
                        >
                          {item.signed_sha256.slice(0, 12)}…
                        </Text>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" c="#5a6478" style={monoFont}>
                        {formatBytes(item.signed_pdf_size)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        <Tooltip label="Download signed PDF">
                          <ActionIcon
                            variant="subtle"
                            color="cyan"
                            size="md"
                            component="a"
                            href={item.download_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Download signed PDF for ${item.audit_id}`}
                          >
                            <IconDownload size={16} />
                          </ActionIcon>
                        </Tooltip>
                        {item.customer_id && (
                          <Tooltip label="View customer">
                            <ActionIcon
                              variant="subtle"
                              color="gray"
                              size="md"
                              onClick={() =>
                                navigate(`/customers?id=${item.customer_id}`)
                              }
                              aria-label="View customer"
                            >
                              <IconUser size={16} />
                            </ActionIcon>
                          </Tooltip>
                        )}
                        <Tooltip label="Copy audit ID">
                          <ActionIcon
                            variant="subtle"
                            color="gray"
                            size="md"
                            onClick={() =>
                              copyToClipboard(item.audit_id, 'Audit ID')
                            }
                            aria-label="Copy audit ID"
                          >
                            <IconCopy size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Open by-token download (if intake token present)">
                          <ActionIcon
                            variant="subtle"
                            color="gray"
                            size="md"
                            disabled={!item.intake_token}
                            component="a"
                            href={
                              item.intake_token
                                ? `/api/tos/signed/by-token/${item.intake_token}`
                                : undefined
                            }
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label="Open by-token download"
                          >
                            <IconExternalLink size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Card>

      {totalPages > 1 && (
        <Group justify="space-between" align="center">
          <Text size="xs" c="#5a6478" style={monoFont}>
            SHOWING {(page - 1) * PAGE_SIZE + 1}–
            {Math.min(page * PAGE_SIZE, total)} OF {total}
          </Text>
          <Pagination
            value={page}
            onChange={setPage}
            total={totalPages}
            color="cyan"
            size="sm"
            withEdges
          />
        </Group>
      )}
    </Stack>
  );
}
