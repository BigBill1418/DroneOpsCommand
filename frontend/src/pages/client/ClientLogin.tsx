import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Center,
  Paper,
  Stack,
  Text,
  TextInput,
  PasswordInput,
  Button,
  Loader,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useClientAuth } from '../../hooks/useClientAuth';

export default function ClientLogin() {
  const navigate = useNavigate();
  const auth = useClientAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // If already authenticated via stored token, redirect to portal
  if (auth.isAuthenticated && !auth.loading) {
    // Build a minimal portal URL — the dashboard reads from localStorage
    navigate('/client/_session');
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) return;

    setSubmitting(true);
    const ok = await auth.loginWithPassword(email.trim(), password);
    setSubmitting(false);

    if (ok) {
      notifications.show({
        title: 'Welcome back',
        message: 'You are now logged in to the client portal.',
        color: 'cyan',
      });
      navigate('/client/_session');
    } else {
      notifications.show({
        title: 'Login Failed',
        message: auth.error || 'Invalid email or password. Please try again.',
        color: 'red',
      });
    }
  };

  return (
    <Center h="100vh" style={{ background: '#050608' }}>
      <Paper
        p="xl"
        radius="md"
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
          maxWidth: 420,
          width: '100%',
        }}
      >
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <Text
              size="xl"
              fw={700}
              ta="center"
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '3px' }}
              c="#e8edf2"
            >
              CLIENT PORTAL LOGIN
            </Text>
            <Text c="#5a6478" size="sm" ta="center">
              Sign in with your email and portal password.
            </Text>

            <TextInput
              label="Email"
              placeholder="your@email.com"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              required
              autoComplete="email"
              styles={{
                input: { background: '#050608', color: '#e8edf2', borderColor: '#1a1f2e' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace" },
              }}
            />

            <PasswordInput
              label="Password"
              placeholder="Your portal password"
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              required
              autoComplete="current-password"
              styles={{
                input: { background: '#050608', color: '#e8edf2', borderColor: '#1a1f2e' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace" },
              }}
            />

            <Button
              type="submit"
              fullWidth
              loading={submitting}
              color="cyan"
              style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}
            >
              {submitting ? <Loader size="xs" color="dark" /> : 'SIGN IN'}
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
