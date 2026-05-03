/**
 * MissionDetail (Hub) contract test (v2.67.0).
 *
 * Per spec §11 done-definition + §8.5 lockdown semantics:
 *   * Renders 5 facet cards (Details, Flights, Images, Report, Invoice)
 *   * Mark SENT button is hidden when status is not COMPLETED
 *   * All Edit buttons are disabled when status is SENT
 *   * Reopen Mission button is visible when status is SENT
 *
 * Per ADR-0013 — exercises the Hub through MemoryRouter + msw'd API
 * (no SimpleNamespace bypass).
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionDetail from '../MissionDetail';
import TestProviders from '../../test/TestProviders';

const MISSION_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const CUSTOMER_ID = 'cccccccc-bbbb-aaaa-9999-888888888888';

function makeHandlers(missionStatus: string) {
  return [
    http.get(`*/api/missions/${MISSION_ID}`, () =>
      HttpResponse.json({
        id: MISSION_ID,
        customer_id: CUSTOMER_ID,
        title: 'Hub-render smoke',
        mission_type: 'inspection',
        description: null,
        mission_date: '2026-05-03',
        location_name: 'Boston',
        area_coordinates: null,
        status: missionStatus,
        is_billable: true,
        unas_folder_path: null,
        download_link_url: null,
        download_link_expires_at: null,
        client_notes: null,
        created_at: '2026-05-01T00:00:00Z',
        updated_at: '2026-05-03T00:00:00Z',
        flights: [],
        images: [],
      }),
    ),
    http.get(`*/api/missions/${MISSION_ID}/report`, () =>
      HttpResponse.json({
        id: 'r1',
        mission_id: MISSION_ID,
        user_narrative: null,
        llm_generated_content: null,
        final_content: null,
      }),
    ),
    http.get(`*/api/missions/${MISSION_ID}/invoice`, () =>
      HttpResponse.json({
        id: 'inv-1',
        mission_id: MISSION_ID,
        invoice_number: 'BARNARDHQ-2026-0001',
        subtotal: 100,
        tax_rate: 0,
        tax_amount: 0,
        total: 100,
        paid_in_full: false,
        notes: null,
        line_items: [],
        deposit_required: true,
        deposit_amount: 50,
        deposit_paid: false,
      }),
    ),
    http.get(`*/api/customers/${CUSTOMER_ID}`, () =>
      HttpResponse.json({
        id: CUSTOMER_ID,
        name: 'Casey Operator',
        email: 'casey@example.com',
        company: 'Acme',
        tos_signed: true,
      }),
    ),
  ];
}

const server = setupServer(...makeHandlers('in_progress'));

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

vi.mock('react-router-dom', async () => {
  const mod: any = await vi.importActual('react-router-dom');
  return {
    ...mod,
    useParams: () => ({ id: MISSION_ID }),
    useNavigate: () => vi.fn(),
  };
});

async function renderHub(status: string) {
  server.use(...makeHandlers(status));
  render(
    <TestProviders initialEntries={[`/missions/${MISSION_ID}`]}>
      <MissionDetail />
    </TestProviders>,
  );
  await waitFor(() => {
    expect(screen.getByTestId('mission-hub')).toBeInTheDocument();
  });
}

describe('Mission Hub', () => {
  it('renders all 5 facet cards (Details, Flights, Images, Report, Invoice)', async () => {
    await renderHub('in_progress');
    // Wait for the title to load (proves the GET hydrated the state).
    await waitFor(() => {
      expect(screen.getByText('HUB-RENDER SMOKE')).toBeInTheDocument();
    });
    // Cards render their title in the dedicated title slot.
    expect(screen.getByText('DETAILS')).toBeInTheDocument();
    expect(screen.getByText('FLIGHTS')).toBeInTheDocument();
    expect(screen.getByText('IMAGES')).toBeInTheDocument();
    expect(screen.getByText('REPORT')).toBeInTheDocument();
    expect(screen.getByText('INVOICE')).toBeInTheDocument();

    // Edit buttons (one per facet card → 5 visible).
    const editButtons = screen.getAllByRole('button', { name: /^Edit/ });
    expect(editButtons.length).toBeGreaterThanOrEqual(5);
  });

  it('Mark SENT button is HIDDEN when status is not COMPLETED', async () => {
    await renderHub('in_progress');
    await waitFor(() => screen.getByText('HUB-RENDER SMOKE'));
    expect(screen.queryByTestId('mark-sent-btn')).toBeNull();
    // But Mark COMPLETED IS visible.
    expect(screen.getByTestId('mark-completed-btn')).toBeInTheDocument();
  });

  it('Mark SENT button IS visible when status is COMPLETED', async () => {
    await renderHub('completed');
    await waitFor(() => screen.getByText('HUB-RENDER SMOKE'));
    expect(screen.getByTestId('mark-sent-btn')).toBeInTheDocument();
    // Mark COMPLETED hidden once already completed.
    expect(screen.queryByTestId('mark-completed-btn')).toBeNull();
    expect(screen.queryByTestId('reopen-btn')).toBeNull();
  });

  it('all Edit buttons are DISABLED when status is SENT (lockdown per §8.5)', async () => {
    await renderHub('sent');
    await waitFor(() => screen.getByText('HUB-RENDER SMOKE'));
    const editButtons = screen.getAllByRole('button', { name: /^Edit/ });
    expect(editButtons.length).toBeGreaterThanOrEqual(5);
    for (const btn of editButtons) {
      expect(btn).toBeDisabled();
    }
  });

  it('Reopen Mission button is VISIBLE when status is SENT', async () => {
    await renderHub('sent');
    await waitFor(() => screen.getByText('HUB-RENDER SMOKE'));
    expect(screen.getByTestId('reopen-btn')).toBeInTheDocument();
    // Mark COMPLETED + Mark SENT must be hidden in the SENT state.
    expect(screen.queryByTestId('mark-completed-btn')).toBeNull();
    expect(screen.queryByTestId('mark-sent-btn')).toBeNull();
  });

  it('shows the lockdown banner when status is SENT', async () => {
    await renderHub('sent');
    await waitFor(() => screen.getByText('HUB-RENDER SMOKE'));
    expect(
      screen.getByText(/Mission marked SENT — record is locked/i),
    ).toBeInTheDocument();
  });

  it('Invoice card surfaces deposit-required badge + extraActions (Issue Link / Email)', async () => {
    await renderHub('in_progress');
    await waitFor(() => screen.getByText('HUB-RENDER SMOKE'));
    // Find the invoice card via its title, then the surrounding card root.
    const invoiceTitle = screen.getByText('INVOICE');
    const card = invoiceTitle.closest('.mantine-Card-root') as HTMLElement;
    expect(card).not.toBeNull();
    // Deposit due badge should render in the summary
    expect(within(card).getByText(/Deposit due/i)).toBeInTheDocument();
    // Extra actions: Issue Link + Email buttons sit on the card.
    expect(within(card).getByRole('button', { name: /ISSUE LINK/i })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /EMAIL/i })).toBeInTheDocument();
  });
});
