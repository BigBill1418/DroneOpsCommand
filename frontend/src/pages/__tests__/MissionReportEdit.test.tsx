/**
 * MissionReportEdit contract test (v2.67.0 Mission Hub).
 *
 * Per ADR-0013 + spec §7: msw intercepts axios calls at the network
 * boundary and asserts:
 *
 *   1. Initial GET populates narrative + final_content from the
 *      existing report row.
 *   2. Save Draft → PUT /api/missions/{id}/report with the exact body.
 *   3. Generate AI → POST /api/missions/{id}/report/generate.
 *   4. Generate PDF → POST /api/missions/{id}/report/pdf.
 *   5. Send to Customer → POST /api/missions/{id}/report/send.
 *   6. CONTRACT: POST /api/missions handler call count = 0 after ALL
 *      of the above. The Report editor MUST NOT touch /missions.
 *   7. Cancel → navigate('/missions/abc-123').
 */
import {
  describe,
  it,
  expect,
  beforeAll,
  afterAll,
  afterEach,
  vi,
} from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionReportEdit from '../MissionReportEdit';
import TestProviders from '../../test/TestProviders';

const MISSION_ID = 'abc-123';

// Heavy components mocked to keep jsdom fast and deterministic. The
// contract under test is the API surface, not the editor or PDF viewer.
vi.mock('../../components/RichTextEditor/RichTextEditor', () => ({
  default: ({
    content,
    onChange,
  }: {
    content: string;
    onChange: (s: string) => void;
  }) => (
    <textarea
      data-testid="rte"
      value={content}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

vi.mock('../../components/PDFPreview/PdfViewer', () => ({
  default: ({ url }: { url: string }) => (
    <div data-testid="pdf-viewer" data-url={url} />
  ),
}));

// Spy on react-router-dom's useNavigate so we can assert routing.
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const mod: any = await vi.importActual('react-router-dom');
  return {
    ...mod,
    useNavigate: () => navigateSpy,
    useParams: () => ({ id: MISSION_ID }),
  };
});

// Track calls per route. The /missions POST handler is the load-bearing
// "must never fire" assertion; the rest are convenience counters.
let callCounts = {
  postMissions: 0,
  putReport: 0,
  postGenerate: 0,
  postPdf: 0,
  postSend: 0,
  getStatus: 0,
};
let lastPutReportBody: any = null;

const initialReport = {
  id: 'report-1',
  mission_id: MISSION_ID,
  user_narrative: 'Initial operator notes from the field.',
  final_content: '<p>Existing AI report content.</p>',
  include_download_link: false,
  generated_at: '2026-05-03T11:00:00Z',
  updated_at: '2026-05-03T11:00:00Z',
  sent_at: null,
};

const initialMission = {
  id: MISSION_ID,
  customer_id: 'cust-1',
  title: 'Solar Inspection',
  mission_type: 'inspection',
  description: null,
  mission_date: '2026-05-01',
  location_name: 'North Field',
  area_coordinates: null,
  status: 'draft',
  is_billable: true,
  unas_folder_path: null,
  download_link_url: 'https://example.com/footage.zip',
  download_link_expires_at: null,
  client_notes: null,
  created_at: '2026-05-01T10:00:00Z',
  updated_at: '2026-05-03T11:00:00Z',
  flights: [],
  images: [],
};

const server = setupServer(
  // Initial loads
  http.get(`*/api/missions/${MISSION_ID}`, () =>
    HttpResponse.json(initialMission),
  ),
  http.get(`*/api/missions/${MISSION_ID}/report`, () =>
    HttpResponse.json(initialReport),
  ),

  // Mutations under test
  http.put(`*/api/missions/${MISSION_ID}/report`, async ({ request }) => {
    callCounts.putReport++;
    lastPutReportBody = await request.json();
    return HttpResponse.json({
      ...initialReport,
      ...(lastPutReportBody as object),
      updated_at: '2026-05-03T12:00:00Z',
    });
  }),
  http.post(`*/api/missions/${MISSION_ID}/report/generate`, async () => {
    callCounts.postGenerate++;
    return HttpResponse.json({ task_id: 'task-xyz' });
  }),
  http.get(
    `*/api/missions/${MISSION_ID}/report/status/task-xyz`,
    () => {
      callCounts.getStatus++;
      // Deterministic: never report "complete" so the polling loop
      // doesn't fire follow-up effects in the test window.
      return HttpResponse.json({ status: 'pending' });
    },
  ),
  http.post(`*/api/missions/${MISSION_ID}/report/pdf`, async () => {
    callCounts.postPdf++;
    const blob = new Blob(['%PDF-1.4 fake'], { type: 'application/pdf' });
    return new HttpResponse(blob, {
      status: 200,
      headers: { 'content-type': 'application/pdf' },
    });
  }),
  http.post(`*/api/missions/${MISSION_ID}/report/send`, async () => {
    callCounts.postSend++;
    return HttpResponse.json({
      sent_at: '2026-05-03T12:05:00Z',
    });
  }),

  // CONTRACT: POST /api/missions MUST NOT fire from this page. If it
  // does, the handler count > 0 and the contract assertion fails
  // loudly at the end of every test.
  http.post('*/api/missions', () => {
    callCounts.postMissions++;
    return HttpResponse.json({ detail: 'forbidden in report editor' }, { status: 500 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  callCounts = {
    postMissions: 0,
    putReport: 0,
    postGenerate: 0,
    postPdf: 0,
    postSend: 0,
    getStatus: 0,
  };
  lastPutReportBody = null;
  navigateSpy.mockClear();
  // Re-register handlers since resetHandlers() wipes them.
  server.use(
    http.get(`*/api/missions/${MISSION_ID}`, () =>
      HttpResponse.json(initialMission),
    ),
    http.get(`*/api/missions/${MISSION_ID}/report`, () =>
      HttpResponse.json(initialReport),
    ),
    http.put(`*/api/missions/${MISSION_ID}/report`, async ({ request }) => {
      callCounts.putReport++;
      lastPutReportBody = await request.json();
      return HttpResponse.json({
        ...initialReport,
        ...(lastPutReportBody as object),
        updated_at: '2026-05-03T12:00:00Z',
      });
    }),
    http.post(`*/api/missions/${MISSION_ID}/report/generate`, async () => {
      callCounts.postGenerate++;
      return HttpResponse.json({ task_id: 'task-xyz' });
    }),
    http.get(`*/api/missions/${MISSION_ID}/report/status/task-xyz`, () => {
      callCounts.getStatus++;
      return HttpResponse.json({ status: 'pending' });
    }),
    http.post(`*/api/missions/${MISSION_ID}/report/pdf`, async () => {
      callCounts.postPdf++;
      const blob = new Blob(['%PDF-1.4 fake'], { type: 'application/pdf' });
      return new HttpResponse(blob, {
        status: 200,
        headers: { 'content-type': 'application/pdf' },
      });
    }),
    http.post(`*/api/missions/${MISSION_ID}/report/send`, async () => {
      callCounts.postSend++;
      return HttpResponse.json({ sent_at: '2026-05-03T12:05:00Z' });
    }),
    http.post('*/api/missions', () => {
      callCounts.postMissions++;
      return HttpResponse.json(
        { detail: 'forbidden in report editor' },
        { status: 500 },
      );
    }),
  );
});
afterAll(() => server.close());

// jsdom shims for browser APIs the page reaches for.
beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    (URL as any).createObjectURL = vi.fn(() => 'blob:mock');
  }
  if (typeof URL.revokeObjectURL !== 'function') {
    (URL as any).revokeObjectURL = vi.fn();
  }
});

describe('MissionReportEdit', () => {
  it('initial GET populates narrative + final_content', async () => {
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    // Wait for the loader to disappear and the form to mount.
    await screen.findByText(/EDIT REPORT/);

    // Narrative textarea reflects the loaded value.
    const narrative = await screen.findByPlaceholderText(
      /Describe what happened/,
    );
    expect((narrative as HTMLTextAreaElement).value).toBe(
      'Initial operator notes from the field.',
    );

    // Final content (mocked RichTextEditor renders into data-testid="rte").
    const rte = await screen.findByTestId('rte');
    expect((rte as HTMLTextAreaElement).value).toBe(
      '<p>Existing AI report content.</p>',
    );

    // Contract: no POST /missions during initial load.
    expect(callCounts.postMissions).toBe(0);
  });

  it('Save Draft sends PUT /api/missions/{id}/report with the exact body', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT REPORT/);
    await screen.findByPlaceholderText(/Describe what happened/);

    const saveBtn = screen.getByRole('button', { name: /SAVE DRAFT/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(callCounts.putReport).toBeGreaterThanOrEqual(1);
    });

    expect(lastPutReportBody).toEqual({
      user_narrative: 'Initial operator notes from the field.',
      final_content: '<p>Existing AI report content.</p>',
      include_download_link: false,
    });

    // Contract: POST /missions still untouched.
    expect(callCounts.postMissions).toBe(0);
  });

  it('Generate AI fires POST /api/missions/{id}/report/generate', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT REPORT/);
    await screen.findByPlaceholderText(/Describe what happened/);

    const generateBtn = screen.getByRole('button', {
      name: /REGENERATE REPORT|GENERATE REPORT/i,
    });
    await user.click(generateBtn);

    await waitFor(() => {
      expect(callCounts.postGenerate).toBe(1);
    });

    expect(callCounts.postMissions).toBe(0);
  });

  it('Generate PDF fires POST /api/missions/{id}/report/pdf', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT REPORT/);
    await screen.findByPlaceholderText(/Describe what happened/);

    const pdfBtn = screen.getByRole('button', { name: /GENERATE PDF/i });
    await user.click(pdfBtn);

    await waitFor(() => {
      expect(callCounts.postPdf).toBe(1);
    });

    expect(callCounts.postMissions).toBe(0);
  });

  it('Send to Customer fires POST /api/missions/{id}/report/send', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT REPORT/);
    await screen.findByPlaceholderText(/Describe what happened/);

    const sendBtn = screen.getByRole('button', { name: /SEND TO CUSTOMER/i });
    await user.click(sendBtn);

    await waitFor(() => {
      expect(callCounts.postSend).toBe(1);
    });

    expect(callCounts.postMissions).toBe(0);
  });

  it('Cancel navigates back to the Hub at /missions/{id}', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT REPORT/);
    const cancelBtn = screen.getByRole('button', { name: /Cancel/i });
    await user.click(cancelBtn);

    expect(navigateSpy).toHaveBeenCalledWith(`/missions/${MISSION_ID}`);
    expect(callCounts.postMissions).toBe(0);
  });

  it('CONTRACT: across all save/generate/pdf/send actions, POST /api/missions count = 0', async () => {
    const user = userEvent.setup();
    render(
      <TestProviders>
        <MissionReportEdit />
      </TestProviders>,
    );

    await screen.findByText(/EDIT REPORT/);
    await screen.findByPlaceholderText(/Describe what happened/);

    // Fire every mutation in sequence.
    await user.click(screen.getByRole('button', { name: /SAVE DRAFT/i }));
    await waitFor(() => expect(callCounts.putReport).toBeGreaterThanOrEqual(1));

    await user.click(
      screen.getByRole('button', {
        name: /REGENERATE REPORT|GENERATE REPORT/i,
      }),
    );
    await waitFor(() => expect(callCounts.postGenerate).toBe(1));

    await user.click(screen.getByRole('button', { name: /GENERATE PDF/i }));
    await waitFor(() => expect(callCounts.postPdf).toBe(1));

    await user.click(screen.getByRole('button', { name: /SEND TO CUSTOMER/i }));
    await waitFor(() => expect(callCounts.postSend).toBe(1));

    // The load-bearing assertion: no rogue POST /missions across any
    // facet-editor action. This is what makes duplicate-mission
    // creation physically impossible from this page.
    expect(callCounts.postMissions).toBe(0);
  });
});
