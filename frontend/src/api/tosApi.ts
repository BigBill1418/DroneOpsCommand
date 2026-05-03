/**
 * TOS-acceptance API client.
 *
 * Uses a fresh axios instance — NOT the operator-app `client.ts` —
 * because the public /tos/accept page must never send an
 * Authorization header. The operator client's request interceptor
 * would otherwise attach a stale (or wrong-tenant) JWT to a public
 * endpoint and trigger the 401 → refresh-token redirect loop.
 *
 * ADR-0010.
 */
import axios from 'axios';

const tosClient = axios.create({
  baseURL: '/api/tos',
  timeout: 30000,
  // Explicitly do NOT attach localStorage tokens here.
});

export interface TosAcceptancePayload {
  full_name: string;
  email: string;
  company: string;
  title: string;
  confirm: true;
  customer_id?: string | null;
  intake_token?: string | null;
}

export interface TosAcceptanceResult {
  id: string;
  audit_id: string;
  accepted_at: string;
  template_version: string;
  template_sha256: string;
  signed_sha256: string;
  download_url: string;
}

/** Source URL for the embedded PDF iframe. */
export const getTemplatePdfUrl = (): string => '/api/tos/template';

/** POST the customer's typed-name + checkbox acceptance. */
export async function acceptTos(
  payload: TosAcceptancePayload,
): Promise<TosAcceptanceResult> {
  const { data } = await tosClient.post<TosAcceptanceResult>('/accept', payload);
  return data;
}
