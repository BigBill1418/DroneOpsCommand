import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPlane, IconPlus, IconTool } from '@tabler/icons-react';
import api from '../../api/client';
import { cardStyle } from '../../components/shared/styles';

/**
 * MAINTENANCE tab — per-aircraft maintenance schedules + seed defaults.
 * Fetches /maintenance/status on mount. Seed payload unchanged.
 */
export default function MaintenanceTab() {
  const [maintenanceStatus, setMaintenanceStatus] = useState<any[]>([]);
  const [maintenanceLoading, setMaintenanceLoading] = useState(false);
  const [seedingDefaults, setSeedingDefaults] = useState<string | null>(null);

  useEffect(() => {
    setMaintenanceLoading(true);
    api.get('/maintenance/status').then((r) => setMaintenanceStatus(Array.isArray(r.data) ? r.data : [])).catch(() => {}).finally(() => setMaintenanceLoading(false));
  }, []);

  const handleSeedDefaults = async (aircraftId: string) => {
    setSeedingDefaults(aircraftId);
    try {
      const resp = await api.post('/maintenance/seed-defaults', { aircraft_id: aircraftId });
      notifications.show({ title: 'Seeded', message: resp.data.message, color: 'cyan' });
      api.get('/maintenance/status').then((r) => setMaintenanceStatus(Array.isArray(r.data) ? r.data : []));
    } catch (err: any) {
      notifications.show({ title: 'Error', message: err.response?.data?.detail || 'Failed to seed defaults', color: 'red' });
    } finally {
      setSeedingDefaults(null);
    }
  };

  return (
    <Stack gap="md">
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconTool size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>MAINTENANCE STATUS</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Industry-standard DJI maintenance intervals. Seed defaults per aircraft, then track service records.
        </Text>

        {maintenanceLoading ? (
          <Group justify="center" py="xl"><Loader color="cyan" size="sm" /></Group>
        ) : maintenanceStatus.length === 0 ? (
          <Text c="#5a6478" size="sm" ta="center" py="md">No aircraft in fleet — add aircraft in Fleet & Rates tab first</Text>
        ) : (
          <Stack gap="lg">
            {maintenanceStatus.map((ac: any) => (
              <Card key={ac.aircraft_id} padding="md" radius="sm" style={{ background: '#0a0d12', border: '1px solid #1a1f2e' }}>
                <Group justify="space-between" mb="sm">
                  <Group gap="sm">
                    <IconPlane size={16} color="#00d4ff" />
                    <Text fw={600} c="#e8edf2" size="sm">{ac.aircraft_name || 'Unknown Aircraft'}</Text>
                    <Badge size="xs" variant="light" color={ac.overall_status === 'overdue' ? 'red' : ac.overall_status === 'due_soon' ? 'yellow' : 'green'}>
                      {(ac.overall_status || 'ok').toUpperCase().replace('_', ' ')}
                    </Badge>
                  </Group>
                  <Group gap="xs">
                    <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {(ac.total_flight_hours ?? 0).toFixed(1)}h total
                    </Text>
                    {(ac.schedules?.length ?? 0) === 0 && (
                      <Button
                        size="xs"
                        variant="light"
                        color="cyan"
                        leftSection={<IconPlus size={12} />}
                        loading={seedingDefaults === ac.aircraft_id}
                        onClick={() => handleSeedDefaults(ac.aircraft_id)}
                      >
                        Seed Defaults
                      </Button>
                    )}
                  </Group>
                </Group>

                {(ac.schedules?.length ?? 0) > 0 && (
                  <Table styles={{
                    table: { color: '#e8edf2' },
                    th: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', borderBottom: '1px solid #1a1f2e', padding: '4px 8px' },
                    td: { borderBottom: '1px solid #0e1117', fontFamily: "'Share Tech Mono', monospace", fontSize: '12px', padding: '4px 8px' },
                  }}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>ITEM</Table.Th>
                        <Table.Th>INTERVAL</Table.Th>
                        <Table.Th>HOURS SINCE</Table.Th>
                        <Table.Th>REMAINING</Table.Th>
                        <Table.Th>STATUS</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {(ac.schedules || []).map((s: any) => (
                        <Table.Tr key={s.schedule_id}>
                          <Table.Td>{s.maintenance_type}</Table.Td>
                          <Table.Td>
                            {s.interval_hours ? `${s.interval_hours}h` : ''}
                            {s.interval_hours && s.interval_days ? ' / ' : ''}
                            {s.interval_days ? `${s.interval_days}d` : ''}
                          </Table.Td>
                          <Table.Td>{s.hours_since_maintenance != null ? `${s.hours_since_maintenance.toFixed(1)}h` : '—'}</Table.Td>
                          <Table.Td>
                            {s.hours_remaining != null ? (
                              <Text c={s.hours_remaining < 0 ? '#ff4444' : s.hours_remaining < 20 ? '#ff6b1a' : '#4ade80'} size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                                {s.hours_remaining.toFixed(1)}h
                              </Text>
                            ) : s.days_remaining != null ? (
                              <Text c={s.days_remaining < 0 ? '#ff4444' : s.days_remaining < 7 ? '#ff6b1a' : '#4ade80'} size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                                {s.days_remaining}d
                              </Text>
                            ) : '—'}
                          </Table.Td>
                          <Table.Td>
                            <Badge size="xs" variant="light" color={s.status === 'overdue' ? 'red' : s.status === 'due_soon' ? 'yellow' : 'green'}>
                              {(s.status || 'ok').toUpperCase().replace('_', ' ')}
                            </Badge>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                )}
              </Card>
            ))}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
