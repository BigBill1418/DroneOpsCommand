"""Shared pytest fixtures for the DroneOpsCommand backend test suite.

These tests target the ADR-0003 device-key rotation surface: auth-dep
dual-key matching, rotation endpoint behaviour, device-health hint
emission, and Celery finalizer promotion. They deliberately do NOT spin
up a full Postgres + Redis + FastAPI stack — the goal is fast, hermetic
unit coverage of the rotation logic, not an end-to-end integration test
of the upload pipeline (which is well-covered by manual smoke tests
against BOS-HQ and is what the operator validates on merge).

To run:

    cd backend
    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/test_device_key_rotation.py -v
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# ── Path bootstrap ─────────────────────────────────────────────────────
# The `app.*` package lives one directory up from `backend/tests/`. Make
# sure pytest can import it without a setup.py.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Stub minimal env so importing `app.config` does not error on missing
# secrets. None of the rotation tests touch SMTP / Stripe / etc.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


# ── Helpers ────────────────────────────────────────────────────────────
def _make_device_row(
    *,
    label: str = "Test RC Pro",
    key_hash: str = "a" * 64,
    is_active: bool = True,
    rotated_to_key_hash: str | None = None,
    rotation_grace_until: datetime | None = None,
) -> SimpleNamespace:
    """Create a stand-in for a `DeviceApiKey` row.

    Tests run without a real DB session; we use SimpleNamespace so the
    auth dep and endpoints can read attributes the same way they would
    on a SQLAlchemy ORM instance. ``_authenticated_via_old_key`` gets
    attached at runtime by the auth dep — same shape as in production.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        label=label,
        key_hash=key_hash,
        is_active=is_active,
        created_at=datetime.utcnow(),
        last_used_at=None,
        rotated_to_key_hash=rotated_to_key_hash,
        rotation_grace_until=rotation_grace_until,
    )


@pytest.fixture
def make_device_row():
    return _make_device_row


@pytest.fixture
def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@pytest.fixture
def grace_24h():
    return datetime.utcnow() + timedelta(hours=24)


@pytest.fixture
def grace_expired():
    return datetime.utcnow() - timedelta(minutes=1)
