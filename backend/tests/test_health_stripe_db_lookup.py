"""v2.67.4 — Tier 2 A7. The /api/health Stripe probe must consult
`system_settings` (DB row, set live via Settings UI) BEFORE the env
fallback. Pre-fix it only checked env, so a key pasted into Settings
showed as "stripe": "unconfigured" forever — misleading anyone
debugging.

Real-ORM-path tests per ADR-0013. The probe function takes a real
`AsyncSession`; we use the same hermetic in-memory SQLite engine the
tz-naive sync regression tests use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.system_settings import SystemSetting


@pytest.fixture
async def db():
    """Hermetic in-memory SQLite session — only the system_settings table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SystemSetting.__table__.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _reset_health_cache():
    """Reset the module-level _HEALTH_CACHE so each test starts cold."""
    from app.main import _HEALTH_CACHE
    _HEALTH_CACHE.update({"checked_at": 0.0, "stripe_status": None, "stripe_error": None})


@pytest.mark.asyncio
async def test_stripe_probe_reads_from_system_settings_when_db_has_key(db: AsyncSession):
    """If `system_settings.stripe_secret_key` is populated, the probe uses it
    even if the env-side `settings.stripe_secret_key` is empty."""
    _reset_health_cache()
    db.add(SystemSetting(key="stripe_secret_key", value="sk_test_FROM_DB_ROW_xxxxxxxxxxxxxxxxxx"))
    await db.commit()

    captured_key = {}

    def _capture_api_key():
        import stripe as _stripe
        captured_key["value"] = _stripe.api_key
        return None  # noop Account.retrieve

    with patch("app.config.settings") as mock_settings:
        mock_settings.stripe_secret_key = ""  # env says nothing
        with patch("stripe.Account.retrieve", side_effect=_capture_api_key):
            from app.main import _probe_stripe_cached
            status, err = await _probe_stripe_cached(db)

    assert status == "ok", f"expected ok, got {status} ({err})"
    assert captured_key.get("value") == "sk_test_FROM_DB_ROW_xxxxxxxxxxxxxxxxxx", (
        f"Stripe API key was not read from system_settings; got {captured_key.get('value')!r}"
    )


@pytest.mark.asyncio
async def test_stripe_probe_falls_back_to_env_when_db_empty(db: AsyncSession):
    """If system_settings has no row, env value is used."""
    _reset_health_cache()
    # No DB row added.

    captured_key = {}

    def _capture_api_key():
        import stripe as _stripe
        captured_key["value"] = _stripe.api_key
        return None

    with patch("app.config.settings") as mock_settings:
        mock_settings.stripe_secret_key = "sk_test_FROM_ENV_yyyyyyyyyyyyyyyyyy"
        with patch("stripe.Account.retrieve", side_effect=_capture_api_key):
            from app.main import _probe_stripe_cached
            status, _ = await _probe_stripe_cached(db)

    assert status == "ok"
    assert captured_key.get("value") == "sk_test_FROM_ENV_yyyyyyyyyyyyyyyyyy"


@pytest.mark.asyncio
async def test_stripe_probe_unconfigured_when_neither_db_nor_env_has_key(db: AsyncSession):
    """Both empty → 'unconfigured'."""
    _reset_health_cache()
    with patch("app.config.settings") as mock_settings:
        mock_settings.stripe_secret_key = ""
        from app.main import _probe_stripe_cached
        status, err = await _probe_stripe_cached(db)

    assert status == "unconfigured"
    assert err is None


@pytest.mark.asyncio
async def test_stripe_probe_db_lookup_failure_falls_back_to_env(db: AsyncSession):
    """If the DB lookup itself raises (e.g., service module import error),
    don't crash the probe — fall through to env."""
    _reset_health_cache()
    with patch("app.services.stripe_service.get_stripe_settings",
               side_effect=RuntimeError("simulated DB blip")):
        with patch("app.config.settings") as mock_settings:
            mock_settings.stripe_secret_key = "sk_test_ENV_FALLBACK_zzzzzzzzzzzzzzz"
            with patch("stripe.Account.retrieve", return_value=None):
                from app.main import _probe_stripe_cached
                status, _ = await _probe_stripe_cached(db)

    assert status == "ok"
