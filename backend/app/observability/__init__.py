"""Observability bootstrap for DroneOpsCommand.

Phase 5 of the BarnardHQ observability rollout. Wires Sentry/GlitchTip
(error + transaction capture) and OpenTelemetry (traces) against the
central plane on HSH-HQ (``10.99.0.1:9000`` + ``10.99.0.1:4317``) for
prod, ``10.99.0.2:4317`` for the demo override. Both inits are
DSN/endpoint gated — unset env means no-op, so dev and single-tenant
deployments keep working unchanged.

PII posture: DroneOps stores customer names, recipient emails, and
mission metadata. The ``_before_send`` hook in :mod:`sentry` redacts
emails + loose phone numbers from free-text strings before events leave
the process; there is no dedicated PII key list like CallVault's because
DroneOps does not store call transcripts or recordings. See
``docs/adr/0001-observability.md`` for the decision record.
"""

from app.observability.sentry import init_sentry
from app.observability.otel import init_otel, instrument_fastapi

__all__ = ["init_sentry", "init_otel", "instrument_fastapi"]
