import { useState } from 'react';
import {
  Anchor,
  Box,
  Button,
  Card,
  Center,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconLock, IconUser, IconShieldCheck } from '@tabler/icons-react';
import { useBranding } from '../hooks/useBranding';
import api from '../api/client';

interface SetupProps {
  onSetupComplete: (accessToken: string, refreshToken: string) => void;
}

interface PasswordRule {
  label: string;
  test: (pw: string) => boolean;
}

const PASSWORD_RULES: PasswordRule[] = [
  { label: 'At least 10 characters', test: (pw) => pw.length >= 10 },
  { label: 'At least one uppercase letter', test: (pw) => /[A-Z]/.test(pw) },
  { label: 'At least one lowercase letter', test: (pw) => /[a-z]/.test(pw) },
  { label: 'At least one number', test: (pw) => /[0-9]/.test(pw) },
  { label: 'At least one special character', test: (pw) => /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]/.test(pw) },
];


/**
 * Turn a failed POST /auth/setup into a message that says WHAT failed.
 * The backend only sends a string `detail` for its own 4xx rejections; a
 * rate-limit 429 (`{"error": ...}`), a FastAPI 422 (`detail` is a list), a
 * 5xx, a proxy 502/504 (HTML) or a dead connection all used to collapse into
 * one generic line, which left an installer with nothing to act on.
 */
function setupErrorMessage(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown; error?: string } } };
  const status = e.response?.status;
  const data = e.response?.data;
  if (!e.response) {
    return 'Cannot reach the DroneOps server (no response). Check that the backend container is running and that this page is served by the same stack: docker compose ps, docker compose logs backend.';
  }
  if (typeof data?.detail === 'string' && data.detail) return data.detail;
  if (Array.isArray(data?.detail)) {
    const msgs = data.detail
      .map((d) => (d as { msg?: string }).msg)
      .filter(Boolean)
      .join('; ');
    if (msgs) return `Invalid input: ${msgs}`;
  }
  if (status === 429) return 'Too many setup attempts. Wait one minute and try again.';
  if (status === 502 || status === 503 || status === 504) {
    return `The backend is not answering (HTTP ${status}). It may still be starting; wait a minute, then check: docker compose logs backend.`;
  }
  if (status && status >= 500) {
    return `The server hit an error creating the account (HTTP ${status}). Check: docker compose logs backend.`;
  }
  return `Could not create admin account (HTTP ${status ?? 'unknown'}). Check: docker compose logs backend.`;
}

export default function Setup({ onSetupComplete }: SetupProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const branding = useBranding();

  const allRulesPassed = PASSWORD_RULES.every((r) => r.test(password));
  const passwordsMatch = password === confirmPassword && confirmPassword.length > 0;
  const usernameValid = username.trim().length >= 3;
  const canSubmit = usernameValid && allRulesPassed && passwordsMatch && !loading;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    try {
      const resp = await api.post('/auth/setup', { username: username.trim(), password });
      const { access_token, refresh_token } = resp.data;
      notifications.show({
        title: 'Setup Complete',
        message: `Welcome, ${resp.data.username}! Your admin account has been created.`,
        color: 'green',
        autoClose: 5000,
      });
      onSetupComplete(access_token, refresh_token);
    } catch (err: unknown) {
      notifications.show({
        title: 'Setup Failed',
        message: setupErrorMessage(err),
        color: 'red',
        autoClose: 15000,
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box
      style={{
        minHeight: '100dvh',
        background: 'linear-gradient(135deg, #050608 0%, #0e1117 50%, #050608 100%)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 24,
        padding: '16px',
        boxSizing: 'border-box',
        overflowX: 'hidden',
        overflowY: 'auto',
        WebkitOverflowScrolling: 'touch',
      }}
    >
      <Card shadow="xl" padding="xl" radius="md" w="100%" maw={480} style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}>
        <form onSubmit={handleSubmit}>
          <Stack gap="lg">
            <Center>
              <img src="/logo-full.svg" alt={branding.company_name} style={{ width: '100%', maxWidth: 420, height: 'auto' }} />
            </Center>
            <Card padding="sm" radius="sm" style={{ background: '#050608', border: '1px solid #00d4ff33' }}>
              <Stack gap={4} align="center">
                <IconShieldCheck size={28} color="#00d4ff" />
                <Title order={4} c="#e8edf2" ta="center" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '3px' }}>
                  INITIAL SETUP
                </Title>
                <Text size="xs" c="#5a6478" ta="center" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  Create your admin account to get started.
                  These credentials are stored securely in the database — no environment variables needed.
                </Text>
              </Stack>
            </Card>
            <TextInput
              label="Username"
              placeholder="Choose a username (min 3 characters)"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              leftSection={<IconUser size={14} />}
              error={username.length > 0 && !usernameValid ? 'Username must be at least 3 characters' : undefined}
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />
            <PasswordInput
              label="Password"
              placeholder="Create a strong password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              leftSection={<IconLock size={14} />}
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />
            {password.length > 0 && (
              <Card padding="xs" radius="sm" style={{ background: '#050608', border: '1px solid #1a1f2e' }}>
                <Stack gap={2}>
                  {PASSWORD_RULES.map((rule) => {
                    const passed = rule.test(password);
                    return (
                      <Text key={rule.label} size="xs" c={passed ? '#00d4ff' : '#5a6478'} style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '10px' }}>
                        {passed ? '✓' : '○'} {rule.label}
                      </Text>
                    );
                  })}
                </Stack>
              </Card>
            )}
            <PasswordInput
              label="Confirm Password"
              placeholder="Re-enter your password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              leftSection={<IconLock size={14} />}
              error={confirmPassword.length > 0 && !passwordsMatch ? 'Passwords do not match' : undefined}
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />
            <Button type="submit" fullWidth loading={loading} disabled={!canSubmit} color="cyan" variant="filled"
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '16px' } }}>
              CREATE ADMIN ACCOUNT
            </Button>
          </Stack>
        </form>
      </Card>
      <Stack align="center" gap={4}>
        <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
          Created by Bill Barnard — <Anchor href="mailto:Bill@BarnardHQ.com" c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>Bill@BarnardHQ.com</Anchor>
        </Text>
        <Anchor href="https://www.barnardHQ.com" target="_blank" c="#00d4ff" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
          www.barnardHQ.com
        </Anchor>
        <Text size="xs" c="#3a3f4a" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
          v{typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : ''}
        </Text>
      </Stack>
    </Box>
  );
}
