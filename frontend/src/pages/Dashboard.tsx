import { useEffect, useState } from 'react';
import {
  Card,
  Group,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Badge,
  Button,
  Table,
} from '@mantine/core';
import { IconDrone, IconUsers, IconFileText, IconPlane, IconPlus } from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Mission, Customer } from '../api/types';

function StatCard({ icon: Icon, label, value, color }: { icon: any; label: string; value: string; color: string }) {
  return (
    <Card
      padding="lg"
      radius="md"
      style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}
    >
      <Group>
        <Icon size={32} color={color} />
        <div>
          <Text
            size="xs"
            c="#5a6478"
            style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}
          >
            {label}
          </Text>
          <Text
            size="xl"
            fw={700}
            c="#e8edf2"
            style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '28px' }}
          >
            {value}
          </Text>
        </div>
      </Group>
    </Card>
  );
}

const statusColors: Record<string, string> = {
  draft: 'yellow',
  completed: 'cyan',
  sent: 'green',
};

export default function Dashboard() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [flightCount, setFlightCount] = useState<number | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.get('/missions').then((r) => setMissions(r.data)).catch(() => {});
    api.get('/customers').then((r) => setCustomers(r.data)).catch(() => {});
    api.get('/flights').then((r) => {
      const data = Array.isArray(r.data) ? r.data : r.data?.flights || r.data?.data || r.data?.results || r.data?.items || [];
      setFlightCount(data.length);
    }).catch(() => setFlightCount(0));
  }, []);

  const recentMissions = missions.slice(0, 5);
  const draftCount = missions.filter((m) => m.status === 'draft').length;
  const completedCount = missions.filter((m) => m.status === 'completed' || m.status === 'sent').length;

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>
          DASHBOARD
        </Title>
        <Button
          leftSection={<IconPlus size={16} />}
          color="cyan"
          onClick={() => navigate('/missions/new')}
          styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
        >
          NEW MISSION
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 4 }}>
        <StatCard icon={IconPlane} label="FLIGHTS (ODL)" value={flightCount !== null ? String(flightCount) : '—'} color="#00d4ff" />
        <StatCard icon={IconDrone} label="TOTAL MISSIONS" value={String(missions.length)} color="#00d4ff" />
        <StatCard icon={IconFileText} label="DRAFTS PENDING" value={String(draftCount)} color="#ff6b1a" />
        <StatCard icon={IconUsers} label="CUSTOMERS" value={String(customers.length)} color="#00d4ff" />
      </SimpleGrid>

      <Card
        padding="lg"
        radius="md"
        style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}
      >
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>
          RECENT MISSIONS
        </Title>

        {recentMissions.length === 0 ? (
          <Text c="#5a6478" ta="center" py="xl">
            No missions yet. Create your first mission to get started.
          </Text>
        ) : (
          <Table
            highlightOnHover
            styles={{
              table: { color: '#e8edf2' },
              th: {
                color: '#00d4ff',
                fontFamily: "'Share Tech Mono', monospace",
                fontSize: '13px',
                letterSpacing: '1px',
                borderBottom: '1px solid #1a1f2e',
              },
              td: { borderBottom: '1px solid #1a1f2e' },
              tr: { '&:hover': { backgroundColor: 'rgba(0, 212, 255, 0.05)' } },
            }}
          >
            <Table.Thead>
              <Table.Tr>
                <Table.Th>MISSION</Table.Th>
                <Table.Th>TYPE</Table.Th>
                <Table.Th>DATE</Table.Th>
                <Table.Th>STATUS</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {recentMissions.map((mission) => (
                <Table.Tr
                  key={mission.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/missions/${mission.id}`)}
                >
                  <Table.Td>{mission.title}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c="#5a6478" tt="capitalize">
                      {mission.mission_type.replace('_', ' ')}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {mission.mission_date || '—'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={statusColors[mission.status] || 'gray'} variant="light" size="sm">
                      {mission.status}
                    </Badge>
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
