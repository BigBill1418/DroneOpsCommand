"""OpenTelemetry bootstrap for DroneOpsCommand.

Sends OTLP/gRPC traces to the nearest Alloy collector, which in turn
cascades them to HSH-HQ Tempo over the WireGuard mesh. Endpoint is
read from ``OTEL_EXPORTER_OTLP_ENDPOINT`` — for prod the default is
``http://10.99.0.1:4317`` (HSH-HQ Alloy); the demo override pins
``http://10.99.0.2:4317`` (CHAD-HQ Alloy).

DSN-style gating: if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is empty, this is
a no-op — dev, test, and any single-tenant self-hosted DroneOps install
that does not run the central plane keeps working unchanged.

FastAPI is instrumented after app construction (see
``app/main.py`` → ``instrument_fastapi()``) because
``FastAPIInstrumentor`` needs the app handle. Celery, SQLAlchemy,
httpx, and stdlib ``logging`` are instrumented here because they don't.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("doc.observability.otel")

_DEFAULT_ENDPOINT = "http://10.99.0.1:4317"


def init_otel(service: str) -> bool:
    """Initialize the OTel SDK + auto-instrumentations for ``service``.

    Returns True on success, False if the endpoint is unset, the SDK is
    unavailable, or init raised. Safe to call multiple times — the
    ``trace.set_tracer_provider`` call guards against re-setting.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT).strip()
    if not endpoint:
        logger.info("otel.skipped: no OTEL_EXPORTER_OTLP_ENDPOINT set")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    except ImportError as exc:
        logger.warning("otel.sdk_not_installed: %s", exc)
        return False

    service_name = os.environ.get("OTEL_SERVICE_NAME", "").strip() or service
    env_tag = os.environ.get("ENV", "production").strip()
    tenant = os.environ.get("TENANT", "shared").strip()

    try:
        resource = Resource.create({
            "service.name": service_name,
            "service.namespace": "droneops",
            "deployment.environment": env_tag,
            "droneops.tenant": tenant,
        })

        current = trace.get_tracer_provider()
        if not isinstance(current, TracerProvider):
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
            )
            trace.set_tracer_provider(provider)
    except Exception:  # noqa: BLE001 — never fail startup on observability
        logger.exception("otel.provider_init_failed — continuing without OTel")
        return False

    # Auto-instrumentations that don't need an app handle. Each one is
    # wrapped so a single bad import / double-instrument doesn't take
    # down the next. FastAPI is wired from main.py.
    for name, install in (
        ("sqlalchemy", lambda: SQLAlchemyInstrumentor().instrument()),
        ("httpx", lambda: HTTPXClientInstrumentor().instrument()),
        ("celery", lambda: CeleryInstrumentor().instrument()),
        ("logging", lambda: LoggingInstrumentor().instrument(set_logging_format=False)),
    ):
        try:
            install()
        except Exception:  # noqa: BLE001 — instrumentation is best-effort
            logger.exception("otel.%s_instrument_failed", name)

    logger.info("otel.initialized service=%s endpoint=%s env=%s", service_name, endpoint, env_tag)
    return True


def instrument_fastapi(app) -> bool:
    """Attach the FastAPI instrumentor to a constructed app handle.

    Called from ``app/main.py`` after ``app = FastAPI(...)``. Gated on
    the same endpoint env var so it stays a no-op in dev.
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as exc:
        logger.warning("otel.fastapi_not_installed: %s", exc)
        return False
    try:
        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001
        logger.exception("otel.fastapi_instrument_failed")
        return False
    return True
