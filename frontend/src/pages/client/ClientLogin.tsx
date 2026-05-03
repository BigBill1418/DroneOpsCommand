/**
 * Client portal — email/password login.
 *
 * Customer-facing — wrapped in <CustomerLayout> with the BarnardHQ
 * brand pass (v2.65.0).
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Loader,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useClientAuth } from '../../hooks/useClientAuth';
import CustomerLayout from '../../components/CustomerLayout';
import { customerBrand, customerStyles } from '../../lib/customerTheme';
import { customerNotify } from '../../lib/customerNotify';

export default function ClientLogin() {
  const navigate = useNavigate();
  const auth = useClientAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // If already authenticated via stored token, redirect to portal
  if (auth.isAuthenticated && !auth.loading) {
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
      customerNotify({
        title: 'Welcome back',
        message: 'You are now logged in to the client portal.',
        kind: 'success',
      });
      navigate('/client/_session');
    } else {
      customerNotify({
        title: 'Login Failed',
        message: auth.error || 'Invalid email or password. Please try again.',
        kind: 'danger',
      });
    }
  };

  return (
    <CustomerLayout
      maxWidth={460}
      contextSlot={
        <span style={{ textTransform: 'uppercase' }}>Client Portal · Sign In</span>
      }
    >
      <Paper p="xl" radius="md" style={customerStyles.card}>
        <form onSubmit={handleSubmit} noValidate>
          <Stack gap="md">
            <div>
              <Title
                order={2}
                ta="center"
                style={{
                  ...customerStyles.display,
                  color: customerBrand.brandCyan,
                  fontSize: 28,
                  marginBottom: 4,
                }}
              >
                CLIENT PORTAL LOGIN
              </Title>
              <Text
                ta="center"
                style={{
                  color: customerBrand.textMuted,
                  fontFamily: customerBrand.fontMono,
                  fontSize: 12,
                  letterSpacing: customerBrand.trackTight,
                }}
              >
                Sign in with your email and portal password.
              </Text>
            </div>

            <TextInput
              label="Email"
              placeholder="your@email.com"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              required
              autoComplete="email"
              type="email"
              size="md"
              styles={{
                input: customerStyles.input,
                label: customerStyles.inputLabel,
              }}
            />

            <PasswordInput
              label="Password"
              placeholder="Your portal password"
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              required
              autoComplete="current-password"
              size="md"
              styles={{
                input: customerStyles.input,
                label: customerStyles.inputLabel,
              }}
            />

            <Button
              type="submit"
              fullWidth
              loading={submitting}
              size="md"
              styles={{
                root: {
                  background: customerBrand.brandCyan,
                  color: customerBrand.brandNavyDeep,
                  fontFamily: customerBrand.fontDisplay,
                  letterSpacing: customerBrand.trackMid,
                  fontWeight: 700,
                  fontSize: 16,
                },
              }}
            >
              {submitting ? <Loader size="xs" color="dark" /> : 'SIGN IN'}
            </Button>
          </Stack>
        </form>
      </Paper>
    </CustomerLayout>
  );
}
