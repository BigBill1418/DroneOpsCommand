import { Card, Group, Text, Stack, Badge, Table } from '@mantine/core';
import { IconDrone } from '@tabler/icons-react';
import { Aircraft } from '../../api/types';

interface AircraftCardProps {
  aircraft: Aircraft;
  compact?: boolean;
}

export default function AircraftCard({ aircraft, compact = false }: AircraftCardProps) {
  const specEntries = Object.entries(aircraft.specs || {});

  if (compact) {
    return (
      <Badge
        variant="outline"
        color="cyan"
        size="lg"
        leftSection={<IconDrone size={14} />}
        styles={{ root: { background: '#0e1117' } }}
      >
        {aircraft.model_name}
      </Badge>
    );
  }

  return (
    <Card
      padding="md"
      radius="md"
      style={{ background: '#0e1117', border: '1px solid #1a1f2e', flex: 1, minWidth: 250 }}
    >
      <Stack gap="xs" align="center">
        <IconDrone size={48} color="#00d4ff" />
        <Text
          size="lg"
          fw={700}
          c="#e8edf2"
          style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}
        >
          {aircraft.model_name}
        </Text>
        <Text
          size="xs"
          c="#5a6478"
          style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}
        >
          {aircraft.manufacturer}
        </Text>

        {specEntries.length > 0 && (
          <Table
            styles={{
              table: { color: '#e8edf2' },
              td: { borderBottom: '1px solid #1a1f2e', padding: '4px 6px', fontSize: '12px' },
            }}
          >
            <Table.Tbody>
              {specEntries.slice(0, 6).map(([key, value]) => (
                <Table.Tr key={key}>
                  <Table.Td
                    c="#5a6478"
                    style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '12px', textTransform: 'uppercase' }}
                  >
                    {key.replace(/_/g, ' ')}
                  </Table.Td>
                  <Table.Td>{String(value)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  );
}
