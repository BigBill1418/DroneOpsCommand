"""Tile-health probe — the control that would have caught the CARTO watermark.

ADR-0046. On ~2026-08-28 CARTO began stamping "API KEY REQUIRED" into its
keyless raster basemaps. Every conventional control was blind to it: the
response was **HTTP 200**, ``content-type: image/png``,
``access-control-allow-origin: *``, correct dimensions, plausible byte size.
A status-code check, an uptime monitor, a Leaflet ``tileerror`` handler and a
tile cache would all have reported green — the cache would have served the
watermark for its whole TTL. The failure was *inside the image*, so the only
control that can see it is one that looks at the pixels. That is this module.

Design notes:

- **No new dependency.** ``imagehash`` pulls numpy + scipy + PyWavelets, none
  of which this backend has. Pillow is already pinned (``Pillow==11.0.0``, used
  by the report renderer), so the two 64-bit hashes below are implemented
  against Pillow alone in ~15 lines.
- **Two hashes, not one.** aHash (brightness vs. mean) and dHash (horizontal
  gradient) fail differently: a watermark overlay moves the gradient hash
  strongly, a colour-scheme change moves the average hash strongly.
- **Transparent overlays are flattened onto black first.** Three of the five
  registry layers are transparent PNGs; ``convert("L")`` on RGBA discards alpha
  and would hash whatever garbage sits under fully transparent pixels.
- **Thresholds here are starting points, not measurements.** Basemaps
  legitimately change when a provider refreshes data. The ntfy publish is
  therefore OFF by default (``basemap_probe_ntfy_enabled``) until the observed
  variance has been watched for two weeks — ROADMAP MP-2. A probe whose first
  data refresh pages falsely gets muted, which is worse than no probe.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import httpx
from PIL import Image

from app.services.basemap_registry import (
    LAYERS,
    PROBE_X,
    PROBE_Y,
    PROBE_Z,
    BasemapLayer,
    probe_url,
)
from app.version import USER_AGENT

logger = logging.getLogger("doc.basemap_probe")

BASELINE_PATH = Path(__file__).with_name("basemap_baseline.json")

#: Persisted last result + the ntfy gate, both in the existing
#: ``system_settings`` key/value store.
SETTING_LAST_RESULT = "basemap_probe_last_result"
SETTING_NTFY_ENABLED = "basemap_probe_ntfy_enabled"

#: Max 64-bit Hamming distance from baseline before a tile reads as changed.
#: Unvalidated — see the module docstring and ROADMAP MP-2.
HAMMING_MAX = 8

#: Byte size must stay inside baseline x (1 +/- this).
BYTE_BAND = 0.40

#: Per-tile budget. Five layers fetched sequentially, so the manual-trigger
#: endpoint's worst case is 5 x this — comfortably inside Cloudflare's ~100s
#: edge window, which a 15s budget would have flirted with.
FETCH_TIMEOUT_SECONDS = 8.0


# ── Perceptual hashing ─────────────────────────────────────────────────


def _grayscale_grid(image: Image.Image, width: int, height: int) -> list[int]:
    """Flatten onto black, desaturate, and downsample to ``width x height``."""
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        flat.alpha_composite(rgba)
        image = flat
    small = image.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    return list(small.getdata())


def _bits_to_hex(bits: Iterable[bool]) -> str:
    value = 0
    for bit in bits:
        value = (value << 1) | (1 if bit else 0)
    return f"{value:016x}"


def average_hash(image: Image.Image) -> str:
    """64-bit aHash: each of 8x8 cells brighter than the tile's mean."""
    px = _grayscale_grid(image, 8, 8)
    mean = sum(px) / len(px)
    return _bits_to_hex(p > mean for p in px)


def difference_hash(image: Image.Image) -> str:
    """64-bit dHash: each of 8x8 cells brighter than its right-hand neighbour."""
    px = _grayscale_grid(image, 9, 8)
    bits: list[bool] = []
    for row in range(8):
        base = row * 9
        bits.extend(px[base + col] > px[base + col + 1] for col in range(8))
    return _bits_to_hex(bits)


