"""ADR-0050 won write-back service."""
import uuid

import pytest

from app.services import lead_source, lead_writeback
from app.services.lead_source import LeadNotFound, LeadSourceUnavailable


class FakeClient:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    async def mark_won(self, key, mission_ref=None):
        self.calls.append((key, mission_ref))
        if self.exc:
            raise self.exc
        return {"key": key, "stage": "won"}


class FakeSession:
    executed: list = []
    committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        FakeSession.executed.append(stmt)

    async def commit(self):
        FakeSession.committed += 1


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    FakeSession.executed, FakeSession.committed = [], 0
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t")


def test_should_write_back(monkeypatch):
    assert lead_writeback.should_write_back("web-1")
    assert lead_writeback.should_write_back("cold-3")
    assert not lead_writeback.should_write_back(None)
    assert not lead_writeback.should_write_back("invoice-9")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "")
    assert not lead_writeback.should_write_back("web-1")


async def test_success_stamps_mission():
    mid = uuid.uuid4()
    client = FakeClient()
    ok = await lead_writeback.run_lead_writeback(mid, "web-1", client=client, session_factory=FakeSession)
    assert ok is True
    assert client.calls == [("web-1", str(mid))]
    assert len(FakeSession.executed) == 1 and FakeSession.committed == 1


@pytest.mark.parametrize("exc", [LeadSourceUnavailable("down"), LeadNotFound("gone")])
async def test_failure_returns_false_and_does_not_stamp(exc):
    ok = await lead_writeback.run_lead_writeback(
        uuid.uuid4(), "web-1", client=FakeClient(exc), session_factory=FakeSession
    )
    assert ok is False
    assert FakeSession.executed == [] and FakeSession.committed == 0


async def test_disabled_without_client_is_noop(monkeypatch):
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "")
    ok = await lead_writeback.run_lead_writeback(uuid.uuid4(), "web-1", session_factory=FakeSession)
    assert ok is False and FakeSession.executed == []
