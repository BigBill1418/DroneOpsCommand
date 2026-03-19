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
import { useBranding } from '../hooks/useBranding';

interface LoginProps {
  onLogin: (username: string, password: string) => Promise<void>;
}

export default function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const branding = useBranding();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await onLogin(username, password);
    } catch {
      notifications.show({
        title: 'Login Failed',
        message: 'Invalid credentials',
        color: 'red',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #050608 0%, #0e1117 50%, #050608 100%)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 24,
        padding: '16px',
      }}
    >
      <Card
        shadow="xl"
        padding="xl"
        radius="md"
        w="100%"
        maw={440}
        mx="md"
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
        }}
      >
        <form onSubmit={handleSubmit}>
          <Stack gap="lg">
            <Center>
              <img
                src="/logo-full.svg"
                alt={branding.company_name}
                style={{ width: '100%', maxWidth: 420, height: 'auto' }}
              />
            </Center>

            <TextInput
              label="Username"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />

            <PasswordInput
              label="Password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
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
      </Card>

      <Stack align="center" gap={4}>
        <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
          Created by Bill Barnard — <Anchor href="mailto:me@barnardHQ.com" c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>me@barnardHQ.com</Anchor>
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
