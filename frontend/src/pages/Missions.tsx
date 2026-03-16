import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconEdit, IconPlus, IconSearch } from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Mission } from '../api/types';

const statusColors: Record<string, string> = {
  draft: 'yellow',
  completed: 'cyan',
  sent: 'green',
};

export default function Missions() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    api.get('/missions').then((r) => setMissions(r.data)).catch(() => {});
  }, []);

  const filtered = missions.filter(
    (m) =>
      m.title.toLowerCase().includes(search.toLowerCase()) ||
      m.mission_type.toLowerCase().includes(search.toLowerCase()) ||
      (m.location_name || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>MISSIONS</Title>
        <Button
          leftSection={<IconPlus size={16} />}
          color="cyan"
          onClick={() => navigate('/missions/new')}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          NEW MISSION
        </Button>
      </Group>

      <TextInput
        placeholder="Search missions..."
        leftSection={<IconSearch size={16} />}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        styles={{
          input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
        }}
      />

      <Card padding="lg" radius="md" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
        {filtered.length === 0 ? (
          <Text c="#5a6478" ta="center" py="xl">No missions found.</Text>
        ) : (
          <Table highlightOnHover styles={{
            table: { color: '#e8edf2' },
            th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e' },
            td: { borderBottom: '1px solid #1a1f2e' },
          }}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>MISSION</Table.Th>
                <Table.Th>TYPE</Table.Th>
                <Table.Th>LOCATION</Table.Th>
                <Table.Th>DATE</Table.Th>
                <Table.Th>STATUS</Table.Th>
                <Table.Th>BILLABLE</Table.Th>
                <Table.Th w={50}></Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filtered.map((m) => (
                <Table.Tr key={m.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/missions/${m.id}`)}>
                  <Table.Td fw={600}>{m.title}</Table.Td>
                  <Table.Td c="#5a6478" tt="capitalize">{m.mission_type.replace('_', ' ')}</Table.Td>
                  <Table.Td c="#5a6478">{m.location_name || '—'}</Table.Td>
                  <Table.Td c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>{m.mission_date || '—'}</Table.Td>
                  <Table.Td><Badge color={statusColors[m.status]} variant="light" size="sm">{m.status}</Badge></Table.Td>
                  <Table.Td>{m.is_billable ? <Badge color="orange" variant="light" size="sm">$</Badge> : '—'}</Table.Td>
                  <Table.Td>
                    <ActionIcon
                      variant="subtle"
                      color="cyan"
                      size="sm"
                      onClick={(e) => { e.stopPropagation(); navigate(`/missions/${m.id}/edit`); }}
                      title="Edit mission"
                    >
                      <IconEdit size={14} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </Stack>
  );
}
