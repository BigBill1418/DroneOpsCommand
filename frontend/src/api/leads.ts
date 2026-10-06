// ADR-0050 — website-lead prefill. Thin wrappers over DOC's /api/leads proxy.
import api from './client';

export interface DocLead {
  key: string;
  name: string;
  email: string;
  phone: string;
  organization: string;
  service: string;
  details: string;
  created_at: string | null;
  stage: string;
  is_open: boolean;
}

export interface LeadDetail {
  lead: DocLead;
  matching_customer: { id: string; name: string } | null;
}

export const LEAD_KEY_RE = /^(web|cold)-\d{1,12}$/;

export async function fetchLeadsEnabled(): Promise<boolean> {
  try {
    const r = await api.get('/leads/status');
    return r.data?.enabled === true;
  } catch {
    return false;
  }
}

export async function listLeads(q: string, includeClosed: boolean): Promise<DocLead[]> {
  const r = await api.get('/leads', { params: { q: q || undefined, include_closed: includeClosed } });
  return r.data.leads as DocLead[];
}

export async function getLead(key: string): Promise<LeadDetail> {
  const r = await api.get(`/leads/${encodeURIComponent(key)}`);
  return r.data as LeadDetail;
}

export async function retryLeadWon(key: string, missionId: string): Promise<void> {
  await api.post(`/leads/${encodeURIComponent(key)}/mark-won`, null, { params: { mission_id: missionId } });
}

function titleCase(s: string): string {
  return s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function missionTitleFromLead(lead: DocLead): string {
  const what = lead.service.trim() ? titleCase(lead.service.trim()) : 'Website lead';
  const who = lead.organization.trim() || lead.name.trim() || lead.email.trim();
  return who ? `${what} — ${who}` : what;
}

export function leadPortalUrl(key: string): string {
  return `https://marketing.barnardhq.com/outbound/leads?lead=${encodeURIComponent(key)}`;
}
