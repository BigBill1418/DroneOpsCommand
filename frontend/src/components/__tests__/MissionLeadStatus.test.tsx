import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionLeadStatus from '../MissionLeadStatus';
import TestProviders from '../../test/TestProviders';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('MissionLeadStatus', () => {
  it('renders nothing for a non-lead source_ref', () => {
    const { container } = render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="INV-9" leadWritebackAt={null} onRetried={vi.fn()} />
    </TestProviders>);
    expect(container.querySelector('[data-testid="mission-lead-status"]')).toBeNull();
  });

  it('shows the View lead link and no warning once written back', () => {
    render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="web-1" leadWritebackAt="2026-10-05T00:00:00Z" onRetried={vi.fn()} />
    </TestProviders>);
    expect(screen.getByRole('link', { name: /view lead/i })).toHaveAttribute(
      'href', 'https://marketing.barnardhq.com/outbound/leads?lead=web-1');
    expect(screen.queryByText(/not marked won/i)).toBeNull();
  });

  it('retries via the mission route and calls onRetried on success', async () => {
    let hit = '';
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.post('*/api/leads/missions/m1/mark-won', ({ request }) => {
        hit = request.url;
        return HttpResponse.json({ ok: true });
      }),
    );
    const onRetried = vi.fn();
    render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="web-1" leadWritebackAt={null} onRetried={onRetried} />
    </TestProviders>);
    expect(await screen.findByText(/lead not marked won/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(onRetried).toHaveBeenCalled());
    expect(hit).toContain('/api/leads/missions/m1/mark-won');
  });

  it('LD-2 M-8: hides Retry when the integration is disabled', async () => {
    let statusCalls = 0;
    server.use(http.get('*/api/leads/status', () => { statusCalls += 1; return HttpResponse.json({ enabled: false }); }));
    render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="web-1" leadWritebackAt={null} onRetried={vi.fn()} />
    </TestProviders>);
    await waitFor(() => expect(statusCalls).toBe(1));
    expect(screen.getByRole('link', { name: /view lead/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });
});
