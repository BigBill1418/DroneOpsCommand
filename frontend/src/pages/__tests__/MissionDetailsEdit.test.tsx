/**
 * MissionDetailsEdit contract test (v2.67.0 Mission Hub).
 *
 * Per ADR-0013 + spec §7: msw intercepts the real axios calls at the
 * network boundary and asserts:
 *
 *   1. GET /api/missions/{id} populates the form on mount.
 *   2. Save → PUT /api/missions/{id} with the edited payload.
 *   3. CONTRACT: POST /api/missions is NEVER fired from this page.
 *      The msw POST handler counts calls and the assertion fails the
 *      test if it runs even once.
 *   4. Cancel → navigate('/missions/abc-123').
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionDetailsEdit from '../MissionDetailsEdit';
import TestProviders from '../../test/TestProviders';

const MISSION_ID = 'abc-123';

let lastPutBody: Record<string, unknown> | null = null;
let lastPutUrl: string | null = null;
let postMissionsCallCount = 0;

const baseMission = {
  id: MISSION_ID,
  customer_id: 'cust-1',
  title: 'Original Title',
  mission_type: 'inspection',
  description: 'Initial notes',
  mission_date: '2026-05-01',
  location_name: '123 Main St',
  area_coordinates: null,
  status: 'draft',
  is_billable: true,
  unas_folder_path: '/missions/original/',
  download_link_url: '',
  download_link_expires_at: null,
  client_notes: null,
  created_at: '2026-04-30T12:00:00Z',
  updated_at: '2026-04-30T12:00:00Z',
  flights: [],
  images: [],
};

const server = setupServer(
  http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(baseMission)),
  http.get('*/api/customers', () =>
    HttpResponse.json([
      { id: 'cust-1', name: 'Acme Inc', company: null },
      { id: 'cust-2', name: 'Beta LLC', company: 'Beta' },
    ]),
  ),
  http.put(`*/api/missions/${MISSION_ID}`, async ({ request }) => {
    lastPutUrl = request.url;
    lastPutBody = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...baseMission, ...lastPutBody });
  }),
  // CONTRACT TRIPWIRE: this MUST NEVER fire from MissionDetailsEdit.
  // If it does, the duplicate-mission bug is back. Test fails.
  http.post('*/api/missions', () => {
    postMissionsCallCount++;
    return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers(
    http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(baseMission)),
    http.get('*/api/customers', () =>
      HttpResponse.json([
        { id: 'cust-1', name: 'Acme Inc', company: null },
        { id: 'cust-2', name: 'Beta LLC', company: 'Beta' },
      ]),
    ),
    http.put(`*/api/missions/${MISSION_ID}`, async ({ request }) => {
      lastPutUrl = request.url;
      lastPutBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...baseMission, ...lastPutBody });
    }),
    http.post('*/api/missions', () => {
      postMissionsCallCount++;
      return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
    }),
  );
  lastPutBody = null;
  lastPutUrl = null;
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

describe('MissionDetailsEdit', () => {
  it('populates the form from GET /api/missions/{id} on mount', async () => {
    render(
      <TestProviders>
        <MissionDetailsEdit />
      </TestProviders>,
    );

    const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
    expect(titleInput.value).toBe('Original Title');

    const locationInput = (await screen.findByLabelText(/^Location/i)) as HTMLInputElement;
    expect(locationInput.value).toBe('123 Main St');
  });

  it('Save fires PUT /api/missions/{id} with edited fields and NEVER POST /api/missions', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionDetailsEdit />
      </TestProviders>,
    );

    const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
    // Edit the title in place.
    await user.clear(titleInput);
    await user.type(titleInput, 'Edited Title');

    const saveBtn = await screen.findByRole('button', { name: /SAVE CHANGES/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(lastPutBody).not.toBeNull();
    });

    expect(lastPutUrl).toMatch(new RegExp(`/api/missions/${MISSION_ID}$`));
    expect(lastPutBody?.title).toBe('Edited Title');
    // Load-bearing invariant: this page MUST NOT POST /api/missions.
    expect(postMissionsCallCount).toBe(0);

    await waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    });
  });

  it('Cancel returns to /missions/{id} (the Hub) without firing any write', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();
    render(
      <TestProviders>
        <MissionDetailsEdit />
      </TestProviders>,
    );

    await screen.findByLabelText(/Mission Title/i);
    const cancelBtn = screen.getByRole('button', { name: /CANCEL/i });
    await user.click(cancelBtn);

    expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    expect(lastPutBody).toBeNull();
    expect(postMissionsCallCount).toBe(0);
  });

  // ─── Unsaved-changes guard (v2.67.3 polish) ────────────────────────
  describe('unsaved-changes guard', () => {
    it('Cancel after editing shows the confirm modal and does NOT navigate', async () => {
      const user = userEvent.setup();
      navigateSpy.mockClear();
      render(
        <TestProviders>
          <MissionDetailsEdit />
        </TestProviders>,
      );

      const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
      await user.type(titleInput, ' edited');

      const cancelBtn = screen.getByRole('button', { name: /CANCEL/i });
      await user.click(cancelBtn);

      // Modal renders; navigation suppressed.
      expect(await screen.findByText(/Discard unsaved changes\?/i)).toBeInTheDocument();
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('Keep Editing closes the modal without navigating', async () => {
      const user = userEvent.setup();
      navigateSpy.mockClear();
      render(
        <TestProviders>
          <MissionDetailsEdit />
        </TestProviders>,
      );

      const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
      await user.type(titleInput, ' edited');
      await user.click(screen.getByRole('button', { name: /CANCEL/i }));

      const keepBtn = await screen.findByRole('button', { name: /KEEP EDITING/i });
      await user.click(keepBtn);

      await waitFor(() => {
        expect(screen.queryByText(/Discard unsaved changes\?/i)).not.toBeInTheDocument();
      });
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('Discard Changes navigates to /missions/{id}', async () => {
      const user = userEvent.setup();
      navigateSpy.mockClear();
      render(
        <TestProviders>
          <MissionDetailsEdit />
        </TestProviders>,
      );

      const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
      await user.type(titleInput, ' edited');
      await user.click(screen.getByRole('button', { name: /CANCEL/i }));

      const discardBtn = await screen.findByRole('button', { name: /DISCARD CHANGES/i });
      await user.click(discardBtn);

      await waitFor(() => {
        expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
      });
      // Cancel must never write.
      expect(lastPutBody).toBeNull();
      expect(postMissionsCallCount).toBe(0);
    });

    it('Cancel after a successful Save does NOT prompt (dirty cleared)', async () => {
      const user = userEvent.setup();
      navigateSpy.mockClear();
      render(
        <TestProviders>
          <MissionDetailsEdit />
        </TestProviders>,
      );

      const titleInput = (await screen.findByLabelText(/Mission Title/i)) as HTMLInputElement;
      await user.clear(titleInput);
      await user.type(titleInput, 'Edited then saved');

      await user.click(screen.getByRole('button', { name: /SAVE CHANGES/i }));

      // Save fires + navigates as part of the existing Save contract.
      await waitFor(() => {
        expect(lastPutBody).not.toBeNull();
      });
      await waitFor(() => {
        expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
      });

      // The Save handler resets dirty state. A subsequent click on
      // Cancel (still mounted post-Save) must NOT fire the modal.
      navigateSpy.mockClear();
      const cancelBtn = screen.getByRole('button', { name: /CANCEL/i });
      await user.click(cancelBtn);

      expect(screen.queryByText(/Discard unsaved changes\?/i)).not.toBeInTheDocument();
      expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    });
  });
});
