/**
 * UnsavedChangesModal — confirm prompt rendered by facet editors when
 * the operator tries to navigate away from a dirty form (v2.67.3
 * polish). Uses operator-side brand: cyan #00d4ff for the safe
 * default ("Keep editing"), red for the destructive ("Discard").
 *
 * Each editor renders its own instance — the JSX is small and lets
 * the editor pass per-context body copy if needed (the default copy
 * matches every facet editor's case today).
 */
import { Button, Group, Modal, Stack, Text } from '@mantine/core';

interface Props {
  opened: boolean;
  onKeepEditing: () => void;
  onDiscard: () => void;
  /** Override the default body if the facet wants editor-specific copy. */
  body?: string;
}

const DEFAULT_BODY =
  "Your edits to this section haven't been saved. Discard and return to the mission?";

export default function UnsavedChangesModal({
  opened,
  onKeepEditing,
  onDiscard,
  body,
}: Props) {
  return (
    <Modal
      opened={opened}
      onClose={onKeepEditing}
      title="Discard unsaved changes?"
      centered
      size="sm"
      // Keep z-index above the editor's own dropdowns / autosuggest.
      zIndex={2000}
      styles={{
        title: {
          fontFamily: "'Bebas Neue', sans-serif",
          letterSpacing: '2px',
          color: '#e8edf2',
        },
        content: { background: '#0e1117', border: '1px solid #1a1f2e' },
        header: { background: '#0e1117', borderBottom: '1px solid #1a1f2e' },
      }}
    >
      <Stack gap="md">
        <Text c="#e8edf2" size="sm">
          {body ?? DEFAULT_BODY}
        </Text>
        <Group justify="flex-end" gap="sm">
          <Button
            color="red"
            variant="light"
            onClick={onDiscard}
            styles={{
              root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' },
            }}
          >
            DISCARD CHANGES
          </Button>
          <Button
            color="cyan"
            onClick={onKeepEditing}
            autoFocus
            styles={{
              root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' },
            }}
          >
            KEEP EDITING
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
