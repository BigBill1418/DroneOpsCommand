/**
 * MissionFlightsEdit contract test (v2.67.0 Mission Hub).
 *
 * Per ADR-0013 + spec §7: msw at the network boundary asserts:
 *
 *   1. Add → POST /api/missions/{id}/flights with the right body shape.
 *   2. Remove → DELETE /api/missions/{id}/flights/{flight_id}.
 *   3. CONTRACT: POST /api/missions is NEVER fired from this page.
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

let lastPostFlightUrl: string | null = null;
let lastPostFlightBody: Record<string, unknown> | null = null;
let lastDeleteFlightUrl: string | null = null;
let postMissionsCallCount = 0;

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
    http.post(`*/api/missions/${MISSION_ID}/flights`, async ({ request }) => {
      lastPostFlightUrl = request.url;
      lastPostFlightBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(
        {
          id: 'mf-newly-attached',
          opendronelog_flight_id: null,
          aircraft_id: lastPostFlightBody?.aircraft_id ?? null,
          aircraft: null,
          flight_data_cache: lastPostFlightBody?.flight_data_cache ?? {},
          added_at: '2026-05-02T10:30:00Z',
        },
        { status: 201 },
      );
    }),
    http.delete(`*/api/missions/${MISSION_ID}/flights/:flightRowId`, ({ request }) => {
      lastDeleteFlightUrl = request.url;
      return new HttpResponse(null, { status: 204 });
    }),
    http.patch(`*/api/missions/${MISSION_ID}/flights/:flightRowId/aircraft`, () =>
      HttpResponse.json({ ok: true }),
    ),
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
  lastPostFlightUrl = null;
  lastPostFlightBody = null;
  lastDeleteFlightUrl = null;
  postMissionsCallCount = 0;
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
  it('Add fires POST /api/missions/{id}/flights and NEVER POST /api/missions', async () => {
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
      expect(lastPostFlightBody).not.toBeNull();
    });

    expect(lastPostFlightUrl).toMatch(new RegExp(`/api/missions/${MISSION_ID}/flights$`));
    // The native-flight branch sets flight_id; opendronelog_flight_id is null.
    expect(lastPostFlightBody?.flight_id).toBe(NATIVE_FLIGHT_ID);
    expect(lastPostFlightBody?.opendronelog_flight_id).toBeNull();

    // Load-bearing invariant.
    expect(postMissionsCallCount).toBe(0);
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
    expect(lastPostFlightBody).toBeNull();
    expect(lastDeleteFlightUrl).toBeNull();
  });
});
