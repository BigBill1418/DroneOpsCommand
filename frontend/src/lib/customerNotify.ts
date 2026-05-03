/**
 * Branded Mantine notification helpers for customer-facing pages.
 *
 * The post-Stripe-redirect flow in `ClientMissionDetail` fires three
 * notification kinds — payment confirmed (success), still-processing
 * (warning), payment error (danger) — and the JWT-token-validation
 * page in `ClientPortal` fires "Access Denied". All of them now go
 * through these helpers so the styling matches the BarnardHQ brand
 * (Bebas Neue title, brand cyan/green/red accents, navy borders) and
 * does not look like a stock Mantine toast.
 *
 * v2.65.0 — customer portal theming pass.
 */
import { notifications } from '@mantine/notifications';
import { customerBrand } from './customerTheme';

type Kind = 'success' | 'warning' | 'danger' | 'info';

const PALETTE: Record<Kind, { color: string; bg: string; border: string }> = {
  success: {
    color: customerBrand.success,
    bg: '#0e1a13',
    border: customerBrand.success,
  },
  warning: {
    color: customerBrand.warning,
    bg: '#1a1408',
    border: customerBrand.warning,
  },
  danger: {
    color: customerBrand.danger,
    bg: '#1a0d0f',
    border: customerBrand.danger,
  },
  info: {
    color: customerBrand.brandCyan,
    bg: customerBrand.bgCard,
    border: customerBrand.brandCyan,
  },
};

const MANTINE_COLOR: Record<Kind, string> = {
  success: 'green',
  warning: 'yellow',
  danger: 'red',
  info: 'cyan',
};

export interface NotifyArgs {
  title: string;
  message: string;
  kind?: Kind;
  /** Auto-close ms. Default 5000. Pass false to require manual dismiss. */
  autoClose?: number | false;
}

export function customerNotify({
  title,
  message,
  kind = 'info',
  autoClose = 5000,
}: NotifyArgs) {
  const p = PALETTE[kind];
  notifications.show({
    title,
    message,
    color: MANTINE_COLOR[kind],
    autoClose,
    withBorder: true,
    styles: {
      root: {
        background: p.bg,
        border: `1px solid ${p.border}`,
        borderLeft: `4px solid ${p.border}`,
      },
      title: {
        color: p.color,
        fontFamily: customerBrand.fontDisplay,
        letterSpacing: customerBrand.trackMid,
        fontSize: 16,
        textTransform: 'uppercase',
      },
      description: {
        color: customerBrand.textBody,
        fontFamily: customerBrand.fontBody,
        fontSize: 13,
        lineHeight: 1.5,
      },
      closeButton: {
        color: customerBrand.textMuted,
      },
    },
  });
}
