/**
 * Operator-local datetime formatting (ADR-0017).
 *
 * Flight instants are stored and transmitted as UTC. A flight's *calendar
 * date*, however, is the date in the operator's local timezone — a flight
 * flown at 20:27 PDT on June 1 is a June-1 flight even though that instant is
 * 03:27 UTC on June 2. All flight date/time rendering MUST go through these
 * helpers so the conversion is applied in exactly one place, independent of
 * the viewer's browser timezone.
 */

export const OPERATOR_TZ = 'America/Los_Angeles';

/**
 * Parse an API timestamp into a Date instant.
 *
 * Defensive: an ISO date-time with no timezone/offset is treated as UTC (the
 * storage convention), so formatting stays correct even if some endpoint
 * emits a naive timestamp. Date-only strings ("2026-06-01") are anchored at
 * local noon to avoid the classic midnight-UTC-rolls-back-a-day pitfall.
 */
function parseInstant(value: string): Date {
  const hasOffset = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  const isDateTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value);
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  if (isDateTime && !hasOffset) return new Date(value + 'Z');
  if (isDateOnly) return new Date(value + 'T12:00:00');
  return new Date(value);
}

/** Operator-local calendar date, e.g. "Jun 1, 2026". */
export function formatFlightDate(value?: string | null): string {
  if (!value) return '—';
  const d = parseInstant(value);
  if (isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: OPERATOR_TZ,
  });
}

/** Operator-local date + time, e.g. "Jun 1, 2026, 8:27 PM". */
export function formatFlightDateTime(value?: string | null): string {
  if (!value) return '—';
  const d = parseInstant(value);
  if (isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: OPERATOR_TZ,
  });
}
