import { useEffect, useState } from 'react';
import {
  Button,
  Card,
  Group,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconLock, IconUser } from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';
import PasswordStrengthMeter, { isPasswordValid } from '../../components/PasswordStrengthMeter';

/**
 * ACCOUNT tab — admin username/password change. Fetches /auth/account on mount.
 * Validation rules and the token-refresh-on-change behavior are unchanged.
 */
export default function AccountTab() {
  const [accountSaving, setAccountSaving] = useState(false);
  const [currentUsername, setCurrentUsername] = useState('');

  const accountForm = useForm({
    initialValues: { current_password: '', new_username: '', new_password: '', confirm_password: '' },
    validate: {
      current_password: (v) => (v.length === 0 ? 'Current password is required' : null),
      new_password: (v) => (v && v.length > 0 && !isPasswordValid(v) ? 'Password does not meet complexity requirements' : null),
      confirm_password: (v, values) => (v !== values.new_password ? 'Passwords do not match' : null),
    },
  });

  useEffect(() => {
    api.get('/auth/account').then((r) => { setCurrentUsername(r.data.username); accountForm.setFieldValue('new_username', r.data.username); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSaveAccount = async (values: typeof accountForm.values) => {
    if (!values.new_username && !values.new_password) {
      notifications.show({ title: 'Nothing to update', message: 'Change username or password', color: 'orange' });
      return;
    }
    setAccountSaving(true);
    try {
      const payload: Record<string, string> = { current_password: values.current_password };
      if (values.new_username && values.new_username !== currentUsername) {
        payload.new_username = values.new_username;
      }
      if (values.new_password) {
        payload.new_password = values.new_password;
      }
      const r = await api.put('/auth/account', payload);
      // Update stored tokens if returned
      if (r.data.access_token) {
        localStorage.setItem('access_token', r.data.access_token);
      }
      if (r.data.refresh_token) {
        localStorage.setItem('refresh_token', r.data.refresh_token);
      }
      setCurrentUsername(r.data.username);
      accountForm.setValues({ current_password: '', new_username: r.data.username, new_password: '', confirm_password: '' });
      notifications.show({ title: 'Account Updated', message: 'Your credentials have been changed', color: 'green' });
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      notifications.show({ title: 'Error', message: axiosErr.response?.data?.detail || 'Failed to update account', color: 'red' });
    } finally {
      setAccountSaving(false);
    }
  };

  return (
    <Stack gap="md">
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconUser size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>ADMIN ACCOUNT</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Change your login username and password. You must enter your current password to confirm changes.
        </Text>
        <form onSubmit={accountForm.onSubmit(handleSaveAccount)}>
          <Stack gap="sm">
            <TextInput
              label="Current Username"
              value={currentUsername}
              readOnly
              styles={{
                ...inputStyles,
                input: { ...inputStyles.input, opacity: 0.6 },
              }}
            />
            <PasswordInput
              label="Current Password"
              placeholder="Enter your current password"
              required
              leftSection={<IconLock size={14} />}
              {...accountForm.getInputProps('current_password')}
              styles={inputStyles}
            />
            <div style={{ borderTop: '1px solid #1a1f2e', margin: '8px 0' }} />
            <TextInput
              label="New Username"
              placeholder="Leave unchanged to keep current username"
              leftSection={<IconUser size={14} />}
              {...accountForm.getInputProps('new_username')}
              styles={inputStyles}
            />
            <PasswordInput
              label="New Password"
              placeholder="Leave blank to keep current password"
              leftSection={<IconLock size={14} />}
              {...accountForm.getInputProps('new_password')}
              styles={inputStyles}
            />
            <PasswordStrengthMeter password={accountForm.values.new_password} />
            <PasswordInput
              label="Confirm New Password"
              placeholder="Re-enter new password"
              leftSection={<IconLock size={14} />}
              {...accountForm.getInputProps('confirm_password')}
              styles={inputStyles}
            />
            <Button type="submit" color="cyan" loading={accountSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              UPDATE ACCOUNT
            </Button>
          </Stack>
        </form>
      </Card>
    </Stack>
  );
}
