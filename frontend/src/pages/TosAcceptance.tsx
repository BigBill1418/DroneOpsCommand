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
 * Customer-facing — wrapped in <CustomerLayout> with the BarnardHQ
 * brand pass (v2.65.0). The TOS PDF iframe sits on a white surface
 * inside the dark themed shell so the document itself remains
 * legible while every chrome/affordance reads as BarnardHQ.
 *
 * ADR-0010.
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Anchor,
  Box,
  Button,
  Checkbox,
  Code,
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
import CustomerLayout from '../components/CustomerLayout';
import { customerBrand, customerStyles } from '../lib/customerTheme';

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

  // ── Post-acceptance success view ─────────────────────────────
  if (result) {
    return (
      <CustomerLayout
        maxWidth={720}
        contextSlot={<span style={{ textTransform: 'uppercase' }}>Terms · Accepted</span>}
      >
        <Paper p="xl" radius="md" style={customerStyles.card}>
          <Stack gap="md">
            <Title
              order={1}
              style={{
                ...customerStyles.display,
                color: customerBrand.success,
                letterSpacing: customerBrand.trackWider,
                fontSize: 'clamp(36px, 6vw, 56px)',
              }}
            >
              ACCEPTED
            </Title>
            <Text style={{ color: customerBrand.textBody, fontFamily: customerBrand.fontBody }}>
              Your acceptance is recorded. Audit reference:
            </Text>
            <Code
              block
              style={{
                background: customerBrand.bgDeep,
                color: customerBrand.brandCyan,
                fontFamily: customerBrand.fontMono,
                fontSize: 13,
                padding: '14px 16px',
                border: `1px solid ${customerBrand.border}`,
                borderLeft: `3px solid ${customerBrand.brandCyan}`,
              }}
            >
              {result.audit_id}
            </Code>
            <Text
              size="sm"
              style={{
                color: customerBrand.textMuted,
                fontFamily: customerBrand.fontBody,
                lineHeight: 1.6,
              }}
            >
              A signed copy has been emailed to you. You can also download
              it directly below. The document is locked and tamper-evident
              via SHA-256.
            </Text>
            <Group>
              <Anchor href={result.download_url} download underline="never">
                <Button
                  size="md"
                  styles={{
                    root: {
                      background: customerBrand.brandCyan,
                      color: customerBrand.brandNavyDeep,
                      fontFamily: customerBrand.fontDisplay,
                      letterSpacing: customerBrand.trackMid,
                      fontWeight: 700,
                    },
                  }}
                >
                  DOWNLOAD SIGNED COPY
                </Button>
              </Anchor>
            </Group>
          </Stack>
        </Paper>
      </CustomerLayout>
    );
  }

  // ── Pre-acceptance form ──────────────────────────────────────
  return (
    <CustomerLayout
      maxWidth={1040}
      contextSlot={<span style={{ textTransform: 'uppercase' }}>Terms of Service · DOC-001</span>}
    >
      <Box>
        <Title
          order={1}
          style={{
            ...customerStyles.display,
            color: customerBrand.brandCyan,
            letterSpacing: customerBrand.trackWider,
            fontSize: 'clamp(28px, 5vw, 40px)',
          }}
        >
          BARNARDHQ LLC &mdash; TERMS OF SERVICE
        </Title>
        <Text
          mt={6}
          style={{
            color: customerBrand.textMuted,
            fontFamily: customerBrand.fontMono,
            fontSize: 12,
            letterSpacing: customerBrand.trackTight,
          }}
        >
          Review the agreement below, fill in your information, and accept to proceed.
        </Text>
      </Box>

      {/* TOS PDF — white surface inside the dark shell, framed by a navy
          border + cyan accent bar to anchor it visually. */}
      <Paper
        radius="md"
        style={{
          background: '#ffffff',
          border: `1px solid ${customerBrand.border}`,
          borderTop: `3px solid ${customerBrand.brandCyan}`,
          overflow: 'hidden',
          padding: 0,
        }}
      >
        <iframe
          src={getTemplatePdfUrl()}
          title="Terms of Service"
          style={{
            width: '100%',
            height: 'min(70vh, 800px)',
            border: 0,
            display: 'block',
          }}
        />
      </Paper>

      <Paper p="xl" radius="md" style={customerStyles.card}>
        <form onSubmit={onSubmit}>
          <Stack gap="md">
            <Title
              order={3}
              style={{
                ...customerStyles.display,
                fontSize: 22,
              }}
            >
              YOUR INFORMATION
            </Title>

            <Group grow>
              <TextInput
                label="Full legal name"
                required
                value={fullName}
                onChange={(e) => setFullName(e.currentTarget.value)}
                autoComplete="name"
                maxLength={120}
                styles={{ input: customerStyles.input, label: customerStyles.inputLabel }}
              />
              <TextInput
                label="Email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.currentTarget.value)}
                autoComplete="email"
                maxLength={254}
                styles={{ input: customerStyles.input, label: customerStyles.inputLabel }}
              />
            </Group>
            <Group grow>
              <TextInput
                label="Company / entity (optional)"
                value={company}
                onChange={(e) => setCompany(e.currentTarget.value)}
                autoComplete="organization"
                maxLength={120}
                styles={{ input: customerStyles.input, label: customerStyles.inputLabel }}
              />
              <TextInput
                label="Title (optional)"
                value={title}
                onChange={(e) => setTitle(e.currentTarget.value)}
                autoComplete="organization-title"
                maxLength={80}
                styles={{ input: customerStyles.input, label: customerStyles.inputLabel }}
              />
            </Group>

            <Paper
              p="md"
              radius="sm"
              style={{
                background: customerBrand.bgDeep,
                border: `1px solid ${customerBrand.border}`,
                borderLeft: `3px solid ${customerBrand.brandCyan}`,
              }}
            >
              <Checkbox
                color="cyan"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.currentTarget.checked)}
                label={
                  <Text
                    size="sm"
                    style={{
                      color: customerBrand.textBody,
                      fontFamily: customerBrand.fontBody,
                      lineHeight: 1.6,
                    }}
                  >
                    I have read and agree to the BarnardHQ LLC Terms of
                    Service. By checking this box and clicking{' '}
                    <strong style={{ color: customerBrand.brandCyan }}>
                      Accept &amp; Sign
                    </strong>
                    , I am providing my electronic signature under the
                    federal E-SIGN Act and Oregon&rsquo;s Uniform
                    Electronic Transactions Act (ORS Ch. 84).
                  </Text>
                }
              />
            </Paper>

            {error && (
              <Text
                size="sm"
                style={{
                  color: customerBrand.danger,
                  fontFamily: customerBrand.fontMono,
                  background: '#1a0d0f',
                  border: `1px solid ${customerBrand.danger}`,
                  borderRadius: 6,
                  padding: '10px 14px',
                }}
              >
                {error}
              </Text>
            )}

            <Group>
              <Button
                type="submit"
                size="md"
                loading={submitting}
                disabled={submitting || !confirmed}
                styles={{
                  root: {
                    background: customerBrand.brandCyan,
                    color: customerBrand.brandNavyDeep,
                    fontFamily: customerBrand.fontDisplay,
                    letterSpacing: customerBrand.trackMid,
                    fontWeight: 700,
                    fontSize: 16,
                    paddingInline: 28,
                  },
                }}
              >
                {submitting ? 'SIGNING…' : 'ACCEPT & SIGN'}
              </Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </CustomerLayout>
  );
}
