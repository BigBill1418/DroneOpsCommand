"""Tests for the GlitchTip/Sentry before_send noise filter.

Guards the OTel-export and event-loop-closed noise tail (droneops issues
1789/2201 + the event-loop-closed cluster): transport/shutdown hum, not
actionable errors, dropped at the SDK level so collector blips don't become
GlitchTip issues. Real errors must still pass through to PII sanitation.
"""

from __future__ import annotations

from app.observability import sentry as s


def test_otel_export_failure_is_noise():
    event = {
        "exception": {
            "values": [
                {"type": "Exception", "value": "Failed to export traces to alloy.barnardhq.com:4317, StatusCode.UNAVAILABLE"}
            ]
        }
    }
    assert s._is_noise_event(event, None) is True
    assert s._before_send(event, None) is None


def test_event_loop_closed_is_noise():
    event = {"exception": {"values": [{"type": "RuntimeError", "value": "Event loop is closed"}]}}
    assert s._is_noise_event(event, None) is True
    assert s._before_send(event, None) is None


def test_noise_detected_via_hint_exc_info():
    err = RuntimeError("Event loop is closed")
    assert s._is_noise_event({}, {"exc_info": (RuntimeError, err, None)}) is True


def test_real_error_passes_through():
    event = {"exception": {"values": [{"type": "ValueError", "value": "mission 7 has no flights"}]}}
    assert s._is_noise_event(event, None) is False
    # _before_send delegates to the PII sanitizer (which returns a dict), not None
    assert s._before_send(event, None) is not None
