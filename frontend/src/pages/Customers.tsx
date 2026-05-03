import { useEffect, useState, useRef, useCallback } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
  Tooltip,
  Popover,
  Loader,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconPlus, IconEdit, IconTrash, IconSearch, IconMapPin, IconSend, IconCheck, IconCopy, IconSignature, IconMail } from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Customer, NominatimResult } from '../api/types';
import PdfViewer from '../components/PDFPreview/PdfViewer';
import { inputStyles } from '../components/shared/styles';

export default function Customers() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  // Initiate Services modal
  const [initiateModalOpen, setInitiateModalOpen] = useState(false);
  const [initiateEmail, setInitiateEmail] = useState('');
  const [initiateLoading, setInitiateLoading] = useState(false);
  const [intakeResult, setIntakeResult] = useState<{ intake_url: string; customer_id?: string } | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const linkInputRef = useRef<HTMLInputElement>(null);

  const copyIntakeLink = async () => {
    if (!intakeResult) return;
    try {
      await navigator.clipboard.writeText(intakeResult.intake_url);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      // Fallback for older browsers or insecure contexts
      const input = linkInputRef.current;
      if (input) {
        input.focus();
        input.select();
        try {
          document.execCommand('copy');
          setLinkCopied(true);
          setTimeout(() => setLinkCopied(false), 2000);
        } catch {
          notifications.show({ title: 'Copy failed', message: 'Please select the link and copy manually (Ctrl+C / Cmd+C).', color: 'orange' });
        }
      }
    }
  };

  // Signed TOS viewer
  const [signatureModal, setSignatureModal] = useState(false);
  const [viewingCustomer, setViewingCustomer] = useState<Customer | null>(null);
  const [signedTosBlobUrl, setSignedTosBlobUrl] = useState<string | null>(null);
  const [signedTosLoading, setSignedTosLoading] = useState(false);

  // Clean up signed TOS blob URL on unmount
  useEffect(() => {
    return () => {
      if (signedTosBlobUrl) URL.revokeObjectURL(signedTosBlobUrl);
    };
  }, [signedTosBlobUrl]);

  const formatPhone = (value: string) => {
    const digits = value.replace(/\D/g, '').slice(0, 10);
    if (digits.length <= 3) return digits;
    if (digits.length <= 6) return `${digits.slice(0, 3)}-${digits.slice(3)}`;
    return `${digits.slice(0, 3)}-${digits.slice(3, 6)}-${digits.slice(6)}`;
  };

  const form = useForm({
    initialValues: { name: '', email: '', phone: '', address: '', city: '', state: '', zip_code: '', company: '', notes: '' },
  });

  const [addressSuggestions, setAddressSuggestions] = useState<NominatimResult[]>([]);
  const [addressLoading, setAddressLoading] = useState(false);
  const [addressPopover, setAddressPopover] = useState(false);
  const addressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const searchAddress = useCallback((query: string) => {
    if (addressTimerRef.current) clearTimeout(addressTimerRef.current);
    if (query.length < 4) { setAddressSuggestions([]); setAddressPopover(false); return; }
    addressTimerRef.current = setTimeout(async () => {
      setAddressLoading(true);
      try {
        const resp = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&limit=5&countrycodes=us&q=${encodeURIComponent(query)}`,
          { headers: { 'Accept-Language': 'en' } }
        );
        const data: NominatimResult[] = await resp.json();
        setAddressSuggestions(data);
        setAddressPopover(data.length > 0);
      } catch {
        setAddressSuggestions([]);
      } finally {
        setAddressLoading(false);
      }
    }, 400);
  }, []);

  const selectAddressSuggestion = (result: NominatimResult) => {
    const addr = result.address;
    if (addr) {
      const street = [addr.house_number, addr.road].filter(Boolean).join(' ');
      if (street) form.setFieldValue('address', street);
      form.setFieldValue('city', addr.city || addr.town || addr.village || '');
      form.setFieldValue('state', addr.state || '');
      form.setFieldValue('zip_code', addr.postcode || '');
    } else {
      form.setFieldValue('address', result.display_name);
    }
    setAddressPopover(false);
  };

  const loadCustomers = () => {
    api.get('/customers').then((r) => setCustomers(r.data)).catch(() => setCustomers([]));
  };

  useEffect(() => { loadCustomers(); }, []);

  const handleSubmit = async (values: typeof form.values) => {
    try {
      const payload = { ...values, phone: values.phone.replace(/\D/g, '') || null };
      if (editingId) {
        await api.put(`/customers/${editingId}`, payload);
        notifications.show({ title: 'Updated', message: 'Customer updated', color: 'cyan' });
      } else {
        await api.post('/customers', payload);
        notifications.show({ title: 'Created', message: 'Customer created', color: 'cyan' });
      }
      setModalOpen(false);
      setEditingId(null);
      form.reset();
      loadCustomers();
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save customer', color: 'red' });
    }
  };

  const handleEdit = (customer: Customer) => {
    setEditingId(customer.id);
    form.setValues({
      name: customer.name,
      email: customer.email || '',
      phone: formatPhone(customer.phone || ''),
      address: customer.address || '',
      city: customer.city || '',
      state: customer.state || '',
      zip_code: customer.zip_code || '',
      company: customer.company || '',
      notes: customer.notes || '',
    });
    setModalOpen(true);
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this customer?')) return;
    try {
      await api.delete(`/customers/${id}`);
      loadCustomers();
      notifications.show({ title: 'Deleted', message: 'Customer deleted', color: 'orange' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to delete', color: 'red' });
    }
  };

  const handleInitiateServices = async () => {
    setInitiateLoading(true);
    try {
      // Email is optional — empty string => stub customer with no email,
      // operator copies the link for SMS/text instead of emailing it.
      const email = initiateEmail.trim();
      const r = await api.post('/intake/initiate', email ? { email } : {});
      setIntakeResult(r.data);
      loadCustomers();
      notifications.show({ title: 'Link Generated', message: 'Intake form link ready', color: 'cyan' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to generate link', color: 'red' });
    } finally {
      setInitiateLoading(false);
    }
  };

  const handleSendTos = async (customer: Customer) => {
    if (!customer.email) {
      notifications.show({ title: 'Error', message: 'Customer needs an email address first', color: 'red' });
      return;
    }
    try {
      await api.post(`/intake/${customer.id}/send-email`);
      loadCustomers();
      notifications.show({ title: 'Sent', message: `TOS form sent to ${customer.email}`, color: 'cyan' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to send', color: 'red' });
    }
  };

  const handleSendIntakeEmail = async () => {
    if (!intakeResult) return;
    if (!intakeResult.customer_id) {
      notifications.show({ title: 'Error', message: 'No customer associated with this intake', color: 'red' });
      return;
    }
    try {
      await api.post(`/intake/${intakeResult.customer_id}/send-email`);
      notifications.show({ title: 'Sent', message: 'Intake email sent', color: 'green' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to send email', color: 'red' });
    }
  };

  const renderTosStatus = (c: Customer) => {
    if (c.tos_signed) {
      return (
        <Tooltip label={`Signed ${c.tos_signed_at ? new Date(c.tos_signed_at).toLocaleDateString() : ''}`}>
          <Badge
            color="green"
            variant="light"
            size="sm"
            leftSection={<IconCheck size={10} />}
            style={{ cursor: 'pointer' }}
            onClick={() => {
              setViewingCustomer(c);
              setSignedTosBlobUrl(null);
              setSignedTosLoading(true);
              setSignatureModal(true);
              api.get(`/intake/${c.id}/signed-tos`, { responseType: 'blob' })
                .then((r) => {
                  const blob = new Blob([r.data], { type: 'application/pdf' });
                  setSignedTosBlobUrl(URL.createObjectURL(blob));
                })
                .catch(() => {
                  notifications.show({ title: 'Error', message: 'Could not load signed TOS document.', color: 'red' });
                })
                .finally(() => setSignedTosLoading(false));
            }}
          >
            TOS SIGNED
          </Badge>
        </Tooltip>
      );
    }
    if (c.intake_token && !c.intake_completed_at) {
      return <Badge color="yellow" variant="light" size="sm">PENDING</Badge>;
    }
    return <Text c="#5a6478" size="sm">—</Text>;
  };

  const filtered = customers.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      (c.company || '').toLowerCase().includes(search.toLowerCase()) ||
      (c.email || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between" wrap="wrap">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>CUSTOMERS</Title>
        <Group gap="xs">
          <Button
            leftSection={<IconSend size={16} />}
            color="cyan"
            variant="light"
            onClick={() => { setInitiateEmail(''); setIntakeResult(null); setInitiateModalOpen(true); }}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            INITIATE SERVICES
          </Button>
          <Button
            leftSection={<IconPlus size={16} />}
            color="cyan"
            onClick={() => { setEditingId(null); form.reset(); setModalOpen(true); }}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            ADD CUSTOMER
          </Button>
        </Group>
      </Group>

      <TextInput
        placeholder="Search customers..."
        leftSection={<IconSearch size={16} />}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        styles={inputStyles}
      />

      <Card padding="lg" radius="md" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
        {filtered.length === 0 ? (
          <Text c="#5a6478" ta="center" py="xl">No customers found.</Text>
        ) : (
          <ScrollArea type="auto">
          <Table highlightOnHover styles={{
            table: { color: '#e8edf2', minWidth: 400 },
            th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e' },
            td: { borderBottom: '1px solid #1a1f2e' },
          }}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>NAME</Table.Th>
                <Table.Th className="hide-mobile">COMPANY</Table.Th>
                <Table.Th className="hide-mobile">EMAIL</Table.Th>
                <Table.Th className="hide-mobile">PHONE</Table.Th>
                <Table.Th>TOS</Table.Th>
                <Table.Th>ACTIONS</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filtered.map((c) => (
                <Table.Tr key={c.id}>
                  <Table.Td fw={600}>{c.name}</Table.Td>
                  <Table.Td className="hide-mobile" c="#5a6478">{c.company || '—'}</Table.Td>
                  <Table.Td className="hide-mobile" c="#5a6478">{c.email || '—'}</Table.Td>
                  <Table.Td className="hide-mobile" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>{c.phone ? formatPhone(c.phone) : '—'}</Table.Td>
                  <Table.Td>{renderTosStatus(c)}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      {!c.tos_signed && c.email && (
                        <Tooltip label="Send TOS Form">
                          <ActionIcon variant="subtle" color="orange" onClick={() => handleSendTos(c)} aria-label={`Send TOS form to ${c.name}`}>
                            <IconSignature size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      <Tooltip label="Edit">
                        <ActionIcon variant="subtle" color="cyan" onClick={() => handleEdit(c)} aria-label={`Edit customer: ${c.name}`}>
                          <IconEdit size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Delete">
                        <ActionIcon variant="subtle" color="red" onClick={() => handleDelete(c.id)} aria-label={`Delete customer: ${c.name}`}>
                          <IconTrash size={16} />
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

      {/* Add/Edit Customer Modal */}
      <Modal
        opened={modalOpen}
        onClose={() => { setModalOpen(false); setEditingId(null); }}
        title={editingId ? 'Edit Customer' : 'New Customer'}
        styles={{
          header: { background: '#0e1117' },
          content: { background: '#0e1117' },
          title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' },
        }}
      >
        <form onSubmit={form.onSubmit(handleSubmit)}>
          <Stack gap="sm">
            <TextInput label="Name" required {...form.getInputProps('name')} styles={inputStyles} />
            <TextInput label="Email" {...form.getInputProps('email')} styles={inputStyles} />
            <TextInput label="Phone" placeholder="xxx-xxx-xxxx" value={form.values.phone} onChange={(e) => form.setFieldValue('phone', formatPhone(e.target.value))} styles={inputStyles} />
            <TextInput label="Company" {...form.getInputProps('company')} styles={inputStyles} />
            <Popover opened={addressPopover} onClose={() => setAddressPopover(false)} position="bottom-start" width="target">
              <Popover.Target>
                <TextInput
                  label="Street Address"
                  leftSection={addressLoading ? <Loader size={14} color="cyan" /> : <IconMapPin size={14} />}
                  {...form.getInputProps('address')}
                  onChange={(e) => {
                    form.getInputProps('address').onChange(e);
                    searchAddress(e.target.value);
                  }}
                  onFocus={() => { if (addressSuggestions.length > 0) setAddressPopover(true); }}
                  styles={inputStyles}
                />
              </Popover.Target>
              <Popover.Dropdown style={{ background: '#0e1117', border: '1px solid #1a1f2e', padding: 0, maxHeight: 200, overflow: 'auto' }}>
                {addressSuggestions.map((s, i) => (
                  <Text
                    key={i}
                    size="sm"
                    c="#e8edf2"
                    p="xs"
                    style={{ cursor: 'pointer', borderBottom: '1px solid #1a1f2e' }}
                    onMouseDown={(e) => { e.preventDefault(); }}
                    onClick={() => selectAddressSuggestion(s)}
                  >
                    {s.display_name}
                  </Text>
                ))}
              </Popover.Dropdown>
            </Popover>
            <SimpleGrid cols={{ base: 1, xs: 3 }}>
              <TextInput label="City" {...form.getInputProps('city')} styles={inputStyles} />
              <TextInput label="State" {...form.getInputProps('state')} styles={inputStyles} />
              <TextInput label="Zip Code" {...form.getInputProps('zip_code')} styles={inputStyles} />
            </SimpleGrid>
            <Textarea label="Notes" {...form.getInputProps('notes')} styles={inputStyles} />
            <Button type="submit" color="cyan" fullWidth styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
              {editingId ? 'UPDATE' : 'CREATE'}
            </Button>
          </Stack>
        </form>
      </Modal>

      {/* Initiate Services Modal */}
      <Modal
        opened={initiateModalOpen}
        onClose={() => setInitiateModalOpen(false)}
        title="Initiate Services"
        styles={{
          header: { background: '#0e1117' },
          content: { background: '#0e1117' },
          title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' },
        }}
      >
        {!intakeResult ? (
          <Stack gap="md">
            <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Enter the customer's email to send the onboarding link, or leave blank to generate a copyable link you can text.
            </Text>
            <TextInput
              label="Customer Email (optional)"
              placeholder="customer@example.com — or leave blank"
              value={initiateEmail}
              onChange={(e) => setInitiateEmail(e.target.value)}
              styles={inputStyles}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleInitiateServices(); } }}
            />
            <Button
              color="cyan"
              fullWidth
              loading={initiateLoading}
              onClick={handleInitiateServices}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              GENERATE INTAKE LINK
            </Button>
          </Stack>
        ) : (
          <Stack gap="md">
            <Badge color="green" variant="light" size="lg" leftSection={<IconCheck size={12} />}>
              LINK GENERATED
            </Badge>
            <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
              INTAKE LINK
            </Text>
            <Group gap="xs">
              <TextInput
                ref={linkInputRef}
                value={intakeResult.intake_url}
                readOnly
                onClick={(e) => (e.target as HTMLInputElement).select()}
                style={{ flex: 1 }}
                styles={inputStyles}
              />
              <Tooltip label={linkCopied ? 'Copied!' : 'Copy to clipboard'}>
                <ActionIcon color={linkCopied ? 'green' : 'cyan'} variant="light" onClick={copyIntakeLink} aria-label="Copy link">
                  {linkCopied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                </ActionIcon>
              </Tooltip>
            </Group>
            <Button
              leftSection={linkCopied ? <IconCheck size={16} /> : <IconCopy size={16} />}
              color={linkCopied ? 'green' : 'cyan'}
              fullWidth
              onClick={copyIntakeLink}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
            >
              {linkCopied ? 'COPIED — PASTE INTO TEXT MESSAGE' : 'COPY LINK'}
            </Button>
            {initiateEmail.trim() && (
              <Button
                leftSection={<IconMail size={16} />}
                color="cyan"
                variant="light"
                fullWidth
                onClick={handleSendIntakeEmail}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                SEND VIA EMAIL
              </Button>
            )}
            <Text c="#5a6478" size="xs" ta="center" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Link expires in 7 days
            </Text>
          </Stack>
        )}
      </Modal>

      {/* Signed TOS Viewer Modal */}
      <Modal
        opened={signatureModal}
        onClose={() => {
          setSignatureModal(false);
          setViewingCustomer(null);
          if (signedTosBlobUrl) { URL.revokeObjectURL(signedTosBlobUrl); setSignedTosBlobUrl(null); }
        }}
        title="Signed Terms of Service"
        size="xl"
        styles={{
          header: { background: '#0e1117' },
          content: { background: '#0e1117' },
          title: { color: '#e8edf2', fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' },
        }}
      >
        {viewingCustomer && (
          <Stack gap="md">
            <Group justify="space-between">
              <Group>
                <Badge color="green" variant="light" leftSection={<IconCheck size={10} />}>TOS SIGNED</Badge>
                {viewingCustomer.tos_signed_at && (
                  <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                    {new Date(viewingCustomer.tos_signed_at).toLocaleString()}
                  </Text>
                )}
              </Group>
            </Group>

            {signedTosLoading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 60 }}>
                <Loader color="cyan" size="md" />
                <Text c="#5a6478" size="sm" ml="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  Generating signed document...
                </Text>
              </div>
            ) : signedTosBlobUrl ? (
              <PdfViewer
                url={signedTosBlobUrl}
                height={600}
                downloadFilename={`TOS_Signed_${(viewingCustomer.name || 'customer').replace(/\s+/g, '_')}.pdf`}
              />
            ) : (
              <Text c="#5a6478" size="sm" ta="center" py={40}>
                Could not load the signed TOS document.
              </Text>
            )}
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
