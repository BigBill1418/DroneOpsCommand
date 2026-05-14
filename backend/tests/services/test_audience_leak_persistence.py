"""ADR-0015 runtime gate — persistence-side coverage for the audience-leak
detector wired into ``generate_report_task``.

The detector itself is exercised by ``test_report_audience_guard.py``.
This module covers the *wire-in*: that the celery-task helper
``_apply_audience_findings`` populates the two new columns on the
``Report`` row correctly, that it never raises, and that the flags
serialize through ``ReportResponse`` so the frontend editorial gate
can read them.

Approach: hermetic. No LLM call, no DB, no celery broker. The helper is
written so it accepts any object that exposes the two flag attributes,
so ``SimpleNamespace`` is a faithful stand-in for the ORM row. This
mirrors the ``test_device_key_rotation.py`` style already in the repo.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.schemas.report import AudienceLeakDetail, ReportResponse
from app.services.report_audience import detect_audience_leaks
from app.tasks.celery_tasks import _apply_audience_findings


# ────────────────────────────── Helpers ─────────────────────────────────


def _fresh_report() -> SimpleNamespace:
    """Return a minimal Report-shaped stub with the two flag attrs unset.

    The task body sets `llm_generated_content`/`final_content` BEFORE the
    helper runs, so the stub mirrors that ordering (the helper only
    touches the two new fields).
    """
    return SimpleNamespace(
        mission_id="11111111-1111-1111-1111-111111111111",
        llm_generated_content=None,
        final_content=None,
        has_audience_leak=None,
        audience_leak_details=None,
    )


# ────────────────────────────── Wire-in tests ───────────────────────────


class TestApplyAudienceFindings:
    """``_apply_audience_findings`` is the post-LLM persistence hook."""

    def test_clean_report_flags_clean(self) -> None:
        report = _fresh_report()
        clean = (
            "## Mission Overview\n"
            "On 2026-05-12 the operator conducted a roof-inspection survey "
            "of the client's commercial property. Two flights were completed.\n\n"
            "## Key Findings\n"
            "Three areas of visible membrane separation were identified "
            "along the north parapet.\n"
        )

        _apply_audience_findings(report, clean)

        assert report.has_audience_leak is False
        assert report.audience_leak_details == []

    def test_leaky_report_flags_dirty_with_full_match_records(self) -> None:
        report = _fresh_report()
        # Verbatim shape from the 2026-05-14 operator-caught incident —
        # multiple rule categories should fire on this one paragraph.
        leaky = (
            "## Recommendations\n"
            "We recommend you adjust your flight pattern to ensure better "
            "coverage of the southern quadrant. Next time, consider lowering "
            "your altitude to 250 ft for higher-resolution imagery. You should "
            "also brief the visual observer before takeoff.\n"
        )

        _apply_audience_findings(report, leaky)

        assert report.has_audience_leak is True
        assert isinstance(report.audience_leak_details, list)
        assert len(report.audience_leak_details) >= 3

        # Each record carries the four fields the frontend banner needs.
        for record in report.audience_leak_details:
            assert set(record.keys()) == {"rule", "snippet", "start", "end"}
            assert isinstance(record["rule"], str) and record["rule"]
            assert isinstance(record["snippet"], str) and record["snippet"]
            assert 0 <= record["start"] < record["end"] <= len(leaky)

        rules_hit = {r["rule"] for r in report.audience_leak_details}
        # At minimum the three highest-signal rules from the incident.
        assert {"we_recommend_you", "next_time_consider", "you_should"} <= rules_hit

    def test_specific_rule_fires_for_single_known_phrase(self) -> None:
        """Smoke check that one rule maps to one persisted record shape."""
        report = _fresh_report()
        bad = "You should descend to 150 ft on similar future missions."

        _apply_audience_findings(report, bad)

        assert report.has_audience_leak is True
        rules = {r["rule"] for r in report.audience_leak_details}
        # `you_should` is the lowest-id rule that matches; the regex set
        # is precision-tuned so the noise floor here should be tight.
        assert "you_should" in rules

    def test_empty_input_flags_clean(self) -> None:
        report = _fresh_report()
        _apply_audience_findings(report, "")
        assert report.has_audience_leak is False
        assert report.audience_leak_details == []

    def test_none_input_flags_clean(self) -> None:
        """Defense against an upstream provider returning None somehow."""
        report = _fresh_report()
        _apply_audience_findings(report, None)  # type: ignore[arg-type]
        assert report.has_audience_leak is False
        assert report.audience_leak_details == []

    def test_detector_failure_does_not_raise(self, caplog) -> None:
        """ADR-0015 contract: detector failure must NEVER block save.

        The helper has to swallow exceptions; otherwise a regex regression
        500s every report generation. Force a failure by patching the
        detector to raise, then assert the helper still returns and the
        report ends up in the clean default state.
        """
        report = _fresh_report()
        with patch(
            "app.services.report_audience.detect_audience_leaks",
            side_effect=RuntimeError("forced detector failure"),
        ):
            _apply_audience_findings(report, "anything")

        assert report.has_audience_leak is False
        assert report.audience_leak_details == []

    def test_persistence_records_match_detector_output_one_to_one(self) -> None:
        """The helper must not drop, dedupe, or reorder detector matches.

        If a future "smart" aggregation lands here, the frontend banner
        loses fidelity — every match should round-trip into the JSONB.
        """
        report = _fresh_report()
        text = (
            "You should consult the safety briefing. "
            "We recommend you also review the pre-flight checklist."
        )
        direct = detect_audience_leaks(text)

        _apply_audience_findings(report, text)

        assert len(report.audience_leak_details) == len(direct)
        assert [r["rule"] for r in report.audience_leak_details] == [
            leak.rule for leak in direct
        ]


# ────────────────────────────── Schema round-trip ───────────────────────


class TestReportResponseSerialization:
    """The two new fields must survive the Pydantic response model."""

    def test_clean_report_serializes_with_default_false_and_empty_list(self) -> None:
        report = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            mission_id="00000000-0000-0000-0000-000000000002",
            user_narrative=None,
            llm_generated_content=None,
            final_content=None,
            ground_covered_acres=None,
            flight_duration_total_seconds=None,
            flight_distance_total_meters=None,
            map_image_path=None,
            pdf_path=None,
            include_download_link=False,
            generated_at=None,
            sent_at=None,
            has_audience_leak=False,
            audience_leak_details=[],
        )

        resp = ReportResponse.model_validate(report)

        assert resp.has_audience_leak is False
        assert resp.audience_leak_details == []

    def test_leaky_report_serializes_with_typed_detail_records(self) -> None:
        details = [
            {"rule": "you_should", "snippet": "You should descend", "start": 0, "end": 11},
            {"rule": "we_recommend_you", "snippet": "We recommend you brief", "start": 50, "end": 66},
        ]
        report = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000003",
            mission_id="00000000-0000-0000-0000-000000000004",
            user_narrative=None,
            llm_generated_content="dummy",
            final_content="dummy",
            ground_covered_acres=None,
            flight_duration_total_seconds=None,
            flight_distance_total_meters=None,
            map_image_path=None,
            pdf_path=None,
            include_download_link=False,
            generated_at=None,
            sent_at=None,
            has_audience_leak=True,
            audience_leak_details=details,
        )

        resp = ReportResponse.model_validate(report)

        assert resp.has_audience_leak is True
        assert len(resp.audience_leak_details) == 2
        assert all(isinstance(d, AudienceLeakDetail) for d in resp.audience_leak_details)
        assert resp.audience_leak_details[0].rule == "you_should"
        assert resp.audience_leak_details[1].rule == "we_recommend_you"


# ────────────────────────────── Sanity ─────────────────────────────────


def test_no_regen_loop_contract_is_documented() -> None:
    """Smoke check that the helper module-doc commits to the soft-block
    contract (no regen loop). If a future edit deletes this language,
    we want a loud signal in CI rather than silent drift to a retry
    pattern that burns API credits.
    """
    from app.tasks import celery_tasks

    src = celery_tasks._apply_audience_findings.__doc__ or ""
    assert "soft-block" in src.lower() or "ADR-0015" in src
    assert "NEVER raises" in src
