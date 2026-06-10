"""Regression: ``generate_report`` must NOT send the ``temperature`` parameter.

The configured model (claude-sonnet-4-6) and the Opus 4.x family return a 400
"`temperature` is deprecated for this model" when ``temperature`` is sent
(GlitchTip issues 2243-2245). The previous per-model ``_supports_temperature``
gate assumed Sonnet/Haiku still honored it — production proved that wrong — so
the parameter is now dropped unconditionally. This test locks that in by
asserting the kwarg never reaches ``client.messages.create``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from app.services import claude_llm


def _run_generate(model: str) -> dict:
    """Call generate_report with a mocked Anthropic client; return create() kwargs."""
    captured: dict = {}

    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="report body")]
    fake_message.usage = MagicMock(input_tokens=10, output_tokens=20)

    def fake_create(**kwargs):
        captured.update(kwargs)
        return fake_message

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = fake_create

    with patch.object(claude_llm.anthropic, "Anthropic", return_value=fake_client):
        asyncio.run(
            claude_llm.generate_report(
                user_narrative="did a survey",
                mission_title="Test Mission",
                mission_type="survey",
                location="Somewhere",
                flight_summaries=[{"aircraft": "M30", "max_altitude": "120m"}],
                api_key="sk-test-key",
                model=model,
            )
        )
    return captured


def test_temperature_not_sent_for_sonnet():
    kwargs = _run_generate("claude-sonnet-4-6")
    assert "temperature" not in kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"


def test_temperature_not_sent_for_opus():
    kwargs = _run_generate("claude-opus-4-7")
    assert "temperature" not in kwargs


def test_temperature_gate_helper_removed():
    # The brittle per-model gate must be gone — its assumption (Sonnet honors
    # temperature) was contradicted by a live 400.
    assert not hasattr(claude_llm, "_supports_temperature")
