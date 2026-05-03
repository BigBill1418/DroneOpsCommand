/**
 * MissionFacetCard — shared "section card with title + summary + Edit
 * button" used on the Mission Hub.
 *
 * Each card shows a single facet (Details, Flights, Images, Report,
 * Invoice). Clicking Edit navigates to a focused per-facet editor; the
 * Hub itself never writes mission data.
 *
 * When `disabled` is true (e.g. mission status === 'sent', per spec
 * §8.5), the Edit button is shown but disabled with a "Mission sent
 * — locked" tooltip.
 *
 * `extraActions` is the slot used by the Invoice card (per spec §8.6)
 * to render Issue Portal Link / Send Email / Copy Link buttons inline
 * with the Edit action.
 */
import { Card, Group, Stack, Text, Button, Tooltip } from '@mantine/core';
import { IconEdit } from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import type { ReactNode } from 'react';

interface Props {
  title: string;
  summary: ReactNode;
  editPath: string;
  disabled?: boolean;
  extraActions?: ReactNode;
}

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

export default function MissionFacetCard({
  title,
  summary,
  editPath,
  disabled = false,
  extraActions,
}: Props) {
  const navigate = useNavigate();

  const editButton = (
    <Button
      leftSection={<IconEdit size={14} />}
      color="cyan"
      variant="light"
      size="sm"
      disabled={disabled}
      onClick={() => navigate(editPath)}
      styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
      aria-label={`Edit ${title}`}
    >
      EDIT
    </Button>
  );

  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
        <Stack gap={6} style={{ flex: 1, minWidth: 0 }}>
          <Text
            c="#00d4ff"
            size="sm"
            fw={700}
            style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
          >
            {title.toUpperCase()}
          </Text>
          <div>{summary}</div>
        </Stack>
        <Group gap="xs" wrap="nowrap">
          {extraActions}
          {disabled ? (
            <Tooltip label="Mission sent — locked" withArrow>
              <span>{editButton}</span>
            </Tooltip>
          ) : (
            editButton
          )}
        </Group>
      </Group>
    </Card>
  );
}
