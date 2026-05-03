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
import { useState, useEffect } from 'react';
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

  // v2.66.0 Fix #7 — mobile-aware iframe height. The default
  // max(70vh, 800px) is too tall on phones (forces awkward outer scroll
  // with the PDF only partially visible) and too short on the iPad
  // landscape end (clips the 5-page doc). matchMedia tracks live
  // orientation flips without forcing a reload.
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches,
  );
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mql = window.matchMedia('(max-width: 768px)');
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

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
      // v2.66.2 hotfix — surface FastAPI/Pydantic validation detail to the
      // user instead of always showing the generic "Acceptance failed".
      // The 2026-05-03 production incident burned six retries from one
      // paying customer because the toast read "Acceptance failed" while
      // the real error was a 422 from a route-binding bug. The customer
      // had no way to know retrying would not help.
      //
      // Pydantic 422 detail is a list of {loc, msg, type, input}; we
      // join the user-actionable msg fields when present, fall back to a
      // single string detail (HTTPException), and finally to the axios
      // message. The full error object is always console.error'd so the
      // operator can pull it from the customer's browser if needed.
      const e = err as {
        response?: {
          status?: number;
          data?: { detail?: string | Array<{ msg?: string; loc?: unknown[] }> };
        };
        message?: string;
      };
      console.error('[TOS] accept failed:', {
        status: e.response?.status,
        detail: e.response?.data?.detail,
        raw: err,
      });
      const detail = e.response?.data?.detail;
      let userMsg: string;
      if (Array.isArray(detail)) {
        const msgs = detail
          .map((d) => (d && typeof d.msg === 'string' ? d.msg : ''))
          .filter(Boolean);
        userMsg = msgs.length
          ? `Please check your input: ${msgs.join('; ')}`
          : 'The form was rejected by the server. Please refresh the page and try again. If this keeps happening, contact your operator.';
      } else if (typeof detail === 'string' && detail.trim()) {
        userMsg = detail;
      } else if (typeof e.message === 'string' && e.message.trim()) {
        userMsg = e.message;
      } else {
        userMsg = 'Could not record your acceptance. Please refresh the page and try again. If this keeps happening, contact your operator.';
      }
      // Append HTTP status so the customer can quote it to support.
      if (e.response?.status) {
        userMsg = `${userMsg} (HTTP ${e.response.status})`;
      }
      setError(userMsg);
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
            {/* v2.66.2 copy update — drop the "ACCEPTED" badge in favor of
                a warmer welcome heading. Two-sentence body replaces the
                "What happens next" framing card (no premature payment
                mention, no defensive "if you don't hear back" line). */}
            <Title
              order={1}
              style={{
                ...customerStyles.display,
                color: customerBrand.brandCyan,
                letterSpacing: customerBrand.trackWider,
                fontSize: 'clamp(32px, 5.5vw, 48px)',
              }}
            >
              THANK YOU. WELCOME TO BARNARDHQ.
            </Title>
            <Text
              style={{
                color: customerBrand.textBody,
                fontFamily: customerBrand.fontBody,
                lineHeight: 1.65,
              }}
            >
              Your operator will follow up shortly with a{' '}
              <strong style={{ color: customerBrand.brandCyan }}>
                secure portal link
              </strong>{' '}
              where you can review your mission and stay updated on its
              progress.
            </Text>

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

      {/* v2.66.0 Fix #7 — mobile caption: customers were missing that the
          PDF scrolls *inside* the iframe (their thumb tried to scroll the
          page). Caption + bigger iframe makes that obvious. */}
      {isMobile && (
        <Text
          mt={4}
          mb={6}
          style={{
            color: customerBrand.textMuted,
            fontFamily: customerBrand.fontMono,
            fontSize: 11,
            letterSpacing: customerBrand.trackTight,
            textAlign: 'center',
          }}
        >
          Scroll inside the document to read all pages
        </Text>
      )}

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
            // v2.66.0 Fix #7 — phones get ~90vh (room to read);
            // desktops keep the v2.65.0 max(70vh, 800px) cap.
            height: isMobile ? 'max(500px, calc(90vh - 24px))' : 'min(70vh, 800px)',
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
