/**
 * MissionInvoiceEdit unsaved-changes guard test (v2.67.3 polish).
 *
 * The Invoice editor is the most field-dense facet (line items array
 * + 5 scalar fields), so it's the canonical proof that the snapshot-
 * based dirty calc reads correctly across complex state. msw intercepts
 * the GET/PUT/POST/DELETE invoice endpoints; navigation is asserted via
 * a spied useNavigate.
 *
 * Scenarios per spec:
 *   - Edit + Cancel → confirm modal renders, no navigate.
 *   - Keep Editing → modal closes, no navigate.
 *   - Discard → navigate('/missions/{id}'), no /missions write.
 *   - Edit + Save (PUT 200) + Cancel → no modal (dirty cleared).
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionInvoiceEdit from '../MissionInvoiceEdit';
import TestProviders from '../../test/TestProviders';

const MISSION_ID = 'inv-mission-1';
const INVOICE_ID = 'inv-1';

const baseMission = {
  id: MISSION_ID,
  customer_id: 'cust-1',
  title: 'Inspection job',
  mission_type: 'inspection',
  description: null,
  mission_date: '2026-05-01',
  location_name: null,
  area_coordinates: null,
  status: 'draft',
  is_billable: true,
  client_notes: null,
  unas_folder_path: null,
  download_link_url: null,
  download_link_expires_at: null,
  created_at: '2026-04-30T12:00:00Z',
  updated_at: '2026-04-30T12:00:00Z',
  flights: [],
  images: [],
};

const baseInvoice = {
  id: INVOICE_ID,
  mission_id: MISSION_ID,
  tax_rate: 0,
  notes: '',
  paid_in_full: false,
  deposit_required: false,
  deposit_amount: 0,
  deposit_paid: false,
  line_items: [
    {
      id: 'li-1',
      description: 'Site survey',
      category: 'billed_time',
      quantity: 2,
      unit_price: 150,
      sort_order: 0,
    },
  ],
};

let postMissionsCallCount = 0;
let putInvoiceCount = 0;

const server = setupServer(
  http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(baseMission)),
  http.get(`*/api/missions/${MISSION_ID}/invoice`, () => HttpResponse.json(baseInvoice)),
  http.get('*/api/rate-templates', () => HttpResponse.json([])),
  http.put(`*/api/missions/${MISSION_ID}/invoice`, async () => {
    putInvoiceCount++;
    return HttpResponse.json(baseInvoice);
  }),
  http.post(`*/api/missions/${MISSION_ID}/invoice/items`, () =>
    HttpResponse.json({ id: 'li-new', sort_order: 0 }),
  ),
  http.delete(`*/api/missions/${MISSION_ID}/invoice/items/:itemId`, () =>
    new HttpResponse(null, { status: 204 }),
  ),
  // CONTRACT TRIPWIRE: Invoice editor MUST NEVER POST /api/missions.
  http.post('*/api/missions', () => {
    postMissionsCallCount++;
    return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers(
    http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(baseMission)),
    http.get(`*/api/missions/${MISSION_ID}/invoice`, () => HttpResponse.json(baseInvoice)),
    http.get('*/api/rate-templates', () => HttpResponse.json([])),
    http.put(`*/api/missions/${MISSION_ID}/invoice`, async () => {
      putInvoiceCount++;
      return HttpResponse.json(baseInvoice);
    }),
    http.post(`*/api/missions/${MISSION_ID}/invoice/items`, () =>
      HttpResponse.json({ id: 'li-new', sort_order: 0 }),
    ),
    http.delete(`*/api/missions/${MISSION_ID}/invoice/items/:itemId`, () =>
      new HttpResponse(null, { status: 204 }),
    ),
    http.post('*/api/missions', () => {
      postMissionsCallCount++;
      return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
    }),
  );
  postMissionsCallCount = 0;
  putInvoiceCount = 0;
});
afterAll(() => server.close());

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const mod: Record<string, unknown> = await vi.importActual('react-router-dom');
  return {
    ...mod,
    useNavigate: () => navigateSpy,
    useParams: () => ({ id: MISSION_ID }),
  };
});

describe('MissionInvoiceEdit unsaved-changes guard', () => {
  it('Cancel without edits navigates immediately (no modal)', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();

    render(
      <TestProviders>
        <MissionInvoiceEdit />
      </TestProviders>,
    );

    // Wait for the invoice to load (mission title appears in the header).
    await screen.findByText(/EDIT INVOICE/i);

    // Click the bottom Cancel — no edits made.
    const cancelBtns = screen.getAllByRole('button', { name: /^Cancel$/ });
    await user.click(cancelBtns[0]);

    expect(screen.queryByText(/Discard unsaved changes\?/i)).not.toBeInTheDocument();
    expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    expect(postMissionsCallCount).toBe(0);
  });

  it('Cancel after editing notes shows the confirm modal and does NOT navigate', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();

    render(
      <TestProviders>
        <MissionInvoiceEdit />
      </TestProviders>,
    );

    const notes = (await screen.findByLabelText(/^Notes$/i)) as HTMLTextAreaElement;
    await user.type(notes, 'Some unsaved note');

    const cancelBtns = screen.getAllByRole('button', { name: /^Cancel$/ });
    await user.click(cancelBtns[0]);

    expect(await screen.findByText(/Discard unsaved changes\?/i)).toBeInTheDocument();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('Keep Editing closes the modal without navigating', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();

    render(
      <TestProviders>
        <MissionInvoiceEdit />
      </TestProviders>,
    );

    const notes = (await screen.findByLabelText(/^Notes$/i)) as HTMLTextAreaElement;
    await user.type(notes, 'unsaved');
    await user.click(screen.getAllByRole('button', { name: /^Cancel$/ })[0]);

    const keepBtn = await screen.findByRole('button', { name: /KEEP EDITING/i });
    await user.click(keepBtn);

    await waitFor(() => {
      expect(screen.queryByText(/Discard unsaved changes\?/i)).not.toBeInTheDocument();
    });
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('Discard Changes navigates to /missions/{id} without writing', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();

    render(
      <TestProviders>
        <MissionInvoiceEdit />
      </TestProviders>,
    );

    const notes = (await screen.findByLabelText(/^Notes$/i)) as HTMLTextAreaElement;
    await user.type(notes, 'unsaved');
    await user.click(screen.getAllByRole('button', { name: /^Cancel$/ })[0]);

    const discardBtn = await screen.findByRole('button', { name: /DISCARD CHANGES/i });
    await user.click(discardBtn);

    await waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    });
    expect(putInvoiceCount).toBe(0);
    expect(postMissionsCallCount).toBe(0);
  });

  it('Cancel after a successful Save does NOT prompt (dirty cleared)', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();

    render(
      <TestProviders>
        <MissionInvoiceEdit />
      </TestProviders>,
    );

    const notes = (await screen.findByLabelText(/^Notes$/i)) as HTMLTextAreaElement;
    await user.type(notes, 'about to save');

    // Save lives both as "SAVE INVOICE" buttons (top + bottom).
    const saveBtns = screen.getAllByRole('button', { name: /SAVE INVOICE/i });
    await user.click(saveBtns[0]);

    await waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    });
    expect(putInvoiceCount).toBeGreaterThan(0);
    expect(postMissionsCallCount).toBe(0);

    // After save, the dirty baseline is reset. Click Cancel — must NOT
    // prompt; navigate fires immediately.
    navigateSpy.mockClear();
    const cancelBtns = screen.getAllByRole('button', { name: /^Cancel$/ });
    await user.click(cancelBtns[0]);

    expect(screen.queryByText(/Discard unsaved changes\?/i)).not.toBeInTheDocument();
    expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
  });
});
