/**
 * MissionStatusBadge — shared status pill used across the Mission Hub.
 *
 * Renders a Mantine Badge in the colour matching the lifecycle stage,
 * with a lock icon when status is SENT (per spec §8.5 lockdown).
 *
 * Status colours (per spec §2 Mission Hub redesign):
 *   DRAFT       → grey
 *   SCHEDULED   → blue
 *   IN_PROGRESS → yellow
 *   PROCESSING  → cyan
 *   REVIEW      → purple
 *   DELIVERED   → teal
 *   COMPLETED   → green
 *   SENT        → dark grey + lock icon (final/locked state)
 */
import { Badge } from '@mantine/core';
import { IconLock } from '@tabler/icons-react';

export type MissionStatus =
  | 'draft'
  | 'scheduled'
  | 'in_progress'
  | 'processing'
  | 'review'
  | 'delivered'
  | 'completed'
  | 'sent';

const STATUS_COLOR: Record<MissionStatus, string> = {
  draft: 'gray',
  scheduled: 'blue',
  in_progress: 'yellow',
  processing: 'cyan',
  review: 'violet',
  delivered: 'teal',
  completed: 'green',
  sent: 'dark',
};

interface Props {
  status: string;
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
}

export default function MissionStatusBadge({ status, size = 'md' }: Props) {
  const normalized = (status || 'draft').toLowerCase() as MissionStatus;
  const color = STATUS_COLOR[normalized] ?? 'gray';
  const isSent = normalized === 'sent';

  return (
    <Badge
      color={color}
      variant={isSent ? 'filled' : 'light'}
      size={size}
      leftSection={isSent ? <IconLock size={12} /> : null}
      styles={{
        root: {
          fontFamily: "'Share Tech Mono', monospace",
          letterSpacing: '1px',
        },
      }}
    >
      {normalized.replace(/_/g, ' ').toUpperCase()}
    </Badge>
  );
}
