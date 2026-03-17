import { Card, Group, Text } from '@mantine/core';
import type { Icon as TablerIcon } from '@tabler/icons-react';

interface StatCardProps {
  icon: TablerIcon;
  label: string;
  value: string;
  sub?: string;
  color?: string;
}

export default function StatCard({ icon: Icon, label, value, sub, color = '#00d4ff' }: StatCardProps) {
  return (
    <Card padding="md" radius="md" style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
      <Group gap="sm" wrap="nowrap">
        <Icon size={22} color={color} style={{ flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <Text size="11px" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }} tt="uppercase">
            {label}
          </Text>
          <Text fw={700} c="#e8edf2" style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '26px', lineHeight: 1.1 }}>
            {value}
          </Text>
          {sub && (
            <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>{sub}</Text>
          )}
        </div>
      </Group>
    </Card>
  );
}
