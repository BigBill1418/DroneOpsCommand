"""ADR-0030 — report-generation output caps must never silently regress.

Mission after-action reports are client deliverables and run ~1.5–2.5k
tokens. A 1024-token output cap truncated EVERY report mid-sentence (the
savannah report stopped at exactly 1024 output tokens / ~2,895 chars). This
suite locks in a sane floor for both report-generation paths:

- Claude (`claude_llm.generate_report`)  → ``max_tokens``
- Ollama (`ollama.generate_report`)       → ``num_predict`` (output) and
                                            ``num_ctx`` (must hold input + output)

If anyone drops a cap back below the floor, these tests fail loudly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from app.services import claude_llm, ollama

# A full report runs ~1.5–2.5k tokens; require clear headroom above that.
MIN_OUTPUT_TOKENS = 4096
# The window must hold system prompt + flight data + narrative + full output.
MIN_CONTEXT_TOKENS = 8192


def test_claude_max_tokens_above_floor() -> None:
    """generate_report must request at least MIN_OUTPUT_TOKENS from Claude."""
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
                model="claude-sonnet-4-6",
            )
        )

    assert captured["max_tokens"] >= MIN_OUTPUT_TOKENS, (
        f"Claude max_tokens={captured['max_tokens']} is below the "
        f"{MIN_OUTPUT_TOKENS}-token floor — reports will truncate."
    )


def _capture_ollama_options() -> dict:
    """Call ollama.generate_report with a mocked httpx client; return options."""
    captured: dict = {}

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {"response": "report body"}
    fake_resp.status_code = 200
    fake_resp.elapsed.total_seconds.return_value = 0.1

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            captured.update(json or {})
            return fake_resp

    with patch.object(ollama.httpx, "AsyncClient", FakeClient):
        asyncio.run(
            ollama.generate_report(
                user_narrative="did a survey",
                mission_title="Test Mission",
                mission_type="survey",
                location="Somewhere",
                flight_summaries=[{"aircraft": "M30", "max_altitude": "120m"}],
            )
        )

    return captured.get("options", {})


def test_ollama_num_predict_above_floor() -> None:
    """generate_report must request at least MIN_OUTPUT_TOKENS from Ollama."""
    options = _capture_ollama_options()
    assert options.get("num_predict", 0) >= MIN_OUTPUT_TOKENS, (
        f"Ollama num_predict={options.get('num_predict')} is below the "
        f"{MIN_OUTPUT_TOKENS}-token floor — reports will truncate."
    )


def test_ollama_num_ctx_holds_input_plus_output() -> None:
    """num_ctx must be large enough to hold the input prompt plus the output.

    If num_ctx <= num_predict the window cannot fit any input and the model
    squeezes/truncates the generation.
    """
    options = _capture_ollama_options()
    num_ctx = options.get("num_ctx", 0)
    num_predict = options.get("num_predict", 0)
    assert num_ctx >= MIN_CONTEXT_TOKENS, (
        f"Ollama num_ctx={num_ctx} is below the {MIN_CONTEXT_TOKENS}-token floor."
    )
    assert num_ctx > num_predict, (
        f"Ollama num_ctx={num_ctx} must exceed num_predict={num_predict} so the "
        "window can hold input as well as output."
    )
