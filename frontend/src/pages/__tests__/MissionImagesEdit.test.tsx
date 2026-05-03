/**
 * MissionImagesEdit contract test (v2.67.0 Mission Hub).
 *
 * Per ADR-0013 + spec §7: msw at the network boundary asserts:
 *
 *   1. Upload → POST /api/missions/{id}/images (multipart).
 *   2. Delete → DELETE /api/missions/{id}/images/{image_id}.
 *   3. CONTRACT: POST /api/missions is NEVER fired from this page.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionImagesEdit from '../MissionImagesEdit';
import TestProviders from '../../test/TestProviders';

const MISSION_ID = 'abc-123';
const EXISTING_IMAGE_ID = 'img-existing-1';

let lastPostImageUrl: string | null = null;
let lastPostImageContentType: string | null = null;
let lastDeleteImageUrl: string | null = null;
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
    flights: [],
    images: [
      {
        id: EXISTING_IMAGE_ID,
        file_path: '/data/uploads/existing-photo.jpg',
        caption: null,
        sort_order: 0,
      },
    ],
  };
}

function buildHandlers() {
  return [
    http.get(`*/api/missions/${MISSION_ID}`, () => HttpResponse.json(freshMission())),
    http.post(`*/api/missions/${MISSION_ID}/images`, async ({ request }) => {
      lastPostImageUrl = request.url;
      lastPostImageContentType = request.headers.get('content-type');
      return HttpResponse.json(
        {
          id: 'img-uploaded-1',
          file_path: '/data/uploads/uploaded.jpg',
          caption: null,
          sort_order: 1,
        },
        { status: 201 },
      );
    }),
    http.delete(`*/api/missions/${MISSION_ID}/images/:imageId`, ({ request }) => {
      lastDeleteImageUrl = request.url;
      return new HttpResponse(null, { status: 204 });
    }),
    // CONTRACT TRIPWIRE.
    http.post('*/api/missions', () => {
      postMissionsCallCount++;
      return HttpResponse.json({ id: 'should-never-happen' }, { status: 201 });
    }),
  ];
}

const server = setupServer(...buildHandlers());

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
  // jsdom lacks URL.createObjectURL/revokeObjectURL — shim for the
  // optimistic-thumbnail path inside MissionImagesEdit.
  if (typeof URL.createObjectURL !== 'function') {
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = () =>
      'blob:mock';
  }
  if (typeof URL.revokeObjectURL !== 'function') {
    (URL as unknown as { revokeObjectURL: (u: string) => void }).revokeObjectURL = () => {};
  }
});
afterEach(() => {
  server.resetHandlers(...buildHandlers());
  lastPostImageUrl = null;
  lastPostImageContentType = null;
  lastDeleteImageUrl = null;
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

describe('MissionImagesEdit', () => {
  it('Upload fires POST /api/missions/{id}/images (multipart) and NEVER POST /api/missions', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TestProviders>
        <MissionImagesEdit />
      </TestProviders>,
    );

    // Wait for the dropzone to render.
    await screen.findByText(/Drag images here/i);

    // Mantine Dropzone renders a hidden file input we can drive directly.
    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();

    const file = new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], 'smoke.jpg', {
      type: 'image/jpeg',
    });
    await user.upload(fileInput as HTMLInputElement, file);

    await waitFor(() => {
      expect(lastPostImageUrl).not.toBeNull();
    });

    expect(lastPostImageUrl).toMatch(new RegExp(`/api/missions/${MISSION_ID}/images$`));
    expect(lastPostImageContentType).toMatch(/multipart\/form-data/);
    // Load-bearing invariant.
    expect(postMissionsCallCount).toBe(0);
  });

  it('Delete fires DELETE /api/missions/{id}/images/{image_id} and NEVER POST /api/missions', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionImagesEdit />
      </TestProviders>,
    );

    // The existing image should render — find its delete button.
    const deleteBtn = await screen.findByRole('button', { name: /Delete existing-photo\.jpg/i });
    await user.click(deleteBtn);

    await waitFor(() => {
      expect(lastDeleteImageUrl).not.toBeNull();
    });

    expect(lastDeleteImageUrl).toMatch(
      new RegExp(`/api/missions/${MISSION_ID}/images/${EXISTING_IMAGE_ID}$`),
    );
    expect(postMissionsCallCount).toBe(0);
  });

  it('Done navigates to /missions/{id} (the Hub) without firing any /missions write', async () => {
    const user = userEvent.setup();
    navigateSpy.mockClear();
    render(
      <TestProviders>
        <MissionImagesEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT IMAGES/i);
    const doneBtns = await screen.findAllByRole('button', { name: /^DONE$/i });
    await user.click(doneBtns[0]);

    expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    expect(postMissionsCallCount).toBe(0);
    expect(lastPostImageUrl).toBeNull();
    expect(lastDeleteImageUrl).toBeNull();
  });
});
