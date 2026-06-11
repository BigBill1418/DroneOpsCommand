/**
 * Backup job-API client helper (FU-8 #4).
 *
 * The synchronous /backup/create-and-download and /backup/restore-from-upload
 * endpoints hold the HTTP connection open for the entire dump/restore. The job
 * API moves that work off the request path: POST /api/backup/jobs returns 202 +
 * a job id; the worker streams {phase, progress} to Redis; GET
 * /api/backup/jobs/{id} polls it. This module polls that endpoint and resolves
 * on a terminal state.
 *
 * Version-skew safety: during a deploy the frontend can be newer than the
 * backend (no /jobs route yet). `jobApiUnavailable()` lets callers detect a 404
 * on the job endpoints and fall back to the still-supported synchronous calls,
 * so backups never break mid-deploy.
 */

import type { AxiosError } from 'axios';
import api from '../../api/client';
import type { BackupJobStatus } from '../../api/types';

const POLL_INTERVAL_MS = 2000;
// Generous ceiling: a large dump/restore can run up to the backend's 600s
// command timeout. 450 polls * 2s = 900s leaves headroom before we give up.
const MAX_POLLS = 450;

const TERMINAL_OK = 'complete';
const TERMINAL_FAIL = 'failed';

/** True when an error means the job endpoints are absent (older backend). */
export function jobApiUnavailable(err: unknown): boolean {
  const ax = err as AxiosError | undefined;
  return ax?.response?.status === 404;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Poll a backup job to completion, invoking `onTick` with each status read so
 * the UI can render phase + progress. Resolves with the terminal status on
 * success; rejects on a `failed` status, a polling timeout, or a non-404
 * transport error. A 404 on the *poll* (job id unknown) is surfaced as an
 * error too — the caller decides whether to fall back.
 */
export async function pollBackupJob(
  jobId: string,
  onTick: (status: BackupJobStatus) => void,
): Promise<BackupJobStatus> {
  for (let i = 0; i < MAX_POLLS; i++) {
    const resp = await api.get<BackupJobStatus>(`/backup/jobs/${jobId}`);
    const status = resp.data;
    onTick(status);

    if (status.status === TERMINAL_OK) return status;
    if (status.status === TERMINAL_FAIL) {
      throw new Error(status.error || 'Backup job failed');
    }
    await sleep(POLL_INTERVAL_MS);
  }
  throw new Error('Backup job timed out while polling for completion');
}
