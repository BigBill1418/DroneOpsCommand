"""Audience-leak detector for generated mission reports.

The mission-report generator produces a CLIENT-facing artifact. It must NOT
contain operator-coaching language (second-person address to the pilot,
flight-technique critique, etc.). This module provides a deterministic,
regex-based detector to catch leaks — primarily as a regression guard in
tests, but exposed as a callable so it can be wired into a post-generation
runtime gate later without re-implementing the rules.

See: backend/tests/services/test_report_audience_guard.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Patterns that indicate the generator addressed the operator instead of the
# client, or injected pilot-coaching content. Each pattern is paired with a
# short rule code so test failures point straight at the offending category.
#
# Patterns are intentionally case-insensitive and tolerant of small
# variations (e.g., "you should" / "you should have"). They are tuned for
# precision over recall — false positives on a client-facing report are
# worse than false negatives, because they would block legitimate output.
# The system-prompt guardrails in claude_llm.py / ollama.py are the primary
# defense; this detector is the second layer.
_LEAK_RULES: tuple[tuple[str, str], ...] = (
    # Direct second-person coaching addressed to the pilot.
    ("you_should", r"\byou\s+should\b"),
    ("you_could_have", r"\byou\s+(?:could|should|might|may)\s+have\b"),
    ("your_flight", r"\byour\s+(?:flight|mission|pattern|altitude|approach|technique|drone|aircraft)\b"),
    # First-person-plural advice directed at the pilot ("we recommend you...").
    ("we_recommend_you", r"\bwe\s+recommend\s+(?:that\s+)?you\b"),
    ("we_suggest_you", r"\bwe\s+(?:suggest|advise)\s+(?:that\s+)?you\b"),
    # Coaching framing aimed at the pilot's future flights.
    ("next_time_consider", r"\bnext\s+time(?:,)?\s+(?:consider|try|you)\b"),
    ("consider_adjusting", r"\bconsider\s+adjusting\s+your\b"),
    # Self-critique that should never appear in a client artifact.
    ("operator_should_have", r"\bthe\s+operator\s+should\s+have\b"),
    ("pilot_should_have", r"\b(?:the\s+)?pilot\s+should\s+have\b"),
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (code, re.compile(pattern, re.IGNORECASE)) for code, pattern in _LEAK_RULES
)


@dataclass(frozen=True)
class AudienceLeak:
    """A single detected audience-leak match within a generated report."""

    rule: str
    snippet: str
    start: int
    end: int


def detect_audience_leaks(report_text: str) -> list[AudienceLeak]:
    """Return all audience-leak matches in ``report_text``.

    An empty list means the report passes the audience guard. The detector
    is intentionally conservative — see module docstring.
    """
    if not report_text:
        return []
    leaks: list[AudienceLeak] = []
    for rule, pattern in _COMPILED:
        for match in pattern.finditer(report_text):
            # Pull a ~60-char window around the hit for diagnostic output.
            window_start = max(0, match.start() - 30)
            window_end = min(len(report_text), match.end() + 30)
            leaks.append(
                AudienceLeak(
                    rule=rule,
                    snippet=report_text[window_start:window_end].strip(),
                    start=match.start(),
                    end=match.end(),
                )
            )
    return leaks


def has_audience_leak(report_text: str) -> bool:
    """Convenience wrapper. True if any audience leak is detected."""
    return bool(detect_audience_leaks(report_text))
