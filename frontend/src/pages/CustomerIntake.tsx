import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Button,
  Card,
  Center,
  Checkbox,
  Group,
  Loader,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconAlertTriangle } from '@tabler/icons-react';
import { useParams } from 'react-router-dom';
import SignatureCanvas from 'react-signature-canvas';
import axios from 'axios';

const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' },
};

interface IntakeData {
  customer_name: string | null;
  customer_email: string | null;
  customer_phone: string | null;
  customer_address: string | null;
  customer_company: string | null;
  tos_pdf_url: string | null;
  already_completed: boolean;
}

type PageState = 'loading' | 'form' | 'completed' | 'already_done' | 'error' | 'expired';

export default function CustomerIntake() {
  const { token } = useParams<{ token: string }>();
  const [state, setState] = useState<PageState>('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [address, setAddress] = useState('');
  const [company, setCompany] = useState('');
  const [tosAccepted, setTosAccepted] = useState(false);
  const [tosPdfUrl, setTosPdfUrl] = useState<string | null>(null);

  const sigRef = useRef<SignatureCanvas>(null);

  useEffect(() => {
    if (!token) { setState('error'); setErrorMsg('Invalid link'); return; }

    axios.get<IntakeData>(`/api/intake/form/${token}`)
      .then((r) => {
        const d = r.data;
        if (d.already_completed) {
          setState('already_done');
          return;
        }
        setName(d.customer_name || '');
        setEmail(d.customer_email || '');
        setPhone(d.customer_phone || '');
        setAddress(d.customer_address || '');
        setCompany(d.customer_company || '');
        setTosPdfUrl(d.tos_pdf_url);
        setState('form');
      })
      .catch((err) => {
        if (err.response?.status === 410) {
          setState('expired');
        } else {
          setState('error');
          setErrorMsg(err.response?.data?.detail || 'This link is invalid or has expired.');
        }
      });
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name.trim() || !email.trim()) {
      notifications.show({ title: 'Required', message: 'Name and email are required', color: 'red' });
      return;
    }
    if (!tosAccepted) {
      notifications.show({ title: 'Required', message: 'You must agree to the Terms of Service', color: 'red' });
      return;
    }
    if (sigRef.current?.isEmpty()) {
      notifications.show({ title: 'Required', message: 'Please provide your signature', color: 'red' });
      return;
    }

    const signatureData = sigRef.current?.toDataURL('image/png') || '';

    setSubmitting(true);
    try {
      await axios.post(`/api/intake/form/${token}`, {
        name: name.trim(),
        email: email.trim(),
        phone: phone.trim() || null,
        address: address.trim() || null,
        company: company.trim() || null,
        signature_data: signatureData,
        tos_accepted: true,
      });
      setState('completed');
    } catch (err: any) {
      notifications.show({
        title: 'Error',
        message: err.response?.data?.detail || 'Submission failed. Please try again.',
        color: 'red',
      });
    } finally {
      setSubmitting(false);
    }
  };

  const renderContent = () => {
    if (state === 'loading') {
      return (
        <Center py={60}>
          <Loader color="cyan" size="lg" />
        </Center>
      );
    }

    if (state === 'error') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconAlertTriangle size={48} color="#ff6b6b" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>INVALID LINK</Title>
          <Text c="#5a6478" ta="center">{errorMsg}</Text>
        </Stack>
      );
    }

    if (state === 'expired') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconAlertTriangle size={48} color="#ff6b1a" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>LINK EXPIRED</Title>
          <Text c="#5a6478" ta="center">This intake link has expired. Please contact BarnardHQ for a new one.</Text>
        </Stack>
      );
    }

    if (state === 'already_done') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconCheck size={48} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>ALREADY COMPLETED</Title>
          <Text c="#5a6478" ta="center">You have already submitted your information. Thank you!</Text>
        </Stack>
      );
    }

    if (state === 'completed') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconCheck size={48} color="#00ff88" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>THANK YOU!</Title>
          <Text c="#5a6478" ta="center" maw={400}>
            Your information has been submitted and your Terms of Service agreement has been signed.
            The BarnardHQ team will be in touch.
          </Text>
        </Stack>
      );
    }

    return (
      <form onSubmit={handleSubmit}>
        <Stack gap="md">
          <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
            CUSTOMER INFORMATION
          </Text>

          <TextInput label="Full Name" required value={name} onChange={(e) => setName(e.target.value)} styles={inputStyles} />
          <TextInput label="Email" required type="email" value={email} onChange={(e) => setEmail(e.target.value)} styles={inputStyles} />
          <TextInput label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} styles={inputStyles} />
          <TextInput label="Company" value={company} onChange={(e) => setCompany(e.target.value)} styles={inputStyles} />
          <TextInput label="Mailing Address" value={address} onChange={(e) => setAddress(e.target.value)} styles={inputStyles} />

          {/* Terms of Service */}
          <div style={{ marginTop: 8 }}>
            <Text c="#5a6478" size="sm" mb="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
              TERMS OF SERVICE
            </Text>

            {tosPdfUrl ? (
              <Box
                style={{
                  border: '1px solid #1a1f2e',
                  borderRadius: 6,
                  overflow: 'hidden',
                  background: '#fff',
                  marginBottom: 12,
                }}
              >
                <iframe
                  src={tosPdfUrl}
                  style={{ width: '100%', height: 400, border: 'none' }}
                  title="Terms of Service"
                />
              </Box>
            ) : (
              <Box
                p="md"
                style={{
                  border: '1px solid #1a1f2e',
                  borderRadius: 6,
                  background: '#050608',
                  marginBottom: 12,
                }}
              >
                <Text c="#5a6478" size="sm" ta="center">
                  Terms of Service document will be provided by BarnardHQ.
                </Text>
              </Box>
            )}

            <Checkbox
              label="I have read and agree to the Terms of Service"
              checked={tosAccepted}
              onChange={(e) => setTosAccepted(e.currentTarget.checked)}
              color="cyan"
              styles={{
                label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px' },
              }}
            />
          </div>

          {/* Signature */}
          <div style={{ marginTop: 8 }}>
            <Text c="#5a6478" size="sm" mb="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
              DIGITAL SIGNATURE
            </Text>
            <Box
              style={{
                border: '1px solid #1a1f2e',
                borderRadius: 6,
                background: '#050608',
                overflow: 'hidden',
              }}
            >
              <SignatureCanvas
                ref={sigRef}
                penColor="#00d4ff"
                backgroundColor="#050608"
                canvasProps={{
                  style: { width: '100%', height: 150 },
                }}
              />
            </Box>
            <Group justify="space-between" mt={4}>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Sign above using your mouse or finger
              </Text>
              <Button
                variant="subtle"
                color="gray"
                size="xs"
                onClick={() => sigRef.current?.clear()}
                styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
              >
                Clear
              </Button>
            </Group>
          </div>

          <Button
            type="submit"
            color="cyan"
            fullWidth
            size="lg"
            loading={submitting}
            mt="md"
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '18px' } }}
          >
            SUBMIT & SIGN
          </Button>
        </Stack>
      </form>
    );
  };

  return (
    <Box
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #050608 0%, #0e1117 50%, #050608 100%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <Card
        shadow="xl"
        padding="xl"
        radius="md"
        w="100%"
        maw={600}
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
        }}
      >
        <Stack gap="lg">
          <Center>
            <div>
              <Title
                order={1}
                style={{
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '4px',
                  fontSize: '36px',
                  textAlign: 'center',
                }}
                c="#e8edf2"
              >
                BARNARD<span style={{ color: '#00d4ff' }}>HQ</span>
              </Title>
              <Text
                ta="center"
                size="xs"
                c="#5a6478"
                style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '3px' }}
              >
                CUSTOMER ONBOARDING
              </Text>
            </div>
          </Center>

          {renderContent()}
        </Stack>
      </Card>
    </Box>
  );
}
