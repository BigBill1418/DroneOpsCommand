import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

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
