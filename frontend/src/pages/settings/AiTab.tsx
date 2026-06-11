import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  PasswordInput,
  Select,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconRobot, IconX } from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';

/**
 * AI / REPORTS tab — LLM provider selection + live status. Fetches /llm/status
 * and /settings/llm only when this tab mounts. Payload field names unchanged.
 */
export default function AiTab() {
  const [llmStatus, setLlmStatus] = useState<{ status: string; configured_model?: string; model_available?: boolean; models?: string[] } | null>(null);
  const [llmLoading, setLlmLoading] = useState(true);
  const [llmSaving, setLlmSaving] = useState(false);

  const llmForm = useForm({
    initialValues: { llm_provider: 'ollama', anthropic_api_key: '' },
  });

  useEffect(() => {
    api.get('/llm/status').then((r) => setLlmStatus(r.data)).catch(() => setLlmStatus({ status: 'offline' })).finally(() => setLlmLoading(false));
    api.get('/settings/llm').then((r) => llmForm.setValues(r.data)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSaveLlm = async (values: typeof llmForm.values) => {
    setLlmSaving(true);
    try {
      await api.put('/settings/llm', values);
      notifications.show({ title: 'Saved', message: 'LLM settings updated', color: 'cyan' });
      const r = await api.get('/settings/llm');
      llmForm.setValues(r.data);
      // Refresh LLM status to reflect new provider
      setLlmLoading(true);
      api.get('/llm/status').then((s) => setLlmStatus(s.data)).catch(() => setLlmStatus({ status: 'offline' })).finally(() => setLlmLoading(false));
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save LLM settings', color: 'red' });
    } finally {
      setLlmSaving(false);
    }
  };

  return (
    <Stack gap="md">
      {/* LLM Provider Settings */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconRobot size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>AI / REPORT GENERATION</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Choose the LLM provider used for generating after-action reports. Claude API is faster and higher quality; Ollama runs locally on your hardware.
        </Text>
        <form onSubmit={llmForm.onSubmit(handleSaveLlm)}>
          <Stack gap="sm">
            <Select
              label="LLM Provider"
              data={[
                { value: 'claude', label: 'Claude API (Anthropic)' },
                { value: 'ollama', label: 'Ollama (Local)' },
              ]}
              {...llmForm.getInputProps('llm_provider')}
              styles={inputStyles}
            />
            {llmForm.values.llm_provider === 'claude' && (
              <PasswordInput
                label="Anthropic API Key"
                placeholder="sk-ant-..."
                {...llmForm.getInputProps('anthropic_api_key')}
                styles={inputStyles}
              />
            )}
            <Button type="submit" color="cyan" loading={llmSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE LLM SETTINGS
            </Button>
          </Stack>
        </form>
      </Card>

      {/* LLM Status */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Title order={3} c="#e8edf2" mb="md" style={{ letterSpacing: '1px' }}>LLM STATUS</Title>
        {llmLoading ? (
          <Loader color="cyan" size="sm" />
        ) : (
          <Stack gap="sm">
            <Group>
              <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>PROVIDER:</Text>
              <Badge color="cyan" variant="light">
                {(llmStatus as any)?.provider === 'claude' ? 'Claude API' : 'Ollama'}
              </Badge>
            </Group>
            <Group>
              <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>STATUS:</Text>
              <Badge
                color={llmStatus?.status === 'online' ? 'green' : 'red'}
                leftSection={llmStatus?.status === 'online' ? <IconCheck size={12} /> : <IconX size={12} />}
              >
                {llmStatus?.status || 'unknown'}
              </Badge>
            </Group>
            <Group>
              <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>MODEL:</Text>
              <Text c="#e8edf2">{llmStatus?.configured_model || 'Not set'}</Text>
              {llmStatus?.model_available && <Badge color="green" size="xs">Available</Badge>}
            </Group>
            {llmStatus?.models && (
              <Group>
                <Text c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>INSTALLED:</Text>
                {llmStatus.models.map((m: string) => <Badge key={m} color="cyan" variant="light" size="sm">{m}</Badge>)}
              </Group>
            )}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
