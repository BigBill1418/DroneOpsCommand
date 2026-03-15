import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconPlus, IconEdit, IconTrash, IconSearch } from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Customer } from '../api/types';

const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
};

export default function Customers() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  const form = useForm({
    initialValues: { name: '', email: '', phone: '', address: '', company: '', notes: '' },
  });

  const loadCustomers = () => {
    api.get('/customers').then((r) => setCustomers(r.data)).catch(() => {});
  };

  useEffect(() => { loadCustomers(); }, []);

  const handleSubmit = async (values: typeof form.values) => {
    try {
      if (editingId) {
        await api.put(`/customers/${editingId}`, values);
        notifications.show({ title: 'Updated', message: 'Customer updated', color: 'cyan' });
      } else {
        await api.post('/customers', values);
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
      phone: customer.phone || '',
      address: customer.address || '',
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

  const filtered = customers.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      (c.company || '').toLowerCase().includes(search.toLowerCase()) ||
      (c.email || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>CUSTOMERS</Title>
        <Button
          leftSection={<IconPlus size={16} />}
          color="cyan"
          onClick={() => { setEditingId(null); form.reset(); setModalOpen(true); }}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          ADD CUSTOMER
        </Button>
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
          <Table highlightOnHover styles={{
            table: { color: '#e8edf2' },
            th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e' },
            td: { borderBottom: '1px solid #1a1f2e' },
          }}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>NAME</Table.Th>
                <Table.Th>COMPANY</Table.Th>
                <Table.Th>EMAIL</Table.Th>
                <Table.Th>PHONE</Table.Th>
                <Table.Th>ACTIONS</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filtered.map((c) => (
                <Table.Tr key={c.id}>
                  <Table.Td fw={600}>{c.name}</Table.Td>
                  <Table.Td c="#5a6478">{c.company || '—'}</Table.Td>
                  <Table.Td c="#5a6478">{c.email || '—'}</Table.Td>
                  <Table.Td c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>{c.phone || '—'}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon variant="subtle" color="cyan" onClick={() => handleEdit(c)}>
                        <IconEdit size={16} />
                      </ActionIcon>
                      <ActionIcon variant="subtle" color="red" onClick={() => handleDelete(c.id)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

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
            <TextInput label="Phone" {...form.getInputProps('phone')} styles={inputStyles} />
            <TextInput label="Company" {...form.getInputProps('company')} styles={inputStyles} />
            <Textarea label="Address" {...form.getInputProps('address')} styles={inputStyles} />
            <Textarea label="Notes" {...form.getInputProps('notes')} styles={inputStyles} />
            <Button type="submit" color="cyan" fullWidth styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
              {editingId ? 'UPDATE' : 'CREATE'}
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
