/** ADR-0050 — mission Hub: link back to the website lead + won write-back status. */
import { useEffect, useState } from 'react';
import { Anchor, Button, Group, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { fetchLeadsEnabled, LEAD_KEY_RE, leadPortalUrl, retryLeadWon } from '../api/leads';

interface Props {
  missionId: string;
  sourceRef: string | null | undefined;
  leadWritebackAt: string | null | undefined;
  onRetried: () => void;
}

export default function MissionLeadStatus({ missionId, sourceRef, leadWritebackAt, onRetried }: Props) {
  const [busy, setBusy] = useState(false);
  // Retry can only work where the integration is configured (LD-2 M-8).
  const [enabled, setEnabled] = useState(false);
  const isLead = !!sourceRef && LEAD_KEY_RE.test(sourceRef);
  useEffect(() => {
    if (isLead) fetchLeadsEnabled().then(setEnabled);
  }, [isLead]);
  if (!sourceRef || !isLead) return null;

  const retry = async () => {
    setBusy(true);
    try {
      await retryLeadWon(missionId);
      notifications.show({ title: 'Lead updated', message: 'Marked won in the marketing pipeline', color: 'cyan' });
      onRetried();
    } catch {
      notifications.show({ title: 'Still not marked', message: 'Marketing API unavailable — try again later', color: 'red' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Group gap="xs" wrap="wrap" data-testid="mission-lead-status">
      <Anchor href={leadPortalUrl(sourceRef)} target="_blank" rel="noopener noreferrer" size="xs">
        View lead
      </Anchor>
      {!leadWritebackAt && enabled && (
        <>
          <Text c="yellow" size="xs">Lead not marked won</Text>
          <Button size="compact-xs" variant="light" color="yellow" loading={busy} onClick={retry}>
            Retry
          </Button>
        </>
      )}
    </Group>
  );
}
