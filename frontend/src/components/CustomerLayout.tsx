/**
 * CustomerLayout — shared visual shell for every customer-facing page.
 *
 * Wraps `/client/*` and `/tos/accept` content in a single, branded
 * frame so the customer experience reads as one continuous BarnardHQ
 * artifact (matching the TOS PDF's visual identity):
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │  ▓ BARNARDHQ              [right slot — page context]│  ← navy header strip
 *   ├──────────────────────────────────────────────────────┤
 *   │                                                       │
 *   │              [page content goes here]                │
 *   │                                                       │
 *   ├──────────────────────────────────────────────────────┤
 *   │  BarnardHQ LLC · Eugene, OR · FAA Part 107 · DOC-001 │  ← Share Tech Mono footer
 *   └──────────────────────────────────────────────────────┘
 *
 * Usage:
 *   <CustomerLayout
 *     contextSlot={<Text style={mono}>MISSION 1A2B</Text>}
 *     maxWidth={720}
 *   >
 *     <YourPageContent />
 *   </CustomerLayout>
 *
 * Responsive: header collapses to a single line on phones; content
 * area uses padding that scales from `md` on mobile to `xl` on
 * desktop. All children render inside a constrained `maxWidth` (default
 * 960) centered column.
 *
 * v2.65.0 — customer portal theming pass.
 */
import { ReactNode } from 'react';
import { Box, Group, Stack, Text } from '@mantine/core';
import { customerBrand, FOOTER_LINE } from '../lib/customerTheme';

export interface CustomerLayoutProps {
  /** Page content. Rendered inside a centered, max-width column. */
  children: ReactNode;
  /**
   * Optional right-aligned header slot for page context — e.g.
   * "MISSION COMPLETE", an audit ID, or a sign-out button. Rendered
   * in Share Tech Mono at the right edge of the navy header strip.
   */
  contextSlot?: ReactNode;
  /** Centered column max width. Default 960. */
  maxWidth?: number | string;
  /** Override the body padding. Default `lg`. */
  bodyPadding?: 'sm' | 'md' | 'lg' | 'xl';
}

/** The wordmark — solid styled text in Bebas Neue rather than an SVG
 *  so it inherits theming and doesn't require an asset round-trip
 *  (matches the TOS PDF page-1 header treatment). */
function Wordmark() {
  return (
    <Group gap={10} align="baseline" wrap="nowrap">
      <Text
        component="span"
        style={{
          fontFamily: customerBrand.fontDisplay,
          letterSpacing: customerBrand.trackWide,
          fontSize: 'clamp(20px, 3.2vw, 26px)',
          color: customerBrand.textOnBrand,
          lineHeight: 1,
        }}
      >
        BARNARDHQ
      </Text>
      <Text
        component="span"
        visibleFrom="sm"
        style={{
          fontFamily: customerBrand.fontMono,
          letterSpacing: customerBrand.trackWider,
          fontSize: 10,
          color: customerBrand.brandCyan,
          textTransform: 'uppercase',
          lineHeight: 1,
        }}
      >
        DRONE OPERATIONS
      </Text>
    </Group>
  );
}

const PAD_BY_KEY = { sm: '12px', md: '20px', lg: '28px', xl: '40px' } as const;

export default function CustomerLayout({
  children,
  contextSlot,
  maxWidth = 960,
  bodyPadding = 'lg',
}: CustomerLayoutProps) {
  return (
    <Box
      style={{
        minHeight: '100vh',
        background: customerBrand.bgDeep,
        color: customerBrand.textPrimary,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* ── Header strip ─────────────────────────────────────────── */}
      <Box
        component="header"
        style={{
          background: customerBrand.brandNavy,
          borderBottom: `2px solid ${customerBrand.brandCyan}`,
          // Subtle inset shine to lift the strip off the page.
          boxShadow: '0 1px 0 rgba(255,255,255,0.04) inset, 0 6px 18px rgba(0,0,0,0.35)',
          padding: '14px clamp(16px, 4vw, 32px)',
        }}
      >
        <Group
          justify="space-between"
          align="center"
          wrap="nowrap"
          style={{ maxWidth, margin: '0 auto', width: '100%' }}
        >
          <Wordmark />
          {contextSlot && (
            <Box
              style={{
                color: customerBrand.brandCyan,
                fontFamily: customerBrand.fontMono,
                fontSize: 12,
                letterSpacing: customerBrand.trackTight,
                textAlign: 'right',
                minWidth: 0, // allow ellipsis on small screens
              }}
            >
              {contextSlot}
            </Box>
          )}
        </Group>
      </Box>

      {/* ── Body ─────────────────────────────────────────────────── */}
      <Box
        component="main"
        style={{
          flex: 1,
          padding: `${PAD_BY_KEY[bodyPadding]} clamp(12px, 4vw, 32px)`,
          width: '100%',
        }}
      >
        <Stack
          gap="lg"
          style={{
            maxWidth,
            margin: '0 auto',
            width: '100%',
          }}
        >
          {children}
        </Stack>
      </Box>

      {/* ── Footer line ──────────────────────────────────────────── */}
      <Box
        component="footer"
        style={{
          padding: '18px clamp(16px, 4vw, 32px)',
          borderTop: `1px solid ${customerBrand.border}`,
          background: '#080b10',
        }}
      >
        <Text
          ta="center"
          style={{
            color: customerBrand.textMuted,
            fontFamily: customerBrand.fontMono,
            fontSize: 11,
            letterSpacing: customerBrand.trackTight,
            lineHeight: 1.6,
          }}
        >
          {FOOTER_LINE}
        </Text>
      </Box>
    </Box>
  );
}
