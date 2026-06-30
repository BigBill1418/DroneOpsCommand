"""Regression guard for the mission-report audience leak.

Background: the original ``SYSTEM_PROMPT_TEMPLATE`` (shared by both Claude
and Ollama report-generation paths) had a Section 5 that asked the LLM for
"Recommendations - Follow-up actions or suggestions for the client", which
in practice caused the model to write second-person operator-coaching
content into a client-facing artifact ("you should adjust your flight
pattern", "next time consider lowering altitude", etc.).

This test file locks the fix in two layers:

1. **Structural** — the system prompt and user prompt strings contain the
   audience guardrails introduced by the fix, and the old leaky phrasing
   is gone. If a future edit relaxes the guardrails the test fails before
   any LLM call is made.

2. **Detector** — ``app.services.report_audience.detect_audience_leaks``
   correctly flags known-bad output and lets known-good output through.
   The detector is the post-generation second-line defense.

These tests are fully hermetic — no network, no LLM call, no DB. They
exercise the prompt template strings and the regex detector directly.
"""

from __future__ import annotations

import pytest

from app.services.claude_llm import generate_report as _claude_generate  # noqa: F401  (import-time check)
from app.services.ollama import SYSTEM_PROMPT_TEMPLATE
from app.services.report_audience import (
    AudienceLeak,
    detect_audience_leaks,
    has_audience_leak,
)


# ────────────────────────────── Structural ──────────────────────────────


class TestSystemPromptGuardrails:
    """The shared system prompt must hold the audience guardrails."""

    @pytest.fixture
    def rendered(self) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(company_name="DroneOps")

    def test_explicitly_names_audience_as_client(self, rendered: str) -> None:
        # Two anchors so renaming one doesn't silently weaken the prompt.
        assert "reader is the CLIENT" in rendered
        assert "They are NOT the pilot" in rendered

    def test_forbids_second_person_to_operator(self, rendered: str) -> None:
        assert "NEVER address the operator" in rendered
        # The explicit FORBIDDEN-phrases enumeration is the highest-signal
        # guardrail; if a future edit deletes it, alert.
        assert "FORBIDDEN" in rendered
        assert "you should have" in rendered

    def test_section_5_is_client_follow_up_not_pilot_recommendations(
        self, rendered: str
    ) -> None:
        """The old leaky phrasing must be gone; the new client-scoped
        section must explicitly forbid pilot advice in its body."""
        assert "Client Follow-Up Items" in rendered
        # OLD phrasing that caused the incident — must NOT come back.
        assert "Follow-up actions or suggestions for the client" not in rendered
        assert "pilot advice" in rendered  # part of "do NOT fill it with pilot advice"

    def test_operator_notes_framed_as_context_not_for_echo(self, rendered: str) -> None:
        assert "Operator Notes" in rendered
        assert "CONTEXT" in rendered
        assert "Do NOT echo them back as advice" in rendered


# ────────────────────────────── Detector ────────────────────────────────


class TestAudienceLeakDetector:
    """The regex detector is the post-generation guard."""

    @pytest.mark.parametrize(
        "bad_text, expected_rule",
        [
            (
                "Recommendations: You should adjust your flight pattern next time.",
                "you_should",
            ),
            (
                "We recommend you fly at a lower altitude during follow-up missions.",
                "we_recommend_you",
            ),
            (
                "Next time, consider increasing the overlap between passes.",
                "next_time_consider",
            ),
            (
                "Consider adjusting your approach when winds exceed 15 knots.",
                "consider_adjusting",
            ),
            (
                "The operator should have aborted the third flight earlier.",
                "operator_should_have",
            ),
            (
                "Your flight pattern shows gaps in the southern quadrant.",
                "your_flight",
            ),
            (
                "You could have captured the eastern ridge with one additional pass.",
                "you_could_have",
            ),
            (
                "We suggest you brief the spotter before night operations.",
                "we_suggest_you",
            ),
            (
                "The pilot should have descended to 200 ft AGL for the inspection.",
                "pilot_should_have",
            ),
        ],
    )
    def test_flags_known_operator_coaching_phrases(
        self, bad_text: str, expected_rule: str
    ) -> None:
        leaks = detect_audience_leaks(bad_text)
        assert leaks, f"detector failed to flag: {bad_text!r}"
        assert any(leak.rule == expected_rule for leak in leaks), (
            f"expected rule {expected_rule!r}, got {[leak.rule for leak in leaks]}"
        )
        assert has_audience_leak(bad_text) is True

    def test_passes_clean_third_person_client_report(self) -> None:
        clean_report = (
            "## Mission Overview\n"
            "On 2026-05-12, the operator conducted a roof-inspection survey of the "
            "client's commercial property at 1400 Industrial Way. Two flights were "
            "completed using a DJI Mavic 3 Enterprise.\n\n"
            "## Area Coverage\n"
            "The aircraft covered approximately 2.3 acres across both flights, with "
            "full coverage of the north and west roof sections.\n\n"
            "## Flight Operations Summary\n"
            "Total flight time: 18 minutes. Total distance: 1.42 miles. "
            "Maximum altitude: 280 ft AGL.\n\n"
            "## Key Findings\n"
            "Three areas of visible membrane separation were identified along the "
            "north parapet. High-resolution imagery has been provided with the "
            "report deliverables.\n\n"
            "## Client Follow-Up Items\n"
            "The marked areas in the imagery warrant on-the-ground inspection by "
            "a licensed roofing contractor.\n"
        )
        leaks = detect_audience_leaks(clean_report)
        assert leaks == [], f"clean report falsely flagged: {leaks}"
        assert has_audience_leak(clean_report) is False

    def test_empty_input_is_clean(self) -> None:
        assert detect_audience_leaks("") == []
        assert has_audience_leak("") is False

    def test_leak_record_carries_diagnostic_snippet(self) -> None:
        bad = "Section 5: You should descend to 150 ft on similar missions."
        leaks = detect_audience_leaks(bad)
        assert leaks
        first = leaks[0]
        assert isinstance(first, AudienceLeak)
        assert "you should" in first.snippet.lower()
        assert 0 <= first.start < first.end <= len(bad)

    def test_real_world_leak_pattern_from_incident(self) -> None:
        """Verbatim shape of the leak the operator reported on 2026-05-14."""
        leak_shape = (
            "## Recommendations\n"
            "We recommend you adjust your flight pattern to ensure better "
            "coverage of the southern quadrant. Next time, consider lowering "
            "your altitude to 250 ft for higher-resolution imagery. You should "
            "also brief the visual observer before takeoff.\n"
        )
        leaks = detect_audience_leaks(leak_shape)
        rules_hit = {leak.rule for leak in leaks}
        # At least three distinct categories should fire on this paragraph.
        assert len(rules_hit) >= 3, (
            f"expected >=3 rule categories on incident-shape leak, "
            f"got {rules_hit}"
        )


