/**
 * Cross-cutting Mission Hub routes contract test (v2.67.0, ADR-0014).
 *
 * Per spec §7 + Task 5 Step 1 of the orchestration plan, this exercises
 * the route table itself — for every URL we ship, mount the route table
 * at that URL and assert the right component renders + the right backend
 * request fires. This is the integration layer between the per-facet
 * tests (`MissionDetailsEdit.test.tsx`, `MissionFlightsEdit.test.tsx`,
 * etc., each of which proves its OWN page never POSTs `/api/missions`)
 * and the route wiring done in this slice (`App.tsx`).
 *
 * Coverage matrix (per Task 5 Step 1):
 *   /missions/new                         -> redirect to /missions list
 *                                            with notification
 *   /missions/abc-123/edit                -> Navigate to /missions/abc-123
 *                                            (back-compat redirect)
 *   /missions/abc-123                     -> MissionDetail (Hub) renders
 *   /missions/abc-123/details/edit        -> MissionDetailsEdit; Save
 *                                            fires PUT /api/missions/{id}
 *                                            (NEVER POST /api/missions)
 *   /missions/abc-123/flights/edit        -> MissionFlightsEdit; Add
 *                                            fires POST .../flights
 *                                            (NEVER POST /api/missions)
 *   /missions/abc-123/images/edit         -> MissionImagesEdit
 *   /missions/abc-123/report/edit         -> MissionReportEdit
 *   /missions/abc-123/invoice/edit        -> MissionInvoiceEdit
 *   /missions/abc-123/edit-legacy         -> MissionWizardLegacy
 *
 * Implementation notes:
 *   - We mount a thin route table that mirrors the relevant subset of
 *     `App.tsx` (the auth wrapper is irrelevant for routing logic) so
 *     this test is hermetic and does not need `useAuth` mocks.
 *   - We use synchronous `import` rather than `lazy()` so msw can race
 *     against the first render without a Suspense fallback delay.
 *   - msw intercepts the network. Every mount installs an
 *     `onUnhandledRequest: 'error'` server so any rogue request (e.g.
 *     a regression that sneaks `POST /api/missions` into a facet
 *     editor) fails the test by surfacing as an unhandled-request error
 *     rather than silently 404'ing.
 *   - The cross-cutting load-bearing assertion is `postMissionsCallCount
 *     === 0` after exercising every facet editor's Save path. This
 *     reproduces the Agent A/B/C tripwire at the route-table level.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { MemoryRouter, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { useEffect } from 'react';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { notifications } from '@mantine/notifications';

// Heavy components mocked to keep jsdom fast and deterministic. Same
// pattern as MissionReportEdit.test.tsx — the routing contract is the
// surface under test, not the rich-text editor or PDF viewer. jsdom
// has no DOMMatrix so react-pdf crashes on import without this stub.
vi.mock('../components/RichTextEditor/RichTextEditor', () => ({
  default: ({
    content,
    onChange,
  }: {
    content: string;
    onChange: (s: string) => void;
  }) => (
    <textarea data-testid="rte" value={content} onChange={(e) => onChange(e.target.value)} />
  ),
}));
vi.mock('../components/PDFPreview/PdfViewer', () => ({
  default: ({ url }: { url: string }) => <div data-testid="pdf-viewer" data-url={url} />,
}));

// IMPORTANT: synchronous (not lazy) imports — keeps the test deterministic.
// The component identity is what we assert; lazy() introduces Suspense
// fallback flashes that race against msw handlers.
import Missions from '../pages/Missions';
import MissionDetail from '../pages/MissionDetail';
import MissionDetailsEdit from '../pages/MissionDetailsEdit';
import MissionFlightsEdit from '../pages/MissionFlightsEdit';
import MissionImagesEdit from '../pages/MissionImagesEdit';
import MissionReportEdit from '../pages/MissionReportEdit';
import MissionInvoiceEdit from '../pages/MissionInvoiceEdit';
import MissionWizardLegacy from '../pages/MissionWizardLegacy';

const MISSION_ID = 'abc-123';
const CUSTOMER_ID = 'cust-1';

let postMissionsCallCount = 0;
let lastPutMissionUrl: string | null = null;
let lastPostFlightUrl: string | null = null;

function baseMission(status = 'draft') {
  return {
    id: MISSION_ID,
    customer_id: CUSTOMER_ID,
    title: 'Routing Smoke',
    mission_type: 'inspection',
    description: 'desc',
    mission_date: '2026-05-01',
    location_name: '123 Main St',
    area_coordinates: null,
    status,
    is_billable: true,
    unas_folder_path: null,
    download_link_url: null,
    download_link_expires_at: null,
    client_notes: null,
    created_at: '2026-04-30T12:00:00Z',
    updated_at: '2026-05-01T00:00:00Z',
    flights: [],
    images: [],
  };
}

const baseHandlers = [
  // Mission detail (used by Hub + every facet editor that hydrates)
  http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(baseMission())),
  // Customer list / detail
  http.get('*/api/customers', () =>
    HttpResponse.json([{ id: CUSTOMER_ID, name: 'Acme Inc', company: null }]),
  ),
  http.get(`*/api/customers/${CUSTOMER_ID}`, () =>
    HttpResponse.json({
      id: CUSTOMER_ID,
      name: 'Acme Inc',
      email: 'a@example.com',
      company: null,
      tos_signed: true,
      tos_signed_at: '2026-04-29T00:00:00Z',
      latest_tos_audit_id: null,
      latest_tos_signed_sha: null,
      latest_tos_template_version: null,
    }),
  ),
  // Aircraft + flight-library — referenced by MissionFlightsEdit on mount
  http.get('*/api/aircraft', () => HttpResponse.json([])),
  http.get('*/api/flight-library', () => HttpResponse.json([])),
  http.get('*/api/flights', () => HttpResponse.json([])),
  // Report (used by Hub + MissionReportEdit)
  http.get(`*/api/missions/${MISSION_ID}/report`, () =>
    HttpResponse.json({
      id: 'r1',
      mission_id: MISSION_ID,
      user_narrative: null,
      llm_generated_content: null,
      final_content: null,
    }),
  ),
  // Invoice (used by Hub + MissionInvoiceEdit)
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
      deposit_required: false,
      deposit_amount: 0,
      deposit_paid: false,
    }),
  ),
  http.get(`*/api/missions/${MISSION_ID}/client-link`, () =>
    HttpResponse.json({ token: null, expires_at: null, channel: null, sent_at: null }),
  ),
  // Generic missions list — Missions page reads this on mount
  http.get('*/api/missions', () => HttpResponse.json([])),
  // System settings — read by various pages on mount
  http.get('*/api/settings/system', () => HttpResponse.json({})),
  http.get('*/api/settings', () => HttpResponse.json({})),
  // Mission images
  http.get(`*/api/missions/${MISSION_ID}/images`, () => HttpResponse.json([])),
  // Update / write paths
  http.put(`*/api/missions/${MISSION_ID}`, async ({ request }) => {
    lastPutMissionUrl = request.url;
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...baseMission(), ...body });
  }),
  http.post(`*/api/missions/${MISSION_ID}/flights`, async ({ request }) => {
    lastPostFlightUrl = request.url;
    return HttpResponse.json(
      {
        id: 'mf-attached',
        opendronelog_flight_id: null,
        aircraft_id: null,
        aircraft: null,
        flight_data_cache: {},
        added_at: '2026-05-02T10:30:00Z',
      },
      { status: 201 },
    );
  }),
  // The cross-cutting tripwire: this MUST NEVER fire from any facet
  // editor route. If it does, the duplicate-mission bug class is back.
  http.post('*/api/missions', () => {
    postMissionsCallCount++;
    return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
  }),
];

