"""ADR-0043 §4.2 — the extracted downsampler behaves exactly like the closure.

``get_telemetry`` carried an inline ``downsample`` closure. The flight-series
endpoint needs the same reduction, and ADR-0032's standing conclusion is that
*the absence of a shared layer is what lets a defect class recur* — three unit
bugs shipped because three parsers re-implemented the same resolution
independently. So the closure moved into a service instead of being copied.

Moving it is only safe if the new implementation is bit-identical on the inputs
the old one accepted. ``test_matches_the_original_closure_exactly`` pins that
against a literal transcription of the pre-extraction code, over a sweep of
lengths and targets — not a hand-picked example or two.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.services.telemetry_downsample import downsample, select_indices


def _original_closure(arr, target):
    """Verbatim transcription of the closure removed from get_telemetry.

    Kept as the oracle for the parity test. Do not "improve" it — its value is
    that it is exactly what production served before the extraction.
    """
    if not arr or len(arr) <= target:
        return arr
    step = (len(arr) - 1) / (target - 1)
    return [arr[int(i * step)] for i in range(target)]


@pytest.mark.parametrize("length", [0, 1, 2, 3, 7, 100, 999, 2000, 2001, 13870])
@pytest.mark.parametrize("target", [2, 3, 10, 500, 2000, 10000])
def test_matches_the_original_closure_exactly(length, target):
    arr = list(range(length))
    assert downsample(arr, target) == _original_closure(arr, target)


def test_empty_and_none_pass_through_unchanged():
    assert downsample([], 100) == []
    assert downsample(None, 100) is None


def test_no_reduction_returns_the_same_object():
    """Short arrays must not be copied — /telemetry serves nine of these per
    request and the arrays can be thousands of points long."""
    arr = [1, 2, 3]
    assert downsample(arr, 10) is arr


def test_endpoints_are_reduced_to_at_most_target():
    for length in (13870, 5000, 2001):
        assert len(downsample(list(range(length)), 2000)) == 2000


def test_first_and_last_samples_are_always_kept():
    """A chart that silently drops the landing is worse than a coarse one."""
    for length in (2001, 5000, 13870):
        out = downsample(list(range(length)), 2000)
        assert out[0] == 0
        assert out[-1] == length - 1


def test_every_returned_value_is_a_real_sample():
    """Index stride, never averaging. An averaged point is a number the
    aircraft never reported, which ADR-0028's posture forbids putting on a
    screen."""
    arr = [x * 3.7 for x in range(5000)]
    out = downsample(arr, 250)
    assert set(out) <= set(arr)


def test_equal_length_series_get_identical_indices():
    """The alignment guarantee /details/series relies on: a value series and
    its ``t_offset_s`` are index-aligned at rest, so downsampling both with
    the same target must keep them aligned."""
    n = 13870
    values = [x * 2 for x in range(n)]
    t = [round(i / 15.0, 2) for i in range(n)]
    idx = select_indices(n, 2000)
    assert [values[i] for i in idx] == downsample(values, 2000)
    assert [t[i] for i in idx] == downsample(t, 2000)
    assert len(set(idx)) == len(idx), "stride must not repeat a source index"


def test_select_indices_is_monotonic_and_in_range():
    for n in (1, 2, 37, 2001, 13870):
        for target in (1, 2, 17, 2000):
            idx = select_indices(n, target)
            assert all(0 <= i < n for i in idx), (n, target, idx)
            assert idx == sorted(idx), (n, target)


def test_target_of_one_does_not_raise():
    """The original closure divided by ``target - 1`` and raised
    ZeroDivisionError on ``max_points=1`` — a 500 from a query string a client
    is free to send, since /telemetry declares no lower bound. The service
    returns the first sample instead. This is the ONE observable behaviour
    change in the extraction and it is a strict improvement.
    """
    with pytest.raises(ZeroDivisionError):
        _original_closure(list(range(10)), 1)
    assert downsample(list(range(10)), 1) == [0]


def test_target_of_zero_matches_the_old_empty_result():
    assert downsample(list(range(10)), 0) == _original_closure(list(range(10)), 0) == []
