"""Audience / compliance leak detector for generated mission reports.

The mission-report generator produces a CLIENT-facing artifact. It must NOT
contain:

1. Operator-coaching language — second-person address to the pilot,
   flight-technique critique, etc. (the original audience-leak class).
2. Altitude-limit / regulatory-exceedance commentary — ADR-0029. Mission
   reports are client deliverables, not compliance audits. The narrative
   must NEVER announce, flag, list, or comment on whether any flight
   exceeded an altitude limit, the 400 ft AGL ceiling, or any Part-107 /
   regulatory limit. Whether an altitude was permissible is the certificated
   operator's determination, not something editorialized to the client.

This module provides a deterministic, regex-based detector to catch both
classes — used as a regression guard in tests AND wired into the
post-generation runtime gate (``celery_tasks._apply_audience_findings``),
which sets ``Report.has_audience_leak`` so the editorial gate surfaces a
warning banner before the report is delivered.

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

# ADR-0029 — altitude-limit / Part-107 exceedance commentary. A client report
# must never editorialize about whether a flight exceeded an altitude limit, the
# 400 ft AGL ceiling, or any Part-107 / regulatory limit. These patterns are
# tuned to fire on EXCEEDANCE / LIMIT framing only — they deliberately do NOT
# match neutral altitude data ("190.8 m AGL (626 ft)", "Maximum altitude: 280 ft
# AGL") nor the single permitted positive framing ("conducted in accordance with
# FAA Part 107 procedures").
_ALTITUDE_LIMIT_RULES: tuple[tuple[str, str], ...] = (
    # "above 400 ft", "over 400 ft", "exceeded 400 ft", "exceeds the 400-foot..."
    ("altitude_over_400ft",
     r"\b(?:over|above|exceed(?:s|ed|ing)?|beyond)\s+(?:the\s+)?(?:standard\s+)?(?:part[\s-]?107\s+)?400[\s-]*(?:ft|feet|foot)\b"),
    # "exceeded ... 400 ft / altitude limit / Part 107" (exceedance verb first).
    ("altitude_exceeds_limit",
     r"\bexceed(?:s|ed|ing)?\b[^.\n]{0,60}\b(?:400[\s-]*(?:ft|feet|foot)|altitude\s+(?:limit|ceiling|restriction)|part[\s-]?107)\b"),
    # The bare loaded phrase: "altitude limit", "altitude ceiling", "altitude restriction".
    ("altitude_limit_phrase",
     r"\baltitude\s+(?:limit|ceiling|restriction)\b"),
    # A regulatory "ceiling" tied to 400 ft (either order).
    ("ft_ceiling",
     r"\b(?:400[\s-]*(?:ft|feet|foot)\b[^.\n]{0,25}\bceiling\b|ceiling\b[^.\n]{0,25}\b400[\s-]*(?:ft|feet|foot))"),
    # "Part 107 ... 400 ft / altitude limit / ceiling / exceed" — Part-107 framing
    # tied to an altitude restriction. Does not match "Part 107 procedures".
    ("part_107_altitude_limit",
     r"\bpart[\s-]?107\b[^.\n]{0,80}\b(?:exceed(?:s|ed|ing)?|400[\s-]*(?:ft|feet|foot)|altitude\s+(?:limit|ceiling|restriction)|ceiling)\b"),
)

_LEAK_RULES = _LEAK_RULES + _ALTITUDE_LIMIT_RULES

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
