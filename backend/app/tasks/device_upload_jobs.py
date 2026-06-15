"""Redis-backed batch state for asynchronous device-upload flight-parse jobs.

ADR-0023 / audit P2-2 (the latency-coupling half): the legacy synchronous
``POST /api/flight-library/device-upload`` route streams each file to disk and
then **parses it synchronously inside the request** — the HTTP connection is
held open for the entire parse, which trips field cellular/Wi-Fi timeouts and
couples one slow file's failure to the rest of the batch (B1/B2 in the ADR).

This module is the device-upload analogue of ``backup_jobs.py`` (the v2.70.0
backup-job leg, ADR-0022). It moves the parse off the request onto a Celery
worker and exposes a per-batch progress overlay so the async route returns a
``202 + {batch_id}`` immediately and the client polls for per-file progress.

The batch state lives in Redis (not the Celery result backend's coarse
PENDING/SUCCESS/FAILURE) so the poller can read a rich per-file overlay
mid-flight. The connection pattern mirrors ``backup_jobs._redis_client`` — a
short-lived ``redis.from_url(settings.redis_url)`` client with a tight 2 s
socket timeout, best-effort (a Redis blip must never crash the task or route).
The Celery ``AsyncResult`` remains the terminal fallback; the Redis record is
the progress overlay and is keyed by the same server-minted ``batch_id``.

Both the producer (the Celery parse task) and the consumer (the flight_library
router) import these helpers so the key shape and field names cannot drift.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.config import settings

# Key namespace mirrors the backup-job prefix (``droneops:backup:job:``) and the
# worker-heartbeat prefix (``droneops:worker:*``).
_JOB_KEY_PREFIX = "droneops:flightupload:job:"
# Records self-expire so a crashed worker never leaks state. 24 h comfortably
# outlives any parse while keeping the keyspace bounded — identical to the
# backup job TTL (``backup_jobs._JOB_TTL_SECONDS``), per ADR-0023 §2.4.
_JOB_TTL_SECONDS = 24 * 60 * 60

# Terminal + in-flight batch-rollup status vocabulary, reused verbatim from
# ``backup_jobs``. ``queued`` is written by the producer (route) at enqueue
# time so a poll between enqueue and first task tick returns a meaningful
# status instead of a bare Celery PENDING.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


def _job_key(batch_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{batch_id}"


def _redis_client():
    """Short-lived Redis client — created per call, tight timeout, never pooled.

    Matches ``backup_jobs._redis_client`` / ``celery_tasks._write_heartbeat``:
    a local import keeps cold start unaffected and a 2 s socket timeout bounds
    the blast radius of a Redis wedge.
    """
    import redis  # local import — keeps module import cheap

    return redis.from_url(settings.redis_url, socket_timeout=2)


def write_batch_state(
    batch_id: str,
    *,
    status: str,
    phase: str,
    progress: int,
    per_file: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> None:
    """Persist a batch's state to Redis. Best-effort — a Redis failure is
    swallowed (the poll falls back to the Celery terminal state) and must never
    raise into task/route logic.

    ``per_file`` is the list of ``{name, state, imported, skipped, error}``
    dicts (ADR-0023 §2.1 envelope). ``progress`` is clamped to 0..100.
    """
    payload = json.dumps(
        {
            "status": status,
            "phase": phase,
            "progress": max(0, min(100, int(progress))),
            "per_file": per_file if per_file is not None else [],
            "error": error,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    try:
        _redis_client().setex(_job_key(batch_id), _JOB_TTL_SECONDS, payload)
    except Exception:  # pragma: no cover - best-effort; poll falls back to Celery state
        pass


def read_batch_state(batch_id: str) -> dict[str, Any] | None:
    """Read a batch's progress overlay from Redis, or ``None`` if absent/unreadable.

    ``None`` means "no Redis record" — the caller should fall back to the
    Celery ``AsyncResult`` terminal state (the two-tier read mirrors
    ``get_backup_job``).
    """
    try:
        raw = _redis_client().get(_job_key(batch_id))
    except Exception:  # pragma: no cover - best-effort
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):  # pragma: no cover - corrupt record
        return None
