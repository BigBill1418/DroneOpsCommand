/**
 * Public TOS-acceptance page.
 *
 * URL: /tos/accept?token=<intake_token>&customer_id=<uuid>
 *
 * Both query params are optional — a cold visitor can also reach
 * this page directly. When present, the params correlate the new
 * acceptance row back to the customer profile created by the intake
 * flow.
 *
 * Themed in Mantine to match the rest of the client-facing surface
 * (dark navy/cyan; the design doc §5 promises a deeper theming pass
 * landed by the parallel theming agent — this page only covers the
 * functional shell).
 *
 * ADR-0010.
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Anchor,
  Box,
  Button,
  Center,
  Checkbox,
  Code,
  Container,
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import {
  acceptTos,
  getTemplatePdfUrl,
  type TosAcceptanceResult,
} from '../api/tosApi';

const PANEL_BG = '#0e1117';
const PANEL_BORDER = '#1a1f2e';
const TEXT_DIM = '#5a6478';
const TEXT = '#e8edf2';
const ACCENT = '#189cc6';

export default function TosAcceptance() {
  const [params] = useSearchParams();
  const intakeToken = params.get('token');
  const customerId = params.get('customer_id');

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [company, setCompany] = useState('');
  const [title, setTitle] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TosAcceptanceResult | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!confirmed) {
      setError('Please confirm you have read and agree to the Terms.');
      return;
    }
    if (fullName.trim().length < 2) {
      setError('Please enter your full legal name.');
      return;
    }
    if (!email.trim()) {
      setError('Please enter your email address.');
      return;
    }
    setSubmitting(true);
    try {
      const data = await acceptTos({
        full_name: fullName.trim(),
        email: email.trim(),
        company: company.trim(),
        title: title.trim(),
        confirm: true,
        intake_token: intakeToken,
        customer_id: customerId,
      });
      setResult(data);
    } catch (err: unknown) {
      // axios error shape — pull `detail` if present, else fall back.
      const e = err as {
        response?: { data?: { detail?: string } };
        message?: string;
      };
      const detail =
        e.response?.data?.detail ?? e.message ?? 'Acceptance failed';
      setError(typeof detail === 'string' ? detail : 'Acceptance failed');
      console.error('[TOS] accept failed:', err);
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <Center mih="100vh" p="md" style={{ background: '#050608' }}>
        <Container size="md" w="100%">
          <Paper
            p="xl"
            radius="md"
            style={{ background: PANEL_BG, border: `1px solid ${PANEL_BORDER}` }}
          >
            <Stack gap="md">
              <Title
                order={1}
                style={{
                  fontFamily: "'Bebas Neue', sans-serif",
                  letterSpacing: '4px',
                  color: ACCENT,
                }}
              >
                ACCEPTED
              </Title>
              <Text c={TEXT}>
                Your acceptance is recorded. Audit reference:
              </Text>
              <Code
                block
                style={{
                  background: '#050608',
                  color: ACCENT,
                  fontFamily: "'Share Tech Mono', monospace",
                }}
              >
                {result.audit_id}
              </Code>
              <Text c={TEXT_DIM} size="sm">
                A signed copy has been emailed to you. You can also download
                it directly below. The document is locked and tamper-evident
                via SHA-256.
              </Text>
              <Group>
                <Anchor
                  href={result.download_url}
                  download
                  underline="never"
                >
                  <Button color="cyan" variant="filled">
                    Download signed copy
                  </Button>
                </Anchor>
              </Group>
              <Text c={TEXT_DIM} size="xs" mt="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                BarnardHQ LLC &middot; Eugene, Oregon &middot; FAA Part 107 Certified &middot;
                barnardhq.com &middot; DOC-001
              </Text>
            </Stack>
          </Paper>
        </Container>
      </Center>
    );
  }

  return (
    <Box mih="100vh" p="md" style={{ background: '#050608' }}>
      <Container size="lg">
        <Stack gap="lg">
          <Box>
            <Title
              order={1}
              style={{
                fontFamily: "'Bebas Neue', sans-serif",
                letterSpacing: '4px',
                color: ACCENT,
              }}
            >
              BARNARDHQ LLC &mdash; TERMS OF SERVICE
            </Title>
            <Text c={TEXT_DIM} mt={4}>
              Review the agreement below, fill in your information, and accept to proceed.
            </Text>
          </Box>

          <Paper
            p={0}
            radius="md"
            style={{
              background: '#ffffff',
              border: `1px solid ${PANEL_BORDER}`,
              overflow: 'hidden',
            }}
          >
            <iframe
              src={getTemplatePdfUrl()}
              title="Terms of Service"
              style={{ width: '100%', height: 700, border: 0, display: 'block' }}
            />
          </Paper>

          <Paper
            p="xl"
            radius="md"
            style={{ background: PANEL_BG, border: `1px solid ${PANEL_BORDER}` }}
          >
            <form onSubmit={onSubmit}>
              <Stack gap="md">
                <Group grow>
                  <TextInput
                    label="Full legal name"
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.currentTarget.value)}
                    autoComplete="name"
                    maxLength={120}
                    styles={{ label: { color: ACCENT, letterSpacing: '1px', textTransform: 'uppercase', fontSize: 11 } }}
                  />
                  <TextInput
                    label="Email"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.currentTarget.value)}
                    autoComplete="email"
                    maxLength={254}
                    styles={{ label: { color: ACCENT, letterSpacing: '1px', textTransform: 'uppercase', fontSize: 11 } }}
                  />
                </Group>
                <Group grow>
                  <TextInput
                    label="Company / entity (optional)"
                    value={company}
                    onChange={(e) => setCompany(e.currentTarget.value)}
                    autoComplete="organization"
                    maxLength={120}
                    styles={{ label: { color: ACCENT, letterSpacing: '1px', textTransform: 'uppercase', fontSize: 11 } }}
                  />
                  <TextInput
                    label="Title (optional)"
                    value={title}
                    onChange={(e) => setTitle(e.currentTarget.value)}
                    autoComplete="organization-title"
                    maxLength={80}
                    styles={{ label: { color: ACCENT, letterSpacing: '1px', textTransform: 'uppercase', fontSize: 11 } }}
                  />
                </Group>

                <Paper
                  p="md"
                  radius="sm"
                  style={{ background: '#050608', border: `1px solid ${PANEL_BORDER}` }}
                >
                  <Checkbox
                    color="cyan"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.currentTarget.checked)}
                    label={
                      <Text c={TEXT} size="sm">
                        I have read and agree to the BarnardHQ LLC Terms of
                        Service. By checking this box and clicking{' '}
                        <strong>Accept &amp; Sign</strong>, I am providing my
                        electronic signature under the federal E-SIGN Act and
                        Oregon&rsquo;s Uniform Electronic Transactions Act
                        (ORS Ch. 84).
                      </Text>
                    }
                  />
                </Paper>

                {error && (
                  <Text c="red.4" size="sm">
                    {error}
                  </Text>
                )}

                <Group>
                  <Button
                    type="submit"
                    color="cyan"
                    size="md"
                    loading={submitting}
                    disabled={submitting || !confirmed}
                  >
                    {submitting ? 'Signing…' : 'Accept & Sign'}
                  </Button>
                </Group>
              </Stack>
            </form>
          </Paper>

          <Text
            c={TEXT_DIM}
            size="xs"
            ta="center"
            style={{ fontFamily: "'Share Tech Mono', monospace" }}
          >
            BarnardHQ LLC &middot; Eugene, Oregon &middot; FAA Part 107 Certified
            &middot; barnardhq.com &middot; DOC-001
          </Text>
        </Stack>
      </Container>
    </Box>
  );
}
