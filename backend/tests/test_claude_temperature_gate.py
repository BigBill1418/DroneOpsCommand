"""The Claude Opus 4.x models 400 on the `temperature` parameter
("`temperature` is deprecated for this model"). Sonnet/Haiku still honor it,
and a low temperature keeps factual after-action reports deterministic — so the
parameter must be sent for those models and omitted for Opus 4.x.
"""
from app.services.claude_llm import _supports_temperature


def test_opus_4x_does_not_accept_temperature():
    assert _supports_temperature("claude-opus-4-7") is False
    assert _supports_temperature("claude-opus-4-1") is False


def test_sonnet_and_haiku_accept_temperature():
    assert _supports_temperature("claude-sonnet-4-6") is True
    assert _supports_temperature("claude-haiku-4-5-20251001") is True
