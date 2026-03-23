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
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useBranding } from '../hooks/useBranding';
import PasswordStrengthMeter, { isPasswordValid } from '../components/PasswordStrengthMeter';
import api from '../api/client';

interface Props {
  onComplete: () => void;
  onLogout: () => void;
}

export default function ForcePasswordReset({ onComplete, onLogout }: Props) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const branding = useBranding();

  const canSubmit =
    currentPassword.length > 0 &&
    isPasswordValid(newPassword) &&
    newPassword === confirmPassword;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    setLoading(true);
    try {
      const resp = await api.post('/auth/force-reset', {
        current_password: currentPassword,
        new_password: newPassword,
      });

      // Update stored tokens
      if (resp.data.access_token) {
        localStorage.setItem('access_token', resp.data.access_token);
      }
      if (resp.data.refresh_token) {
        localStorage.setItem('refresh_token', resp.data.refresh_token);
      }

      notifications.show({
        title: 'Password Updated',
        message: 'Your password now meets security requirements',
        color: 'green',
      });
      onComplete();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({
        title: 'Reset Failed',
        message: axiosErr.response?.data?.detail || 'Failed to update password',
        color: 'red',
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
      <Card
        shadow="xl"
        padding="xl"
        radius="md"
        w="100%"
        maw={480}
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
          position: 'relative',
          zIndex: 1,
        }}
      >
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <Center>
              <img
                src="/logo-full.svg"
                alt={branding.company_name}
                style={{ width: '100%', maxWidth: 360, height: 'auto' }}
              />
            </Center>

            {/* Warning banner */}
            <Box
              style={{
                background: 'rgba(255, 68, 68, 0.08)',
                border: '1px solid rgba(255, 68, 68, 0.3)',
                borderRadius: 8,
                padding: '12px 16px',
              }}
            >
              <Title
                order={5}
                style={{
                  color: '#ff4444',
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '2px',
                  marginBottom: 4,
                }}
              >
                PASSWORD UPDATE REQUIRED
              </Title>
              <Text
                size="xs"
                style={{
                  fontFamily: "'Share Tech Mono', monospace",
                  color: '#cc8888',
                  lineHeight: 1.5,
                }}
              >
                Your current password does not meet the security requirements.
                You must set a new password before continuing.
              </Text>
            </Box>

            <PasswordInput
              label="Current Password"
              placeholder="Enter your current password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              autoComplete="current-password"
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />

            <div style={{ borderTop: '1px solid #1a1f2e', margin: '4px 0' }} />

            <PasswordInput
              label="New Password"
              placeholder="Enter a strong password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              autoComplete="new-password"
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />

            <PasswordStrengthMeter password={newPassword} />

            <PasswordInput
              label="Confirm New Password"
              placeholder="Re-enter new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
              error={
                confirmPassword.length > 0 && confirmPassword !== newPassword
                  ? 'Passwords do not match'
                  : undefined
              }
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />

            <Button
              type="submit"
              fullWidth
              loading={loading}
              disabled={!canSubmit}
              color="cyan"
              variant="filled"
              styles={{
                root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '16px' },
              }}
            >
              UPDATE PASSWORD
            </Button>

            <Button
              variant="subtle"
              color="gray"
              fullWidth
              onClick={onLogout}
              styles={{
                root: { fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            >
              LOGOUT
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
      </Stack>
    </Box>
  );
}