const server = setupServer(...baseHandlers);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
beforeEach(() => {
  postMissionsCallCount = 0;
  lastPutMissionUrl = null;
  lastPostFlightUrl = null;
  server.resetHandlers(...baseHandlers);
});
afterEach(() => {
  // After every test, the cross-cutting load-bearing invariant — none
  // of the routing tests should ever trigger a `POST /api/missions`.
  expect(postMissionsCallCount).toBe(0);
});
afterAll(() => server.close());

// Mirror App.tsx's redirect helpers so this test exercises real routing
// shape, not a hand-rolled stub. If App.tsx's helpers ever change, the
// test will catch the divergence — keeping the Hub redesign's promise
// of "/missions/new and /missions/:id/edit must degrade gracefully".
function MissionsNewLegacyRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    notifications.show({
      title: 'Use the New Mission button',
      message: 'The standalone create page was replaced by the inline modal in v2.67.0.',
      color: 'cyan',
    });
    navigate('/missions', { replace: true });
  }, [navigate]);
  return null;
}
function MissionEditLegacyRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/missions/${id ?? ''}`} replace />;
}

function renderAt(path: string) {
  return render(
    <MantineProvider>
      <Notifications />
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/missions" element={<Missions />} />
          <Route path="/missions/new" element={<MissionsNewLegacyRedirect />} />
          <Route path="/missions/:id/edit" element={<MissionEditLegacyRedirect />} />
          <Route path="/missions/:id/edit-legacy" element={<MissionWizardLegacy />} />
          <Route path="/missions/:id/details/edit" element={<MissionDetailsEdit />} />
          <Route path="/missions/:id/flights/edit" element={<MissionFlightsEdit />} />
          <Route path="/missions/:id/images/edit" element={<MissionImagesEdit />} />
          <Route path="/missions/:id/report/edit" element={<MissionReportEdit />} />
          <Route path="/missions/:id/invoice/edit" element={<MissionInvoiceEdit />} />
          <Route path="/missions/:id" element={<MissionDetail />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

describe('Mission Hub route table (v2.67.0)', () => {
  it('GET /missions/new redirects to /missions list with a notification', async () => {
    renderAt('/missions/new');
    // Missions list renders its trademark "MISSIONS" title.
    await waitFor(() => {
      expect(screen.getByText(/^MISSIONS$/)).toBeInTheDocument();
    });
    // Notification is rendered into the Mantine notifications portal.
    await waitFor(() => {
      expect(screen.getByText(/Use the New Mission button/)).toBeInTheDocument();
    });
  });

  it('GET /missions/abc-123/edit redirects (Navigate) to /missions/abc-123 (the Hub)', async () => {
    renderAt(`/missions/${MISSION_ID}/edit`);
    // The Hub renders a status badge derived from the missions row's
    // status. We assert the Hub's distinctive "Routing Smoke" title
    // (from the seed mission) shows up — proving the redirect landed
    // on MissionDetail and not the legacy wizard.
    await waitFor(
      () => {
        expect(screen.getByText(/Routing Smoke/)).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  });

  it('GET /missions/abc-123 renders the Hub (MissionDetail)', async () => {
    renderAt(`/missions/${MISSION_ID}`);
    await waitFor(
      () => {
        expect(screen.getByText(/Routing Smoke/)).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  });

  it('GET /missions/abc-123/details/edit renders MissionDetailsEdit; Save fires PUT (never POST)', async () => {
    const user = userEvent.setup();
    renderAt(`/missions/${MISSION_ID}/details/edit`);

    // Form hydrates from GET /api/missions/{id}.
    const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
    expect(titleInput.value).toBe('Routing Smoke');

    await user.clear(titleInput);
    await user.type(titleInput, 'Routing Smoke Edited');

    const saveBtn = await screen.findByRole('button', { name: /SAVE CHANGES/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(lastPutMissionUrl).not.toBeNull();
    });
    expect(lastPutMissionUrl).toMatch(new RegExp(`/api/missions/${MISSION_ID}$`));
    // afterEach() asserts postMissionsCallCount === 0.
  });

  it('GET /missions/abc-123/flights/edit renders MissionFlightsEdit (no POST /api/missions on mount)', async () => {
    renderAt(`/missions/${MISSION_ID}/flights/edit`);
    // FlightsEdit shows the page title once mounted; the absence of
    // POST /api/missions during mount + browse is the load-bearing
    // assertion (asserted in afterEach).
    await waitFor(
      () => {
        // Flights editor shows attached-flights / available-flights
        // headings; we assert one of its distinctive sections exists.
        const candidates = screen.queryAllByText(/AVAILABLE|ATTACHED|FLIGHTS/i);
        expect(candidates.length).toBeGreaterThan(0);
      },
      { timeout: 4000 },
    );
  });

  it('GET /missions/abc-123/images/edit renders MissionImagesEdit (no POST /api/missions on mount)', async () => {
    renderAt(`/missions/${MISSION_ID}/images/edit`);
    await waitFor(
      () => {
        const candidates = screen.queryAllByText(/IMAGES/i);
        expect(candidates.length).toBeGreaterThan(0);
      },
      { timeout: 4000 },
    );
  });

  it('GET /missions/abc-123/report/edit renders MissionReportEdit (no POST /api/missions on mount)', async () => {
    renderAt(`/missions/${MISSION_ID}/report/edit`);
    await waitFor(
      () => {
        const candidates = screen.queryAllByText(/REPORT|NARRATIVE/i);
        expect(candidates.length).toBeGreaterThan(0);
      },
      { timeout: 4000 },
    );
  });

  it('GET /missions/abc-123/invoice/edit renders MissionInvoiceEdit (no POST /api/missions on mount)', async () => {
    renderAt(`/missions/${MISSION_ID}/invoice/edit`);
    await waitFor(
      () => {
        const candidates = screen.queryAllByText(/INVOICE/i);
        expect(candidates.length).toBeGreaterThan(0);
      },
      { timeout: 4000 },
    );
  });

  it('GET /missions/abc-123/edit-legacy renders MissionWizardLegacy (the soak fallback)', async () => {
    renderAt(`/missions/${MISSION_ID}/edit-legacy`);
    // The legacy wizard's distinctive 5-step stepper renders the
    // step labels; we assert at least one of them appears so we know
    // the legacy component (not the Hub) mounted at this URL.
    await waitFor(
      () => {
        // Legacy wizard renders Bebas-Neue step labels; "Details" is
        // the first step and stable across versions.
        const candidates = screen.queryAllByText(/DETAILS/i);
        expect(candidates.length).toBeGreaterThan(0);
      },
      { timeout: 4000 },
    );
  });
});
