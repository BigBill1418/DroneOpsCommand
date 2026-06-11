import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  PasswordInput,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconBrandPaypal, IconCash, IconMail, IconSend, IconSignature, IconUpload } from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';

/**
 * EMAIL & BILLING tab — SMTP config + test, default TOS PDF, payment links.
 * Fetches /settings/smtp, /settings/payment, /intake/default-tos-status on
 * mount. All field names and payloads unchanged.
 */
export default function EmailTab() {
  const [smtpSaving, setSmtpSaving] = useState(false);
  const [smtpTesting, setSmtpTesting] = useState(false);
  const [paymentSaving, setPaymentSaving] = useState(false);
  const [tosUploaded, setTosUploaded] = useState(false);
  const [tosUploading, setTosUploading] = useState(false);

  const smtpForm = useForm({
    initialValues: {
      smtp_host: '',
      smtp_port: '587',
      smtp_user: '',
      smtp_password: '',
      smtp_from_email: '',
      smtp_from_name: '',
      smtp_use_tls: 'true',
    },
  });

  const paymentForm = useForm({
    initialValues: { paypal_link: '', venmo_link: '' },
  });

  useEffect(() => {
    api.get('/settings/smtp').then((r) => smtpForm.setValues(r.data)).catch(() => {});
    api.get('/settings/payment').then((r) => paymentForm.setValues(r.data)).catch(() => {});
    api.get('/intake/default-tos-status').then((r) => setTosUploaded(r.data.uploaded)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSaveSmtp = async (values: typeof smtpForm.values) => {
    setSmtpSaving(true);
    try {
      await api.put('/settings/smtp', values);
      notifications.show({ title: 'Saved', message: 'SMTP settings updated', color: 'cyan' });
      const r = await api.get('/settings/smtp');
      smtpForm.setValues(r.data);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save SMTP settings', color: 'red' });
    } finally {
      setSmtpSaving(false);
    }
  };

  const handleTestSmtp = async () => {
    setSmtpTesting(true);
    try {
      const r = await api.post('/settings/smtp/test');
      if (r.data.status === 'ok') {
        notifications.show({ title: 'Success', message: r.data.message, color: 'green' });
      } else {
        notifications.show({ title: 'SMTP Error', message: r.data.message, color: 'red' });
      }
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to test SMTP', color: 'red' });
    } finally {
      setSmtpTesting(false);
    }
  };

  const handleSavePayment = async (values: typeof paymentForm.values) => {
    setPaymentSaving(true);
    try {
      await api.put('/settings/payment', values);
      notifications.show({ title: 'Saved', message: 'Payment links updated', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save payment links', color: 'red' });
    } finally {
      setPaymentSaving(false);
    }
  };

  return (
    <Stack gap="md">
      {/* SMTP Settings */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" mb="md">
          <Group gap="sm">
            <IconMail size={20} color="#00d4ff" />
            <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>SMTP / EMAIL</Title>
          </Group>
          <Button
            leftSection={<IconSend size={14} />}
            size="xs"
            variant="light"
            color="cyan"
            loading={smtpTesting}
            onClick={handleTestSmtp}
          >
            Send Test
          </Button>
        </Group>
        <form onSubmit={smtpForm.onSubmit(handleSaveSmtp)}>
          <Stack gap="sm">
            <Group grow>
              <TextInput label="SMTP Host" placeholder="smtp.gmail.com" {...smtpForm.getInputProps('smtp_host')} styles={inputStyles} />
              <TextInput label="SMTP Port" placeholder="587" {...smtpForm.getInputProps('smtp_port')} styles={inputStyles} />
            </Group>
            <Group grow>
              <TextInput label="Username" placeholder="user@example.com" {...smtpForm.getInputProps('smtp_user')} styles={inputStyles} />
              <PasswordInput label="Password" placeholder="App password or SMTP key" {...smtpForm.getInputProps('smtp_password')} styles={inputStyles} />
            </Group>
            <Group grow>
              <TextInput label="From Email" placeholder="reports@yourcompany.com" {...smtpForm.getInputProps('smtp_from_email')} styles={inputStyles} />
              <TextInput label="From Name" placeholder="Your Company Drone Operations" {...smtpForm.getInputProps('smtp_from_name')} styles={inputStyles} />
            </Group>
            <Switch
              label="Use TLS"
              checked={smtpForm.values.smtp_use_tls === 'true'}
              onChange={(e) => smtpForm.setFieldValue('smtp_use_tls', e.currentTarget.checked ? 'true' : 'false')}
              color="cyan"
              styles={{ label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' } }}
            />
            <Button type="submit" color="cyan" loading={smtpSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE SMTP SETTINGS
            </Button>
          </Stack>
        </form>
      </Card>

      {/* Terms of Service PDF */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconSignature size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>TERMS OF SERVICE</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Upload the default TOS PDF that customers will review and sign during onboarding.
        </Text>
        <Group>
          <Badge color={tosUploaded ? 'green' : 'orange'} variant="light">
            {tosUploaded ? 'TOS PDF UPLOADED' : 'NO TOS PDF'}
          </Badge>
          <Button
            leftSection={<IconUpload size={14} />}
            color="cyan"
            variant="light"
            size="xs"
            loading={tosUploading}
            onClick={() => {
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = '.pdf';
              input.onchange = async (e: Event) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (!file) return;
                setTosUploading(true);
                try {
                  const formData = new FormData();
                  formData.append('file', file);
                  await api.post('/intake/upload-default-tos', formData);
                  setTosUploaded(true);
                  notifications.show({ title: 'Uploaded', message: 'Default TOS PDF uploaded', color: 'cyan' });
                } catch {
                  notifications.show({ title: 'Error', message: 'Failed to upload TOS PDF', color: 'red' });
                } finally {
                  setTosUploading(false);
                }
              };
              input.click();
            }}
          >
            Upload PDF
          </Button>
        </Group>
      </Card>

      {/* Payment Links */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconCash size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>PAYMENT LINKS</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          These links appear on invoices that are not marked as paid in full.
        </Text>
        <form onSubmit={paymentForm.onSubmit(handleSavePayment)}>
          <Stack gap="sm">
            <TextInput
              label="PayPal Link"
              placeholder="https://paypal.me/yourname"
              leftSection={<IconBrandPaypal size={14} />}
              {...paymentForm.getInputProps('paypal_link')}
              styles={inputStyles}
            />
            <TextInput
              label="Venmo Link"
              placeholder="https://venmo.com/yourname"
              {...paymentForm.getInputProps('venmo_link')}
              styles={inputStyles}
            />
            <Button type="submit" color="cyan" loading={paymentSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE PAYMENT LINKS
            </Button>
          </Stack>
        </form>
      </Card>
    </Stack>
  );
}
