/**
 * MissionFlightsEdit contract test (v2.67.0 Mission Hub; bulk path v2.73.0/ADR-0025).
 *
 * Per ADR-0013 + spec §7 + ADR-0025: msw at the network boundary asserts:
 *
 *   1. Add (per-row AND multi-select) → POST /api/missions/{id}/flights/bulk
 *      with body {flights:[...]}. Items must NOT include aircraft_id — the
 *      server derives it from the flight log.
 *   2. Remove → DELETE /api/missions/{id}/flights/{flight_id}.
 *   3. CONTRACT: POST /api/missions is NEVER fired from this page.
 *   4. CONTRACT: PATCH /api/missions/{id}/flights/{flight_id}/aircraft
 *      is NEVER fired from this page either — that whole concept is gone.
 *   5. CONTRACT: the per-flight, one-POST-per-click /flights endpoint is no
 *      longer hit — adds go through the single-request bulk endpoint.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionFlightsEdit from '../MissionFlightsEdit';
import TestProviders from '../../test/TestProviders';

const MISSION_ID = 'abc-123';
const NATIVE_FLIGHT_ID = 'fac34a12-1111-4111-8111-111111111111';
const ATTACHED_FLIGHT_ROW_ID = 'mf-77777777-7777-4777-8777-777777777777';

let lastBulkUrl: string | null = null;
let lastBulkBody: Record<string, unknown> | null = null;
let lastDeleteFlightUrl: string | null = null;
let postMissionsCallCount = 0;
let patchAircraftCallCount = 0;
let singleFlightPostCallCount = 0;

function freshMission() {
  return {
    id: MISSION_ID,
    customer_id: null,
    title: 'M',
    mission_type: 'inspection',
    description: null,
    mission_date: '2026-05-01',
    location_name: null,
    area_coordinates: null,
    status: 'draft',
    is_billable: false,
    unas_folder_path: null,
    download_link_url: null,
    download_link_expires_at: null,
    client_notes: null,
    created_at: '2026-04-30T12:00:00Z',
    updated_at: '2026-04-30T12:00:00Z',
    flights: [
      {
        id: ATTACHED_FLIGHT_ROW_ID,
        opendronelog_flight_id: 'odl-99',
        aircraft_id: null,
        aircraft: null,
        flight_data_cache: {
          id: 'odl-99',
          display_name: 'Already Attached Flight',
          drone_model: 'Mavic 3 Pro',
          start_time: '2026-04-30T08:00:00Z',
          duration_secs: 600,
        },
        added_at: '2026-04-30T08:30:00Z',
      },
    ],
    images: [],
  };
}

function buildHandlers() {
  return [
    http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(freshMission())),
    http.get('*/api/aircraft', () =>
      HttpResponse.json([{ id: 'air-1', model_name: 'Mavic 3 Pro', manufacturer: 'DJI', specs: {}, created_at: 'x', serial_number: null, image_filename: null }]),
    ),
    http.get('*/api/flight-library', () =>
      HttpResponse.json([
        {
          id: NATIVE_FLIGHT_ID,
          display_name: 'New Available Flight',
          drone_model: 'Mavic 3 Pro',
          start_time: '2026-05-02T10:00:00Z',
          duration_secs: 800,
          source: 'native',
        },
      ]),
    ),
    http.post(`*/api/missions/${MISSION_ID}/flights/bulk`, async ({ request }) => {
      lastBulkUrl = request.url;
      lastBulkBody = (await request.json()) as Record<string, unknown>;
      const items = (lastBulkBody?.flights as Array<Record<string, unknown>>) ?? [];
      return HttpResponse.json(
        items.map((it, i) => ({
          id: `mf-newly-attached-${i}`,
          opendronelog_flight_id: (it.opendronelog_flight_id as string | null) ?? null,
          flight_id: (it.flight_id as string | null) ?? null,
          aircraft_id: null,
          aircraft: null,
          flight_data_cache: (it.flight_data_cache as Record<string, unknown>) ?? {},
          added_at: '2026-05-02T10:30:00Z',
        })),
        { status: 201 },
      );
    }),
    // TRIPWIRE: the one-POST-per-click endpoint must no longer be hit.
    http.post(`*/api/missions/${MISSION_ID}/flights`, () => {
      singleFlightPostCallCount++;
      return HttpResponse.json({ id: 'should-not-be-used' }, { status: 201 });
    }),
    http.delete(`*/api/missions/${MISSION_ID}/flights/:flightRowId`, ({ request }) => {
      lastDeleteFlightUrl = request.url;
      return new HttpResponse(null, { status: 204 });
    }),
    // CONTRACT TRIPWIRE: PATCH .../aircraft must never fire from this
    // page anymore — aircraft is derived server-side from the flight log.
    http.patch(`*/api/missions/${MISSION_ID}/flights/:flightRowId/aircraft`, () => {
      patchAircraftCallCount++;
      return HttpResponse.json({ ok: true });
    }),
    // CONTRACT TRIPWIRE.
    http.post('*/api/missions', () => {
      postMissionsCallCount++;
      return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
    }),
  ];
}

