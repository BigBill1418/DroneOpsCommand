import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse, delay } from 'msw';

import LeadPicker from '../LeadPicker';
import { missionTitleFromLead, leadPortalUrl } from '../../api/leads';
import TestProviders from '../../test/TestProviders';

const LEAD = {
  key: 'web-1', name: 'Ann Open', email: 'ann@acme.com', phone: '5415550100',
  organization: 'Acme', service: 'inspection', details: 'Roof survey',
  created_at: '2026-10-01T10:00:00Z', stage: 'new', is_open: true,
};
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('missionTitleFromLead', () => {
  it('uses service and organization', () => {
    expect(missionTitleFromLead(LEAD)).toBe('Inspection — Acme');
  });
  it('falls back to name and a generic service', () => {
    expect(missionTitleFromLead({ ...LEAD, organization: '', service: '' })).toBe('Website lead — Ann Open');
  });
  it('builds the portal deep link', () => {
    expect(leadPortalUrl('cold-7')).toBe('https://marketing.barnardhq.com/outbound/leads?lead=cold-7');
  });
});

describe('LeadPicker', () => {
  it('renders nothing when the feature is off', async () => {
    server.use(http.get('*/api/leads/status', () => HttpResponse.json({ enabled: false })));
    const { container } = render(<TestProviders><LeadPicker onPick={vi.fn()} /></TestProviders>);
    await waitFor(() => expect(container.querySelector('[data-testid="lead-picker"]')).toBeNull());
  });

  it('lists leads and returns the detail on pick', async () => {
    const onPick = vi.fn();
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.get('*/api/leads', () => HttpResponse.json({ leads: [LEAD] })),
      http.get('*/api/leads/web-1', () =>
        HttpResponse.json({ lead: LEAD, matching_customer: null })),
    );
    render(<TestProviders><LeadPicker onPick={onPick} /></TestProviders>);
    const input = await screen.findByPlaceholderText(/search website leads/i);
    await userEvent.click(input);
    await userEvent.click(await screen.findByText(/Ann Open — Acme/));
    await waitFor(() => expect(onPick).toHaveBeenCalledWith({ lead: LEAD, matching_customer: null }));
  });

  it('shows "Leads unavailable" when the list call fails', async () => {
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.get('*/api/leads', () => HttpResponse.json({ detail: 'Leads unavailable' }, { status: 503 })),
    );
    render(<TestProviders><LeadPicker onPick={vi.fn()} /></TestProviders>);
    expect(await screen.findByText(/fill the form in by hand/i)).toBeInTheDocument();
  });
});

describe('LeadPicker — LD-2 M-6', () => {
  it('a slow earlier search cannot overwrite a newer one', async () => {
    const OLD = { ...LEAD, key: 'web-2', name: 'Old Result' };
    const NEW = { ...LEAD, key: 'web-3', name: 'New Result' };
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.get('*/api/leads', async ({ request }) => {
        const q = new URL(request.url).searchParams.get('q') ?? '';
        if (q === 'ol') { await delay(1500); return HttpResponse.json({ leads: [OLD] }); }
        if (q === 'olx') return HttpResponse.json({ leads: [NEW] });
        return HttpResponse.json({ leads: [] });
      }),
    );
    render(<TestProviders><LeadPicker onPick={vi.fn()} /></TestProviders>);
    const input = await screen.findByPlaceholderText(/search website leads/i);
    await userEvent.click(input);
    await userEvent.type(input, 'ol');
    await new Promise((r) => setTimeout(r, 450)); // debounce fires the slow 'ol' request
    await userEvent.type(input, 'x');
    await screen.findByText(/New Result/, {}, { timeout: 3000 });
    await new Promise((r) => setTimeout(r, 1600)); // let the slow 'ol' response land
    expect(screen.queryByText(/Old Result/)).toBeNull();
    expect(screen.getByText(/New Result/)).toBeInTheDocument();
  }, 10000);
});