def hamming(left: str, right: str) -> int:
    """Bit distance between two hex hashes. 64 when either is unparseable."""
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except (TypeError, ValueError):
        return 64


# ── Fingerprinting ─────────────────────────────────────────────────────


@dataclass
class TileFingerprint:
    """Everything the probe records about one fetched tile."""

    layer: str
    url: str
    status: int
    bytes: int
    content_type: str
    width: int
    height: int
    ahash: str
    dhash: str
    error: str | None = None

    @property
    def blank(self) -> bool:
        """A featureless tile — every provider's out-of-data filler."""
        return self.ahash == "0000000000000000" and self.dhash == "0000000000000000"


def fingerprint_image(layer_id: str, url: str, status: int, content_type: str, raw: bytes) -> TileFingerprint:
    """Decode ``raw`` and fingerprint it. Never raises on a bad image."""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            return TileFingerprint(
                layer=layer_id,
                url=url,
                status=status,
                bytes=len(raw),
                content_type=content_type,
                width=image.width,
                height=image.height,
                ahash=average_hash(image),
                dhash=difference_hash(image),
            )
    except Exception as exc:  # undecodable body — an error page, HTML, truncated bytes
        return TileFingerprint(
            layer=layer_id, url=url, status=status, bytes=len(raw),
            content_type=content_type, width=0, height=0,
            ahash="", dhash="", error=f"decode failed: {type(exc).__name__}",
        )


def fetch_fingerprint(layer: BasemapLayer, client: httpx.Client) -> TileFingerprint:
    """Fetch this layer's fixed probe tile and fingerprint it."""
    url = probe_url(layer)
    try:
        resp = client.get(url)
    except Exception as exc:
        return TileFingerprint(
            layer=layer.id, url=url, status=0, bytes=0, content_type="",
            width=0, height=0, ahash="", dhash="",
            error=f"fetch failed: {type(exc).__name__}",
        )
    return fingerprint_image(
        layer.id, url, resp.status_code, resp.headers.get("content-type", ""), resp.content
    )


# ── Comparison ─────────────────────────────────────────────────────────


def evaluate(observed: TileFingerprint, baseline: dict | None) -> dict:
    """Grade one fingerprint against its checked-in baseline.

    Pure — no network, no clock, no DB. Returns ``{"verdict", "reasons", ...}``
    where verdict is one of ``ok`` / ``unreachable`` / ``undecodable`` /
    ``blank`` / ``changed`` / ``no_baseline``.
    """
    reasons: list[str] = []

    if observed.error and observed.status == 0:
        return {"verdict": "unreachable", "reasons": [observed.error]}
    if observed.status != 200:
        return {"verdict": "unreachable", "reasons": [f"HTTP {observed.status}"]}
    if observed.error:
        return {"verdict": "undecodable", "reasons": [observed.error]}
    if observed.blank:
        return {"verdict": "blank", "reasons": ["tile has no image content"]}
    if baseline is None:
        return {"verdict": "no_baseline", "reasons": ["no baseline recorded for this layer"]}

    d_ahash = hamming(observed.ahash, baseline.get("ahash", ""))
    d_dhash = hamming(observed.dhash, baseline.get("dhash", ""))
    if max(d_ahash, d_dhash) > HAMMING_MAX:
        reasons.append(f"perceptual hash moved (aHash {d_ahash}, dHash {d_dhash} > {HAMMING_MAX})")

    base_bytes = int(baseline.get("bytes", 0))
    if base_bytes > 0:
        low = base_bytes * (1 - BYTE_BAND)
        high = base_bytes * (1 + BYTE_BAND)
        if not low <= observed.bytes <= high:
            reasons.append(
                f"byte size {observed.bytes} outside {int(low)}-{int(high)} band (baseline {base_bytes})"
            )

    base_dims = (int(baseline.get("width", 0)), int(baseline.get("height", 0)))
    if base_dims != (0, 0) and (observed.width, observed.height) != base_dims:
        reasons.append(f"dimensions {observed.width}x{observed.height} != baseline {base_dims[0]}x{base_dims[1]}")

    return {
        "verdict": "changed" if reasons else "ok",
        "reasons": reasons,
        "ahash_distance": d_ahash,
        "dhash_distance": d_dhash,
    }


