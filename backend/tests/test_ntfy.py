"""Tests for ADR-0036 ntfy notification transport.

Coverage:

  - Primary success path                     (test_send_alert_primary_succeeds)
  - Primary fail -> fallback success         (test_send_alert_falls_back_with_prefix)
  - Both fail -> returns False               (test_send_alert_returns_false_when_all_fail)
  - Unconfigured token -> True (no-op)       (test_send_alert_noop_when_unconfigured)
  - Dedup suppresses second call             (test_send_alert_dedup_suppresses_repeat)
  - Redis unavailable -> sends anyway        (test_send_alert_redis_down_fails_open)
  - Pushover-shaped priority -> ntfy named   (test_priority_mapping)
  - Bearer auth header carried on primary    (test_primary_carries_bearer)
  - Fallback omits Authorization header      (test_fallback_no_auth_header)
  - Title prefix [DroneOpsCommand]           (test_title_prefix_applied)
  - Default click is NOC status fallback     (test_default_click_url)

Approach: hermetic — patch ``httpx.AsyncClient`` and the redis client
where the module imports them. No real HTTP, no real Redis. Mirrors
the ``test_device_key_rotation.py`` style.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ─────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text


def _async_client_mock(post_side_effect):
    """Return a MagicMock standing in for ``httpx.AsyncClient`` whose
    ``post`` returns ``post_side_effect`` (a value or an exception)."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if isinstance(post_side_effect, Exception):
        client.post = AsyncMock(side_effect=post_side_effect)
    else:
        client.post = AsyncMock(return_value=post_side_effect)
    return client


def _httpx_factory(primary_resp, fallback_resp=None):
    """Build a side_effect for ``httpx.AsyncClient(...)`` that returns
    distinct mock clients on each call. First call -> primary, second
    call -> fallback."""
    calls: list = []

    def factory(*_args, **_kwargs):
        if not calls:
            calls.append("primary")
            return _async_client_mock(primary_resp)
        calls.append("fallback")
        return _async_client_mock(fallback_resp if fallback_resp is not None else _Resp(200))

    return factory, calls


def _set_token(token: str = "tk_publisher_test_token"):
    """Patch settings to expose a publisher token."""
    return patch("app.services.ntfy.settings", new=SimpleNamespace(
        ntfy_droneops_publisher_token=token,
        redis_url="redis://localhost:6379/15",
    ))


# ── Public-API tests ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_alert_primary_succeeds():
    from app.services import ntfy

    factory, calls = _httpx_factory(_Resp(200))
    with _set_token(), patch.object(ntfy.httpx, "AsyncClient", side_effect=factory):
        result = await ntfy.send_alert("hello", "world")

    assert result is True
    # Only one client was constructed — fallback never reached.
    assert calls == ["primary"]


@pytest.mark.asyncio
async def test_send_alert_falls_back_with_prefix(caplog):
    """Primary 5xx -> fallback called, fallback returns 200."""
    from app.services import ntfy

    factory, calls = _httpx_factory(_Resp(500, "boom"), _Resp(200))
    captured_headers: dict[str, dict] = {}

    async def post_recorder(url, content, headers):  # noqa: ARG001
        captured_headers[url] = headers
        if "ntfy.sh" in url:
            return _Resp(200)
        return _Resp(500, "boom")

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=post_recorder)

    with _set_token(), patch.object(ntfy.httpx, "AsyncClient", return_value=client):
        result = await ntfy.send_alert("device offline", "M4TD silent for 50h")

    assert result is True
    # Primary URL hit AND fallback URL hit
    assert any("ntfy.barnardhq.com" in u for u in captured_headers)
    assert any("ntfy.sh" in u for u in captured_headers)
    # Fallback Title is prefixed
    fallback_headers = next(h for u, h in captured_headers.items() if "ntfy.sh" in u)
    assert fallback_headers["Title"].startswith("[FALLBACK] [DroneOpsCommand] ")


@pytest.mark.asyncio
async def test_send_alert_returns_false_when_all_fail():
    from app.services import ntfy

    factory, _ = _httpx_factory(_Resp(500), _Resp(503))
    with _set_token(), patch.object(ntfy.httpx, "AsyncClient", side_effect=factory):
        result = await ntfy.send_alert("any", "any")

    assert result is False