const server = setupServer(...buildHandlers());

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers(...buildHandlers());
  lastBulkUrl = null;
  lastBulkBody = null;
  lastDeleteFlightUrl = null;
  postMissionsCallCount = 0;
  patchAircraftCallCount = 0;
  singleFlightPostCallCount = 0;
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

describe('MissionFlightsEdit', () => {
  it('Per-row Add fires POST /flights/bulk (one item) and NEVER POST /api/missions', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionFlightsEdit />
      </TestProviders>,
    );

    // Wait for the available flights table to render.
    await screen.findByText(/AVAILABLE FLIGHTS/i);
    const addBtn = await screen.findByRole('button', { name: /Add New Available Flight/i });
    await user.click(addBtn);

    await waitFor(() => {
      expect(lastBulkBody).not.toBeNull();
    });

    expect(lastBulkUrl).toMatch(new RegExp(`/api/missions/${MISSION_ID}/flights/bulk$`));
    const items = lastBulkBody?.flights as Array<Record<string, unknown>>;
    expect(items).toHaveLength(1);
    // The native-flight branch sets flight_id; opendronelog_flight_id is null.
    expect(items[0].flight_id).toBe(NATIVE_FLIGHT_ID);
    expect(items[0].opendronelog_flight_id).toBeNull();
    // aircraft_id must NOT be in the item — the server derives it.
    expect(items[0]).not.toHaveProperty('aircraft_id');

    // Load-bearing invariants.
    expect(postMissionsCallCount).toBe(0);
    expect(patchAircraftCallCount).toBe(0);
    expect(singleFlightPostCallCount).toBe(0);
  });

  it('Multi-select "Add selected" attaches the checked flights in ONE bulk request', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionFlightsEdit />
      </TestProviders>,
    );

    await screen.findByText(/AVAILABLE FLIGHTS/i);
    // Tick the row checkbox for the available flight.
    const rowCheckbox = await screen.findByRole('checkbox', {
      name: /Select New Available Flight/i,
    });
    await user.click(rowCheckbox);

    // The "ADD SELECTED (1)" button reflects the count and triggers the bulk add.
    const addSelected = await screen.findByRole('button', { name: /ADD SELECTED \(1\)/i });
    await user.click(addSelected);

    await waitFor(() => {
      expect(lastBulkBody).not.toBeNull();
    });

    expect(lastBulkUrl).toMatch(new RegExp(`/api/missions/${MISSION_ID}/flights/bulk$`));
    const items = lastBulkBody?.flights as Array<Record<string, unknown>>;
    expect(items).toHaveLength(1);
    expect(items[0].flight_id).toBe(NATIVE_FLIGHT_ID);
    expect(postMissionsCallCount).toBe(0);
    expect(singleFlightPostCallCount).toBe(0);
  });

  it('Remove fires DELETE /api/missions/{id}/flights/{flight_id} and NEVER POST /api/missions', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionFlightsEdit />
      </TestProviders>,
    );

    // Wait for the attached row to render and click its trash button.
    await screen.findByText(/Already Attached Flight/i);
    const removeBtn = await screen.findByRole('button', {
      name: /Remove Already Attached Flight/i,
    });
    await user.click(removeBtn);

    await waitFor(() => {
      expect(lastDeleteFlightUrl).not.toBeNull();
    });

    expect(lastDeleteFlightUrl).toMatch(
      new RegExp(`/api/missions/${MISSION_ID}/flights/${ATTACHED_FLIGHT_ROW_ID}$`),
    );
    expect(postMissionsCallCount).toBe(0);
  });

  it('Done navigates to /missions/{id} (the Hub) without any /missions write', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();
    render(
      <TestProviders>
        <MissionFlightsEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT FLIGHTS/i);
    const doneBtn = await screen.findAllByRole('button', { name: /^DONE$/i });
    await user.click(doneBtn[0]);

    expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    expect(postMissionsCallCount).toBe(0);
    expect(lastBulkBody).toBeNull();
    expect(lastDeleteFlightUrl).toBeNull();
  });
});