def load_baseline(path: Path | None = None) -> dict:
    """Read the checked-in baseline fixture. ``{}`` if it is missing."""
    target = path or BASELINE_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("basemap_probe.baseline_missing path=%s", target)
        return {}


# ── Probe run ──────────────────────────────────────────────────────────


def _default_fetcher() -> Callable[[BasemapLayer], TileFingerprint]:
    # `follow_redirects=False` on purpose: a provider that starts redirecting
    # tile requests has changed in a way the probe should report, not absorb.
    client = httpx.Client(
        timeout=FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
    )
    return lambda layer: fetch_fingerprint(layer, client)


def run_probe(
    fetcher: Callable[[BasemapLayer], TileFingerprint] | None = None,
    baseline: dict | None = None,
) -> dict:
    """Probe every registry layer once and grade it. Returns the full result.

    ``fetcher`` is injected by the tests; production passes ``None`` and gets a
    real httpx client carrying the identifying User-Agent the OSMF policy
    requires.
    """
    fetch = fetcher or _default_fetcher()
    base = baseline if baseline is not None else load_baseline()
    base_layers = base.get("layers", {})

    layers: dict[str, dict] = {}
    for layer in LAYERS:
        observed = fetch(layer)
        graded = evaluate(observed, base_layers.get(layer.id))
        entry = asdict(observed)
        entry.pop("layer", None)
        entry.update(graded)
        layers[layer.id] = entry

    failing = sorted(lid for lid, entry in layers.items() if entry["verdict"] != "ok")
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "probe_tile": {"z": PROBE_Z, "x": PROBE_X, "y": PROBE_Y},
        "baseline_captured_at": base.get("captured_at"),
        "ok": not failing,
        "layers_ok": len(layers) - len(failing),
        "layers_total": len(layers),
        "failing": failing,
        "layers": layers,
    }


# ── Alert shaping (pure — the DB and the transport live in the callers) ──

#: Tier-1 click target per ADR-0036: a page that actually renders a map.
ALERT_CLICK_URL = "https://droneops.barnardhq.com/telemetry"

#: ADR-0037 cooldown. A basemap provider changing its tiles is a slow-moving
#: condition, not an incident — one message a day is already generous.
ALERT_DEDUP_TTL_SECONDS = 24 * 3600


def ntfy_enabled(raw: str | None) -> bool:
    """Is the ntfy publish armed?

    Default OFF (ADR-0046 / ROADMAP MP-2): the hash and byte-band thresholds
    are unvalidated, and a probe that pages falsely on the provider's first
    data refresh gets muted — which is worse than no probe. Anything other
    than an explicit affirmative is off.
    """
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def alert_payload(result: dict) -> tuple[str, str, str]:
    """Build (title, message, dedup_key) for a failing probe run.

    The dedup key is the SET of failing layers, not the run — keying it on
    anything run-unique would make every run a fresh alert and the cooldown a
    decoration. A newly-failing layer changes the key and does alert.
    """
    failing = result.get("failing") or []
    title = f"Basemap tiles changed — {', '.join(failing)}"
    lines = [
        f"{result['layers_ok']}/{result['layers_total']} basemap layers match their baseline "
        f"(captured {result.get('baseline_captured_at') or 'unknown'}).",
        "",
    ]
    for layer_id in failing:
        entry = result["layers"].get(layer_id, {})
        lines.append(f"{layer_id}: {entry.get('verdict')} — {'; '.join(entry.get('reasons') or [])}")
    lines += [
        "",
        "Open a tile and LOOK at it before changing anything — this probe fires on "
        "pixels, and a provider data refresh looks the same as a watermark until "
        "someone checks. Registry: frontend/src/lib/basemaps.ts (ADR-0046).",
    ]
    return title, "\n".join(lines), f"basemap_probe:{','.join(failing)}"