@pytest.mark.asyncio
async def test_send_alert_noop_when_unconfigured():
    """Token unset -> skip cleanly, return True (preserved from pushover module)."""
    from app.services import ntfy

    with patch("app.services.ntfy.settings", new=SimpleNamespace(
        ntfy_droneops_publisher_token="",
        redis_url="redis://localhost:6379/15",
    )), patch.object(ntfy.httpx, "AsyncClient") as factory:
        result = await ntfy.send_alert("title", "msg")

    assert result is True
    # No HTTP call attempted
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_send_alert_dedup_suppresses_repeat():
    """Two calls with the same dedup_key inside the TTL -> only one publish."""
    from app.services import ntfy

    # First SETNX returns "OK" (acquired). Second returns None (suppressed).
    setnx_mock = AsyncMock(side_effect=[True, None])
    redis_client = MagicMock()
    redis_client.set = setnx_mock
    redis_client.aclose = AsyncMock(return_value=None)

    factory_calls: list = []

    def httpx_factory(*_a, **_k):
        factory_calls.append(1)
        return _async_client_mock(_Resp(200))

    with _set_token(), patch.object(
        ntfy.aioredis, "from_url", return_value=redis_client
    ), patch.object(ntfy.httpx, "AsyncClient", side_effect=httpx_factory):
        r1 = await ntfy.send_alert("t", "m", dedup_key="device_silence:abc")
        r2 = await ntfy.send_alert("t", "m", dedup_key="device_silence:abc")

    assert r1 is True
    assert r2 is True  # suppressed = True (no-op success)
    # Only the first call hit httpx
    assert len(factory_calls) == 1


@pytest.mark.asyncio
async def test_send_alert_redis_down_fails_open():
    """Redis raises -> alert is still sent (preserved fail-open behaviour)."""
    from app.services import ntfy

    factory, calls = _httpx_factory(_Resp(200))
    with _set_token(), patch.object(
        ntfy.aioredis, "from_url", side_effect=ConnectionError("redis down")
    ), patch.object(ntfy.httpx, "AsyncClient", side_effect=factory):
        result = await ntfy.send_alert("t", "m", dedup_key="anything")

    assert result is True
    # Despite Redis failure, primary publish was attempted
    assert calls == ["primary"]


# ── Header / contract tests ─────────────────────────────────────────────
def test_priority_mapping():
    from app.services.ntfy import _map_priority

    assert _map_priority(-1) == "low"
    assert _map_priority(0) == "default"
    assert _map_priority(1) == "high"
    assert _map_priority(2) == "urgent"
    assert _map_priority(99) == "urgent"


def test_primary_carries_bearer():
    from app.services.ntfy import _build_headers

    h = _build_headers(
        title="x",
        priority=1,
        click=None,
        tags=None,
        publisher_token="tk_secret",
        fallback=False,
    )
    assert h["Authorization"] == "Bearer tk_secret"
    assert h["Priority"] == "high"


def test_fallback_no_auth_header():
    from app.services.ntfy import _build_headers

    h = _build_headers(
        title="x",
        priority=0,
        click=None,
        tags=None,
        publisher_token="tk_secret",
        fallback=True,
    )
    assert "Authorization" not in h
    assert h["Title"].startswith("[FALLBACK] [DroneOpsCommand] ")


def test_title_prefix_applied():
    from app.services.ntfy import _build_headers

    h = _build_headers(
        title="device silent",
        priority=0,
        click=None,
        tags=None,
        publisher_token="tk",
        fallback=False,
    )
    assert h["Title"] == "[DroneOpsCommand] device silent"


def test_default_click_url():
    """When the caller doesn't pass click=, headers fall back to the
    NOC status page per ADR-0036 §Click URL priority tier 3."""
    from app.services.ntfy import _build_headers

    h = _build_headers(
        title="x",
        priority=0,
        click=None,
        tags=None,
        publisher_token="tk",
        fallback=False,
    )
    assert h["Click"] == "https://noc-mastercontrol.barnardhq.com/status/droneops"


def test_explicit_click_overrides_default():
    from app.services.ntfy import _build_headers

    h = _build_headers(
        title="x",
        priority=0,
        click="https://droneops.barnardhq.com/devices/abc",
        tags=None,
        publisher_token="tk",
        fallback=False,
    )
    assert h["Click"] == "https://droneops.barnardhq.com/devices/abc"


def test_tags_are_comma_joined():
    from app.services.ntfy import _build_headers

    h = _build_headers(
        title="x",
        priority=0,
        click=None,
        tags=["warning", "device_silence", ""],  # empty filtered
        publisher_token="tk",
        fallback=False,
    )
    assert h["Tags"] == "warning,device_silence"
