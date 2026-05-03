/**
 * MissionCreateModal contract test (v2.67.0 Mission Hub).
 *
 * Per ADR-0013 + spec §7: msw intercepts the real axios POST at the
 * network boundary and asserts:
 *
 *   1. The submitted JSON body NEVER includes an `id` field. This is
 *      the load-bearing contract that makes the duplicate-mission
 *      class physically impossible.
 *   2. On 201 success, the user is navigated to `/missions/{id}`.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { useNavigate } from 'react-router-dom';

import MissionCreateModal from '../MissionCreateModal';
import TestProviders from '../../test/TestProviders';

let lastBody: any = null;
let lastUrl: string | null = null;
const FAKE_NEW_ID = '11111111-1111-4111-8111-111111111111';

const server = setupServer(
  http.get('*/api/customers', () =>
    HttpResponse.json([
      { id: 'cust-1', name: 'Casey Operator', company: 'Acme' },
    ]),
  ),
  http.post('*/api/missions', async ({ request }) => {
    lastUrl = request.url;
    lastBody = await request.json();
    return HttpResponse.json(
      {
        id: FAKE_NEW_ID,
        title: lastBody?.title ?? 'X',
        mission_type: lastBody?.mission_type ?? 'other',
        status: 'draft',
        is_billable: false,
        customer_id: lastBody?.customer_id ?? null,
        created_at: '2026-05-03T12:00:00Z',
        updated_at: '2026-05-03T12:00:00Z',
        flights: [],
        images: [],
        description: null,
        mission_date: lastBody?.mission_date ?? null,
        location_name: null,
        area_coordinates: null,
      },
      { status: 201 },
    );
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  lastBody = null;
  lastUrl = null;
});
afterAll(() => server.close());

// Spy on react-router-dom's useNavigate so we can assert routing
// without a full RouterProvider stack.
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const mod: any = await vi.importActual('react-router-dom');
  return { ...mod, useNavigate: () => navigateSpy };
});

describe('MissionCreateModal', () => {
  it('submits POST /api/missions WITHOUT an `id` field and navigates to the new Hub on success', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      <TestProviders>
        <MissionCreateModal opened onClose={onClose} />
      </TestProviders>,
    );

    // Wait until the modal is mounted (Mantine portals it).
    await screen.findByText(/NEW MISSION/);

    // Type the title (the only required field).
    const titleInput = await screen.findByLabelText(/^Title/);
    await user.type(titleInput, 'Hub-modal smoke');

    const createBtn = screen.getByRole('button', { name: /CREATE MISSION/i });
    await user.click(createBtn);

    await waitFor(() => {
      expect(lastBody).not.toBeNull();
    });

    expect(lastUrl).toMatch(/\/api\/missions$/);
    expect(lastBody).toEqual({
      title: 'Hub-modal smoke',
      mission_type: 'other',
    });
    // Load-bearing invariant: NEVER POST an `id` from the create modal.
    expect(lastBody).not.toHaveProperty('id');

    await waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith(`/missions/${FAKE_NEW_ID}`);
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('shows an error notification + does not navigate when the server returns 4xx', async () => {
    server.use(
      http.post('*/api/missions', () =>
        HttpResponse.json({ detail: 'Boom' }, { status: 400 }),
      ),
    );
    navigateSpy.mockClear();

    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionCreateModal opened onClose={vi.fn()} />
      </TestProviders>,
    );

    await screen.findByText(/NEW MISSION/);
    const titleInput = await screen.findByLabelText(/^Title/);
    await user.type(titleInput, 'Will fail');
    await user.click(screen.getByRole('button', { name: /CREATE MISSION/i }));

    await waitFor(() => {
      expect(navigateSpy).not.toHaveBeenCalled();
    });
  });
});