# ───────────────── ADR-0029: altitude-limit / Part-107 guard ─────────────


class TestAltitudeLimitGuard:
    """The prompt must forbid altitude-limit commentary; the detector is the
    deterministic second line that catches any that slips through."""

    @pytest.fixture
    def rendered(self) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(company_name="DroneOps")

    def test_prompt_forbids_altitude_limit_commentary(self, rendered: str) -> None:
        low = rendered.lower()
        # The reversed ADR-0028 H1 instruction must be GONE.
        assert "note factually that it exceeds 400 ft" not in low
        assert "the fact of exceedance" not in low
        # The explicit prohibition must be present.
        assert "not a compliance audit" in low
        assert "must not mention, compare against, flag, or comment on any altitude limit" in low
        assert "the 400 ft agl ceiling" in low
        assert "must never state or imply that any flight exceeded" in low
        # The single permitted positive framing is preserved.
        assert "in accordance with faa part 107 procedures" in low

    def test_flags_real_world_savannah_exceedance_sentence(self) -> None:
        """The verbatim damaging language Bill reported. Must be caught."""
        offending = (
            "A number of flights operated above 400 ft AGL — specifically "
            "Flights 2,4,5,6,7,8,9,11,15,19,20,21,22,23,26 — and as such "
            "exceeded the standard Part 107 400 ft AGL altitude limit."
        )
        leaks = detect_audience_leaks(offending)
        assert leaks, "detector failed to flag the savannah exceedance sentence"
        assert has_audience_leak(offending) is True

    @pytest.mark.parametrize(
        "bad_text, expected_rule",
        [
            ("Three flights operated above 400 ft AGL.", "altitude_over_400ft"),
            ("Flight 4 exceeded 400 feet AGL.", "altitude_over_400ft"),
            ("This exceeds the 400 ft AGL Part 107 limit.", "altitude_over_400ft"),
            ("The flight breached the 400 ft AGL altitude limit.", "altitude_limit_phrase"),
            ("Several flights went beyond the 400-foot ceiling.", "ft_ceiling"),
            ("This altitude exceeds the Part 107 restriction.", "altitude_exceeds_limit"),
            ("Operations stayed under the Part 107 400 ft ceiling.", "part_107_altitude_limit"),
        ],
    )
    def test_flags_altitude_limit_phrasings(
        self, bad_text: str, expected_rule: str
    ) -> None:
        leaks = detect_audience_leaks(bad_text)
        assert leaks, f"detector failed to flag: {bad_text!r}"
        assert any(leak.rule == expected_rule for leak in leaks), (
            f"expected rule {expected_rule!r}, got {[leak.rule for leak in leaks]}"
        )

    @pytest.mark.parametrize(
        "clean_text",
        [
            "Maximum altitude: 280 ft AGL.",
            "The aircraft reached 190.8 m AGL (626 ft) on the final pass.",
            "Altitude across the survey ranged from 95 ft to 412 ft AGL.",
            "Operations were conducted in accordance with FAA Part 107 procedures.",
            "DroneOps is an FAA Part 107 certified drone operations company.",
        ],
    )
    def test_neutral_altitude_and_positive_framing_pass(self, clean_text: str) -> None:
        leaks = detect_audience_leaks(clean_text)
        assert leaks == [], f"neutral/positive text falsely flagged: {leaks}"
