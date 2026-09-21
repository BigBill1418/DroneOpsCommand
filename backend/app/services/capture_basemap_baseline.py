"""Regenerate the tile-health baseline fixture (ADR-0046).

    cd backend && python -m app.services.capture_basemap_baseline

Run this ONLY after opening the live tiles and confirming with your own eyes
that they are clean. The whole probe rests on the baseline being a picture of a
good tile; capturing a watermarked one bakes the defect in as "normal", which
is exactly the failure this subsystem exists to prevent.

Refuses to write a baseline where any layer is non-200 or blank.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import httpx

from app.services.basemap_probe import BASELINE_PATH, fetch_fingerprint
from app.services.basemap_registry import LAYERS, PROBE_X, PROBE_Y, PROBE_Z
from app.version import USER_AGENT


def main() -> int:
    client = httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
    layers: dict[str, dict] = {}

    for layer in LAYERS:
        fp = fetch_fingerprint(layer, client)
        if fp.status != 200 or fp.error:
            print(f"REFUSING: {layer.id} returned status={fp.status} error={fp.error}", file=sys.stderr)
            return 1
        if fp.blank:
            print(f"REFUSING: {layer.id} probe tile has no image content", file=sys.stderr)
            return 1
        layers[layer.id] = {
            "url": fp.url,
            "bytes": fp.bytes,
            "content_type": fp.content_type,
            "width": fp.width,
            "height": fp.height,
            "ahash": fp.ahash,
            "dhash": fp.dhash,
        }
        print(f"{layer.id:26s} {fp.bytes:6d}B {fp.content_type:11s} "
              f"{fp.width}x{fp.height} a={fp.ahash} d={fp.dhash}")

    payload = {
        "_comment": (
            "Baseline tile fingerprints for the ADR-0046 tile-health probe. "
            "Regenerate with: python -m app.services.capture_basemap_baseline "
            "— but only after visually confirming the live tiles are clean."
        ),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "probe_tile": {"z": PROBE_Z, "x": PROBE_X, "y": PROBE_Y},
        "layers": layers,
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
