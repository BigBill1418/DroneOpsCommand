"""Device-silence watchdog selection (2026-10-06).

M4P (Mavic 4 Pro, RC2 uploader) went silent 2026-09-19. The watchdog only
looked at keys used within the last 7 days, so after 2026-09-26 it stopped
reporting M4P at all — a long outage went quiet exactly when it mattered.
(Its alerts before that were also dropped — ADR-0051.) An active key now
stays reported for as long as it is silent; retired devices are expected to
have their key deactivated.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.tasks.celery_tasks import select_silent_device_keys

NOW = datetime(2026, 10, 6, 19, 0)


def key(label, hours_ago, active=True):
    last = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    return SimpleNamespace(label=label, is_active=active, last_used_at=last)


def labels(rows, **kw):
    return [r.label for r in select_silent_device_keys(rows, NOW, silence_hours=48, **kw)]


def test_long_silent_active_key_is_still_reported():
    # 17 days silent — the M4P case; previously dropped after 7 days.
    assert labels([key("M4P", 17 * 24)]) == ["M4P"]


def test_recent_key_is_not_reported():
    assert labels([key("M30T", 16)]) == []


def test_inactive_and_never_used_keys_are_ignored():
    assert labels([key("M3P", 3400, active=False), key("Phone", None)]) == []


def test_optional_window_still_caps_when_configured():
    rows = [key("M4P", 17 * 24), key("M4TD", 60)]
    assert labels(rows, activity_window_days=7) == ["M4TD"]
    assert labels(rows, activity_window_days=0) == ["M4P", "M4TD"]
