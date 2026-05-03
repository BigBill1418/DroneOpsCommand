/**
 * Customer-facing brand tokens.
 *
 * Lifted directly from the BarnardHQ LLC Terms of Service PDF
 * (`/data/uploads/tos/default_tos.pdf`, Rev 3) so every customer-
 * facing surface — `/client/*` portal pages, the `/tos/accept`
 * acceptance page, and the five customer transactional emails —
 * reads as one continuous BarnardHQ artifact.
 *
 * Operator-side surfaces (`MissionDetail`, `Dashboard`, etc.)
 * intentionally keep the existing operator-cyan `#00d4ff`. Do NOT
 * import these tokens from operator-only pages.
 *
 * v2.65.0 — customer portal theming pass (orchestration plan task 4).
 */

export const customerBrand = {
  // Page surfaces
  bgDeep: '#0e1117', // page background
  bgCard: '#161b22', // card / panel surface
  bgInput: '#0b0f15', // form input fill (slightly deeper than card)

  // Brand colors (TOS PDF palette — NOT operator palette)
  brandNavy: '#003858', // header strip, deep accents
  brandNavyDeep: '#011d2f', // hover/pressed states
  brandCyan: '#189cc6', // CTAs, accents, links — NOT the operator's #00d4ff
  brandCyanSoft: '#189cc633', // 20% alpha — borders/glows on hover

  // Status
  success: '#28a850',
  successSoft: '#28a85022',
  danger: '#dc3545',
  dangerSoft: '#dc354522',
  warning: '#f0a13a',

  // Text
  textPrimary: '#e8edf2',
  textBody: '#c0c8d4',
  textMuted: '#5a6478',
  textOnBrand: '#ffffff',

  // Borders / dividers
  border: '#1f2937',
  borderSoft: '#161b22',
  borderStrong: '#2a3444',

  // Typography
  fontDisplay: "'Bebas Neue', 'Arial Black', sans-serif",
  fontMono: "'Share Tech Mono', 'Courier New', monospace",
  fontBody: "'Rajdhani', 'Inter', system-ui, sans-serif",

  // Letter-spacing scale for the all-caps display heads
  trackTight: '1px',
  trackMid: '2px',
  trackWide: '3px',
  trackWider: '4px',

  // Component scales
  radiusCard: 10,
  radiusInput: 6,
  shadowCard: '0 1px 0 rgba(255,255,255,0.02) inset, 0 8px 28px rgba(0,0,0,0.32)',
} as const;

/**
 * The corporate footer line that appears on every customer-facing
 * page and email. Single source of truth — change here and it
 * propagates everywhere via `<CustomerLayout>` and the email
 * templates' shared snippet.
 */
export const FOOTER_LINE =
  'BarnardHQ LLC · Eugene, Oregon · FAA Part 107 Certified · barnardhq.com · DOC-001';

/** Convenience composed style objects so pages don't repeat themselves. */
export const customerStyles = {
  pageRoot: {
    minHeight: '100vh',
    background: customerBrand.bgDeep,
    color: customerBrand.textPrimary,
    display: 'flex',
    flexDirection: 'column' as const,
  },
  card: {
    background: customerBrand.bgCard,
    border: `1px solid ${customerBrand.border}`,
    boxShadow: customerBrand.shadowCard,
  },
  input: {
    background: customerBrand.bgInput,
    color: customerBrand.textPrimary,
    borderColor: customerBrand.border,
  },
  inputLabel: {
    color: customerBrand.textMuted,
    fontFamily: customerBrand.fontMono,
    fontSize: 11,
    letterSpacing: customerBrand.trackTight,
    textTransform: 'uppercase' as const,
  },
  display: {
    fontFamily: customerBrand.fontDisplay,
    letterSpacing: customerBrand.trackWide,
    color: customerBrand.textPrimary,
  },
  mono: {
    fontFamily: customerBrand.fontMono,
  },
  ctaButton: {
    background: customerBrand.brandCyan,
    color: customerBrand.brandNavyDeep,
    fontFamily: customerBrand.fontDisplay,
    letterSpacing: customerBrand.trackMid,
    fontWeight: 700,
  },
} as const;
