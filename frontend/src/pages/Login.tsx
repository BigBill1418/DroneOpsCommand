import { useState, useEffect } from 'react';
import {
  Anchor,
  Box,
  Button,
  Card,
  Center,
  Divider,
  PasswordInput,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconShieldCheck } from '@tabler/icons-react';
import { useBranding } from '../hooks/useBranding';
import { useDemoMode } from '../hooks/useDemoMode';

interface LoginProps {
  onLogin: (username: string, password: string) => Promise<void>;
  /** ADR-0047 — true once the backend has Cloudflare Access verification
   * wired up (CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD both set). false for
   * every self-hosted/OSS install and the public demo instance — the
   * form below renders exactly as it did before this ADR in that case. */
  ssoConfigured?: boolean;
  /** ADR-0047 Step B — true only once an operator has explicitly retired
   * local login after the SSO soak period. Hides the password form
   * entirely. Always false for self-hosted/OSS/demo. */
  localLoginDisabled?: boolean;
}

const monoLabel = {
  color: '#5a6478',
  fontFamily: "'Share Tech Mono', monospace",
  fontSize: '11px',
  letterSpacing: '1px',
} as const;

const darkInput = {
  background: '#050608',
  borderColor: '#1a1f2e',
  color: '#e8edf2',
} as const;

export default function Login({ onLogin, ssoConfigured = false, localLoginDisabled = false }: LoginProps) {
  const isDemo = useDemoMode();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const branding = useBranding();

  // Auto-fill demo credentials
  useEffect(() => {
    if (isDemo) {
      setUsername('demo');
      setPassword('demo123');
    }
  }, [isDemo]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await onLogin(username, password);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { status?: number; data?: { detail?: string } } };
      const status = axiosErr.response?.status;
      const detail = axiosErr.response?.data?.detail;

      if (status === 429) {
        notifications.show({
          title: 'Account Locked',
          message: detail || 'Too many failed attempts. Please wait a few minutes.',
          color: 'orange',
          autoClose: 10000,
        });
      } else {
        notifications.show({
          title: 'Login Failed',
          message: detail || 'Invalid credentials',
          color: 'red',
        });
      }
    } finally {
      setLoading(false);
    }
  };

  // The operator does not type a password any more (ADR-0047): once this
  // screen renders with ssoConfigured=true, useAuth's silent Access probe
  // has already run and come back unauthenticated (a real operator whose
  // browser already completed Cloudflare Access SSO never sees this card
  // at all — the probe succeeds before the login screen ever mounts). So
  // this is the "not currently Access-authenticated" state, not a
  // "signing you in..." spinner.
  const showSsoCard = ssoConfigured;
  const showPasswordForm = !ssoConfigured || !localLoginDisabled;

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
      className="login-page"
    >
      {isDemo && (
        <Card
          w="100%"
          maw={440}
          padding="md"
          radius="md"
          style={{
            background: 'linear-gradient(135deg, #1a0a00, #1a0500)',
            border: '1px solid #ff6b1a',
            position: 'relative',
            zIndex: 1,
          }}
        >
          <Stack gap={8} align="center">
            <Text size="sm" fw={700} c="#ff6b1a" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '3px', fontSize: '18px' }}>
              DEMO INSTANCE
            </Text>
            <Text size="xs" c="#e8edf2" ta="center" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              Explore DroneOpsCommand with pre-loaded sample data.
              Some actions are restricted.
            </Text>
            <Card padding="xs" radius="sm" style={{ background: '#050608', border: '1px solid #1a1f2e', width: '100%' }}>
              <Stack gap={2} align="center">
                <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
                  CREDENTIALS
                </Text>
                <Text size="sm" c="#00d4ff" fw={600} style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  Username: demo &nbsp;|&nbsp; Password: demo123
                </Text>
              </Stack>
            </Card>
            <Anchor href="https://github.com/BigBill1418/DroneOpsCommand" target="_blank" c="#00d4ff" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
              Deploy Your Own Instance
            </Anchor>
          </Stack>
        </Card>
      )}

      <Card
        shadow="xl"
        padding="xl"
        radius="md"
        w="100%"
        maw={440}
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
          position: 'relative',
          zIndex: 1,
        }}
      >
        <Stack gap="lg">
          <Center>
            <img
              src="/logo-full.svg"
              alt={branding.company_name}
              style={{ width: '100%', maxWidth: 420, height: 'auto' }}
            />
          </Center>

          {showSsoCard && (
            <Card
              padding="md"
              radius="sm"
              data-testid="sso-card"
              style={{ background: '#071016', border: '1px solid #0d3a45' }}
            >
              <Stack gap={6} align="center">
                <IconShieldCheck size={22} color="#00d4ff" />
                <Text
                  size="xs"
                  fw={700}
                  c="#00d4ff"
                  ta="center"
                  style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '2px' }}
                >
                  BARNARDHQ ACCESS
                </Text>
                <Text size="xs" c="#8a93a6" ta="center" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                  This instance is protected by Cloudflare Access SSO. Sign in through your
                  organization's identity provider — you'll be returned here automatically.
                </Text>
                <Button
                  variant="light"
                  color="cyan"
                  size="xs"
                  mt={4}
                  onClick={() => window.location.reload()}
                  styles={{ root: { fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' } }}
                >
                  Retry Access Sign-In
                </Button>
              </Stack>
            </Card>
          )}

          {showSsoCard && showPasswordForm && (
            <Divider
              label="or sign in locally"
              labelPosition="center"
              color="#1a1f2e"
              styles={{ label: { ...monoLabel, fontSize: '10px' } }}
            />
          )}

          {showPasswordForm && (
            <form onSubmit={handleSubmit}>
              <Stack gap="lg">
                <TextInput
                  label="Username"
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  styles={{ input: darkInput, label: monoLabel }}
                />

                <PasswordInput
                  label="Password"
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  styles={{ input: darkInput, label: monoLabel }}
                />

                <Button
                  type="submit"
                  fullWidth
                  loading={loading}
                  color="cyan"
                  variant="filled"
                  styles={{
                    root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '16px' },
                  }}
                >
                  LOGIN
                </Button>
              </Stack>
            </form>
          )}
        </Stack>
      </Card>

      <Stack align="center" gap={4}>
        <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
          Created by Bill Barnard — <Anchor href="mailto:Bill@BarnardHQ.com" c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>Bill@BarnardHQ.com</Anchor>
        </Text>
        <Text size="xs" c="#3a3f4a" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
          v{typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : ''}
        </Text>
        <Anchor
          href="https://www.barnardhq.com"
          target="_blank"
          rel="noopener noreferrer"
          data-testid="barnardhq-credit"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 4 }}
        >
          <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px', textTransform: 'uppercase' }}>
            A software solution by:
          </Text>
          <img src="/barnardhq-logo.svg" alt="BarnardHQ" width={82} height={14} style={{ height: 14, width: 'auto' }} />
        </Anchor>
      </Stack>
    </Box>
  );
}
