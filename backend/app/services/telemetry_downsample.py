"""Index-stride downsampling for telemetry / flight-series read paths.

Extracted from the closure that lived inline in
``flight_library.get_telemetry`` so that ``GET /{id}/telemetry`` and
``GET /{id}/details/series`` share ONE implementation.

This is ADR-0032's standing finding applied prophylactically. That ADR's own
conclusion is that *the absence of a shared conversion layer is what lets a
defect class recur* — three unit bugs shipped because Litchi, Airdata and DJI
each re-implemented the same resolution independently. A second, copy-pasted
downsampler in the new series endpoint would be the same mistake in a
different place: a fix or an off-by-one correction in one copy would silently
not reach the other, and the two endpoints would disagree about which sample
index a chart point came from.

**Index-stride, not averaging.** Every returned value is a real recorded
sample, never a synthesised mean. Averaging would put a number on a screen
that the aircraft never reported, which ADR-0028's posture forbids. It also
makes ``select_indices`` usable as the single alignment authority: two series
downsampled with the same ``(length, max_points)`` land on the *same* source
indices, so a caller can pair a value series with its time base without
re-deriving anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def select_indices(length: int, max_points: int) -> list[int]:
    """The source indices an index-stride downsample of ``length`` keeps.

    Returns ``list(range(length))`` when no reduction is needed, so callers
    can treat the identity case uniformly.
    """
    if length <= 0:
        return []
    if max_points <= 0:
        return []
    if length <= max_points or max_points == 1:
        # max_points == 1 on a longer array would divide by zero below; the
        # honest answer is the first sample.
        return list(range(length)) if length <= max_points else [0]
    step = (length - 1) / (max_points - 1)
    return [int(i * step) for i in range(max_points)]


def downsample(arr: Sequence[T] | None, target: int) -> Sequence[T] | None:
    """Reduce ``arr`` to at most ``target`` samples by index stride.

    Falsy input and arrays already at or below ``target`` are returned
    unchanged — the historical behaviour of the inline closure this replaces,
    preserved exactly so ``/telemetry`` responses do not move.
    """
    if not arr or len(arr) <= target:
        return arr
    return [arr[i] for i in select_indices(len(arr), target)]
