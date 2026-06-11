import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Button,
  Card,
  Group,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconDrone, IconKey, IconPlus, IconTrash } from '@tabler/icons-react';
import api from '../../api/client';
import { DeviceKey } from '../../api/types';
import { inputStyles, cardStyle } from '../../components/shared/styles';

/**
 * DEVICE ACCESS tab — DroneOpsSync API keys. Fetches /settings/device-keys on
 * mount. Create/revoke flows and the once-shown raw key UX are unchanged.
 */
export default function DevicesTab() {
  const [deviceKeys, setDeviceKeys] = useState<DeviceKey[]>([]);
  const [deviceKeyCreating, setDeviceKeyCreating] = useState(false);
  const [deviceKeyLabel, setDeviceKeyLabel] = useState('');
  const [newDeviceKey, setNewDeviceKey] = useState<string | null>(null);

  useEffect(() => {
    api.get('/settings/device-keys').then((r) => setDeviceKeys(Array.isArray(r.data) ? r.data : [])).catch(() => {});
  }, []);

  const createKey = () => {
    if (!deviceKeyLabel.trim()) return;
    setDeviceKeyCreating(true);
    setNewDeviceKey(null);
    api.post('/settings/device-keys', { label: deviceKeyLabel.trim() })
      .then((r) => {
        setNewDeviceKey(r.data.raw_key);
        setDeviceKeys((prev) => [r.data, ...prev]);
        setDeviceKeyLabel('');
        notifications.show({ title: 'Key Created', message: 'Copy the key below — it won\'t be shown again', color: 'cyan' });
      })
      .catch(() => notifications.show({ title: 'Error', message: 'Failed to create key', color: 'red' }))
      .finally(() => setDeviceKeyCreating(false));
  };

  return (
    <Stack gap="md">
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconKey size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>DRONEOPSSYNC API KEYS</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Generate API keys for DroneOpsSync field controllers to upload flight logs without a user login.
          Keys are shown once at creation — copy immediately to your device.
        </Text>

        {/* Create new key */}
        <Group mb="md">
          <TextInput
            placeholder="Device label (e.g. Field Tablet #1)"
            value={deviceKeyLabel}
            onChange={(e) => setDeviceKeyLabel(e.target.value)}
            styles={{ ...inputStyles, root: { flex: 1 } }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                createKey();
              }
            }}
          />
          <Button
            leftSection={<IconPlus size={14} />}
            color="cyan"
            loading={deviceKeyCreating}
            onClick={createKey}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
          >
            GENERATE KEY
          </Button>
        </Group>

        {/* Show newly created key */}
        {newDeviceKey && (
          <Card padding="sm" radius="sm" mb="md" style={{ background: '#0a1628', border: '1px solid #00d4ff' }}>
            <Text size="xs" c="#00d4ff" fw={700} mb={4} style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
              NEW API KEY — COPY NOW (shown once)
            </Text>
            <TextInput
              value={newDeviceKey}
              readOnly
              styles={{ input: { background: '#050608', borderColor: '#00d4ff', color: '#00ff88', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px' } }}
              onClick={(e) => (e.target as HTMLInputElement).select()}
            />
            <Button
              size="xs"
              variant="light"
              color="cyan"
              mt={8}
              onClick={() => {
                const copy = (text: string) => {
                  if (navigator.clipboard?.writeText) {
                    return navigator.clipboard.writeText(text);
                  }
                  // Fallback for non-secure contexts (HTTP tunnels, older browsers)
                  const ta = document.createElement('textarea');
                  ta.value = text;
                  ta.style.position = 'fixed';
                  ta.style.opacity = '0';
                  document.body.appendChild(ta);
                  ta.focus();
                  ta.select();
                  document.execCommand('copy');
                  document.body.removeChild(ta);
                  return Promise.resolve();
                };
                copy(newDeviceKey)
                  .then(() => notifications.show({ title: 'Copied', message: 'API key copied to clipboard', color: 'cyan' }))
                  .catch(() => notifications.show({ title: 'Copy failed', message: 'Please select the key and copy manually', color: 'orange' }));
              }}
              styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
            >
              COPY TO CLIPBOARD
            </Button>
          </Card>
        )}

        {/* Key list */}
        {deviceKeys.length > 0 ? (
          <Table
            highlightOnHover
            styles={{
              table: { color: '#e8edf2' },
              th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px', borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
              td: { borderBottom: '1px solid #1a1f2e', padding: '8px 12px' },
            }}
          >
            <Table.Thead>
              <Table.Tr>
                <Table.Th>DEVICE</Table.Th>
                <Table.Th>CREATED</Table.Th>
                <Table.Th>LAST USED</Table.Th>
                <Table.Th w={60}></Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {deviceKeys.map((dk) => (
                <Table.Tr key={dk.id}>
                  <Table.Td>
                    <Text size="sm" fw={500}>{dk.label}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {new Date(dk.created_at).toLocaleDateString()}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {dk.last_used_at ? new Date(dk.last_used_at).toLocaleDateString() : 'Never'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      size="sm"
                      onClick={() => {
                        api.delete(`/settings/device-keys/${dk.id}`)
                          .then(() => {
                            setDeviceKeys((prev) => prev.filter((k) => k.id !== dk.id));
                            notifications.show({ title: 'Revoked', message: `Key "${dk.label}" has been revoked`, color: 'orange' });
                          })
                          .catch(() => notifications.show({ title: 'Error', message: 'Failed to revoke key', color: 'red' }));
                      }}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text c="#5a6478" size="sm" ta="center" py="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            No device keys yet. Generate one to connect DroneOpsSync.
          </Text>
        )}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconDrone size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>DRONEOPSSYNC SETUP</Title>
        </Group>
        <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          On your Android device, configure DroneOpsSync with:
        </Text>
        <Stack gap={4} mt="sm">
          <Text c="#e8edf2" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            1. Server URL: your server's LAN IP and port (e.g. http://192.168.1.50:3080)
          </Text>
          <Text c="#e8edf2" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            2. API Key: paste the key generated above
          </Text>
          <Text c="#e8edf2" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            3. Connect the device to the same network as the server
          </Text>
        </Stack>
      </Card>
    </Stack>
  );
}
