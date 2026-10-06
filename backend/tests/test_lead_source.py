"""Website-lead client (ADR-0050)."""
import httpx
import pytest

from app.services import lead_source
from app.services.lead_source import (
    LEAD_KEY_RE, LeadNotFound, LeadSourceClient, LeadSourceUnavailable,
)


def _client(handler):
    return LeadSourceClient("http://mkt:3002/", "tok", transport=httpx.MockTransport(handler))


def test_lead_key_pattern():
    assert LEAD_KEY_RE.match("web-1") and LEAD_KEY_RE.match("cold-42")
    for bad in ["web-", "x-1", "web-1/../x", "web-1234567890123", ""]:
        assert not LEAD_KEY_RE.match(bad)


def test_is_enabled_needs_both(monkeypatch):
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "")
    assert lead_source.is_enabled() is False
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t")
    assert lead_source.is_enabled() is True


def test_get_client_disabled_raises(monkeypatch):
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "")
    with pytest.raises(LeadSourceUnavailable):
        lead_source.get_client()


async def test_list_leads_sends_auth_and_params():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"leads": [{"key": "web-1"}]})

    out = await _client(handler).list_leads(q="acme", include_closed=True, limit=10)
    assert out == [{"key": "web-1"}]
    assert seen["auth"] == "Bearer tok"
    assert seen["url"].startswith("http://mkt:3002/api/doc/leads?")
    assert "q=acme" in seen["url"] and "include_closed=1" in seen["url"] and "limit=10" in seen["url"]


async def test_get_lead_404_raises_not_found():
    with pytest.raises(LeadNotFound):
        await _client(lambda r: httpx.Response(404, json={})).get_lead("web-9")


async def test_get_lead_rejects_bad_key_without_request():
    called = False

    def handler(req):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with pytest.raises(LeadNotFound):
        await _client(handler).get_lead("../admin")
    assert called is False


@pytest.mark.parametrize("status", [401, 500, 503])
async def test_non_404_errors_are_unavailable(status):
    with pytest.raises(LeadSourceUnavailable):
        await _client(lambda r: httpx.Response(status, json={})).list_leads()


async def test_transport_error_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LeadSourceUnavailable):
        await _client(handler).list_leads()


async def test_mark_won_posts_mission_ref():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = req.content
        return httpx.Response(200, json={"key": "web-1", "stage": "won"})

    out = await _client(handler).mark_won("web-1", "m-1")
    assert out == {"key": "web-1", "stage": "won"}
    assert seen["path"] == "/api/doc/leads/web-1/won"
    assert b'"mission_ref":"m-1"' in seen["body"].replace(b" ", b"")
