"""Light-touch PII scrubbing for DroneOps observability events.

DroneOps stores customer names, recipient emails, phone numbers (on
intake forms), mission details (location, narrative), and flight logs.
None of it is PHI — the only reportable PII classes that can leak into
Sentry events are emails and phone numbers embedded in free-text
strings (e.g. exception messages from SMTP send failures or Stripe
webhook handlers).

Unlike CallVault (which has a locked ``PII_KEYS`` list because voicemails
and transcripts can't leak even as key:value pairs), DroneOps uses only
the free-text regex redaction path. The ``_before_send`` hook in
:mod:`app.observability.sentry` invokes :func:`sanitize_event` on every
event + transaction; if the scrubber raises, the caller drops the event
rather than risk transmission of the unscrubbed payload.
"""

from __future__ import annotations

import re
from typing import Any

# Loose phone: +? then 10-15 digits, optional separators. Tuned to match
# US/international numbers in prose without snagging port numbers or
# fixed-size IDs (which rarely cross a ~10-digit boundary).
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{8,18}\d")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_MAX_DEPTH = 8


def _redact_string(value: str) -> str:
    """Strip phone numbers and emails from a free-text string."""
    value = _EMAIL_RE.sub("[REDACTED-EMAIL]", value)
    value = _PHONE_RE.sub("[REDACTED-PHONE]", value)
    return value


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub(v, depth + 1) for v in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Walk a Sentry event dict and strip PII in place.

    Returns the same dict (mutated). Idempotent and O(n) in event size.
    Exceptions inside the scrubber are swallowed at the caller
    (``_before_send``) because a failed scrub must not leak the
    unscrubbed event.
    """
    for field in ("message", "culprit", "logger", "server_name"):
        if field in event and isinstance(event[field], str):
            event[field] = _redact_string(event[field])

    for field in ("extra", "contexts", "tags", "request", "user"):
        if field in event and isinstance(event[field], (dict, list)):
            event[field] = _scrub(event[field])

    bc = event.get("breadcrumbs")
    if isinstance(bc, dict) and isinstance(bc.get("values"), list):
        bc["values"] = [_scrub(v) for v in bc["values"]]
    elif isinstance(bc, list):
        event["breadcrumbs"] = [_scrub(v) for v in bc]

    exc = event.get("exception")
    if isinstance(exc, dict) and isinstance(exc.get("values"), list):
        for entry in exc["values"]:
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                entry["value"] = _redact_string(entry["value"])

    return event
