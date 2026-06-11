"""Redis-backed job state for asynchronous backup create/restore jobs.

FU-8 #4 (audit 2026-06-11): the backup create/restore endpoints offload
``pg_dump``/``pg_restore`` to a worker thread *within the request* — the
event loop stays live, but the HTTP connection is still held open for the
full dump/restore duration. This module moves that work to a Celery job and
exposes a progress-polling surface, so the request returns immediately with
a job id and the client polls for phase/progress.

The job state lives in Redis (not the Celery result backend's coarse
PENDING/SUCCESS/FAILURE) so the poller can read a rich ``{phase, progress}``
mid-flight. The connection pattern mirrors the worker heartbeat in
``celery_tasks.py`` — a short-lived ``redis.from_url(settings.redis_url)``
client with a tight socket timeout, best-effort (a Redis blip must not crash
the task). The Celery ``AsyncResult`` remains the terminal source of truth;
the Redis record is the progress overlay and is keyed by the same job id.

Both the producer (the Celery task) and the consumer (the backup router)
import these helpers so the key shape and field names cannot drift.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.config import settings

# Key namespace mirrors the worker-heartbeat prefix (``droneops:worker:*``).
_JOB_KEY_PREFIX = "droneops:backup:job:"
# Records self-expire so a crashed worker never leaks state. 24 h comfortably
# outlives any dump/restore while keeping the keyspace bounded.
_JOB_TTL_SECONDS = 24 * 60 * 60

# Terminal + in-flight status vocabulary. ``queued`` is written by the
# producer (router) at enqueue time so a poll between enqueue and first task
# tick returns a meaningful status instead of a bare Celery PENDING.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


def _redis_client():
    """Short-lived Redis client — created per call, tight timeout, never pooled.

    Matches ``celery_tasks._write_heartbeat``: a local import keeps cold start
    unaffected and a 2 s socket timeout bounds the blast radius of a Redis
    wedge.
    """
    import redis  # local import — keeps module import cheap

    return redis.from_url(settings.redis_url, socket_timeout=2)


def write_job_state(
    job_id: str,
    *,
    status: str,
    phase: str,
    progress: int,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Persist a job's state to Redis. Best-effort — a Redis failure is logged
    by the caller's context but must never raise into task/route logic."""
    payload = json.dumps(
        {
            "status": status,
            "phase": phase,
            "progress": max(0, min(100, int(progress))),
            "result": result,
            "error": error,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    try:
        _redis_client().setex(_job_key(job_id), _JOB_TTL_SECONDS, payload)
    except Exception:  # pragma: no cover - best-effort; poll falls back to Celery state
        pass


def read_job_state(job_id: str) -> dict[str, Any] | None:
    """Read a job's progress overlay from Redis, or ``None`` if absent/unreadable.

    ``None`` means "no Redis record" — the caller should fall back to the
    Celery ``AsyncResult`` terminal state.
    """
    try:
        raw = _redis_client().get(_job_key(job_id))
    except Exception:  # pragma: no cover - best-effort
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):  # pragma: no cover - corrupt record
        return None
