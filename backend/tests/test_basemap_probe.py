"""Tile-health probe: hashing, grading, and the alert shaping (ADR-0046).

The probe exists because of one specific failure: CARTO began serving
watermarked basemap tiles with HTTP 200, the right content-type, the right
CORS headers and a plausible byte size. So the load-bearing test in this file
is not "the hash function is stable" — it is
``test_watermark_stamped_into_a_tile_is_detected``, which reproduces that exact
shape and asserts the probe calls it out. Everything else supports it.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image, ImageDraw

from app.services import basemap_probe as probe
from app.services.basemap_probe import (
    TileFingerprint,
    alert_payload,
    average_hash,
    difference_hash,
    evaluate,
    fingerprint_image,
    hamming,
    load_baseline,
    ntfy_enabled,
    run_probe,
)
from app.services.basemap_registry import LAYERS, LAYERS_BY_ID, probe_url


# ── Fixtures: synthetic tiles ──────────────────────────────────────────


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _map_like_tile() -> Image.Image:
    """A deterministic stand-in for a dark basemap tile: dark ground, a few
    light roads. Structured enough that both hashes carry real information."""
    img = Image.new("RGB", (256, 256), (26, 31, 46))
    draw = ImageDraw.Draw(img)
    for x in range(0, 256, 48):
        draw.line([(x, 0), (x + 80, 255)], fill=(200, 160, 140), width=5)
    for y in range(0, 256, 64):
        draw.line([(0, y), (255, y)], fill=(170, 140, 125), width=4)
    draw.rectangle([90, 40, 200, 120], fill=(12, 14, 20))
    return img


def _watermarked(image: Image.Image) -> Image.Image:
    """The CARTO failure shape: text stamped diagonally into the pixels."""
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for offset in range(-256, 512, 56):
        draw.text((offset, 24), "API KEY REQUIRED", fill=(255, 255, 255))
        draw.line([(offset, 0), (offset + 256, 255)], fill=(235, 235, 235), width=2)
    return out


def _fingerprint_of(image: Image.Image, layer: str = "test_layer") -> TileFingerprint:
    return fingerprint_image(layer, "https://tiles.test/1/2/3", 200, "image/png", _png(image))


def _baseline_of(image: Image.Image) -> dict:
    fp = _fingerprint_of(image)
    return {"bytes": fp.bytes, "width": fp.width, "height": fp.height,
            "ahash": fp.ahash, "dhash": fp.dhash}


# ── Hashing ────────────────────────────────────────────────────────────


def test_hashes_are_64_bit_hex_and_deterministic():
    tile = _map_like_tile()
    for fn in (average_hash, difference_hash):
        first, second = fn(tile), fn(tile)
        assert first == second
        assert len(first) == 16
        int(first, 16)  # parses as hex


def test_hashes_survive_recompression_of_the_same_image():
    """A provider re-encoding a tile must not read as a content change."""
    tile = _map_like_tile()
    jpeg = io.BytesIO()
    tile.save(jpeg, "JPEG", quality=82)
    with Image.open(io.BytesIO(jpeg.getvalue())) as reencoded:
        assert hamming(average_hash(tile), average_hash(reencoded)) <= probe.HAMMING_MAX
        assert hamming(difference_hash(tile), difference_hash(reencoded)) <= probe.HAMMING_MAX


def test_transparent_overlay_is_flattened_not_discarded():
    """RGBA -> L would hash whatever sits under transparent pixels. Two
    overlays with identical RGB but different alpha must hash differently."""
    visible = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(visible).rectangle([0, 0, 255, 127], fill=(255, 255, 255, 255))
    hidden = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(hidden).rectangle([0, 0, 255, 127], fill=(255, 255, 255, 0))
    assert average_hash(visible) != average_hash(hidden)
    assert average_hash(hidden) == "0000000000000000"


def test_hamming_reports_max_distance_for_unparseable_hashes():
    assert hamming("ffffffffffffffff", "0000000000000000") == 64
    assert hamming("", "0000000000000000") == 64
    assert hamming("not-hex", "0000000000000000") == 64
    assert hamming("00000000000000ff", "00000000000000f0") == 4


# ── The defect this exists to catch ────────────────────────────────────


def test_watermark_stamped_into_a_tile_is_detected():
    """HTTP 200, right content-type, right dimensions, wrong pixels.

    This is the CARTO failure verbatim. A status check, a `tileerror` handler,
    an uptime monitor and a tile cache all report green here.
    """
    clean = _map_like_tile()
    baseline = _baseline_of(clean)

    verdict = evaluate(_fingerprint_of(_watermarked(clean)), baseline)

    assert verdict["verdict"] == "changed"
    assert max(verdict["ahash_distance"], verdict["dhash_distance"]) > probe.HAMMING_MAX
    assert any("perceptual hash" in r for r in verdict["reasons"])


def test_the_same_clean_tile_reads_ok_against_its_own_baseline():
    """The negative control: without this, the test above passes on a probe
    that calls everything changed."""
    clean = _map_like_tile()
    assert evaluate(_fingerprint_of(clean), _baseline_of(clean))["verdict"] == "ok"


def test_blank_filler_is_caught_even_when_its_hash_distance_is_tiny():
    """The low-entropy trap, and why `blank` is checked before the hashes.

    A sparse transparent overlay (a label layer over dark ground) hashes to
    mostly zeros. When such a layer runs out of data and the provider returns
    its blank filler tile, the Hamming distance from baseline can be ~3 — well
    inside the threshold — so a hash-only probe would call an empty tile
    healthy. Measured against the live `esri_boundaries_places` baseline on
    2026-09-21: distance 3.
    """
    sparse = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(sparse).rectangle([120, 8, 132, 20], fill=(255, 255, 255, 255))
    baseline = _baseline_of(sparse)

    blank = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    blank_fp = _fingerprint_of(blank)

    # The hash alone would have waved it through...
    assert hamming(blank_fp.ahash, baseline["ahash"]) <= probe.HAMMING_MAX
    # ...but the blankness check runs first.
    assert evaluate(blank_fp, baseline)["verdict"] == "blank"


# ── Grading ────────────────────────────────────────────────────────────


def _fp(**over) -> TileFingerprint:
    base = dict(layer="l", url="u", status=200, bytes=1000, content_type="image/png",
                width=256, height=256, ahash="0f0f0f0f0f0f0f0f", dhash="1122334455667788")
    base.update(over)
    return TileFingerprint(**base)


_MATCHING_BASELINE = {"bytes": 1000, "width": 256, "height": 256,
                      "ahash": "0f0f0f0f0f0f0f0f", "dhash": "1122334455667788"}


def test_verdict_ok_when_everything_matches():
    assert evaluate(_fp(), _MATCHING_BASELINE)["verdict"] == "ok"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_non_200_is_unreachable(status):
    verdict = evaluate(_fp(status=status), _MATCHING_BASELINE)
    assert verdict["verdict"] == "unreachable"
    assert f"HTTP {status}" in verdict["reasons"][0]


def test_connection_failure_is_unreachable():
    result = evaluate(_fp(status=0, bytes=0, error="fetch failed: ConnectError"), _MATCHING_BASELINE)
    assert result["verdict"] == "unreachable"


def test_a_200_carrying_html_is_undecodable_not_ok():
    """A provider that starts returning an error page with a 200 must not be
    mistaken for a healthy tile."""
    fp = fingerprint_image("l", "u", 200, "text/html", b"<html>API key required</html>")
    assert fp.error is not None
    assert evaluate(fp, _MATCHING_BASELINE)["verdict"] == "undecodable"


def test_byte_size_outside_the_band_is_changed():
    assert evaluate(_fp(bytes=300), _MATCHING_BASELINE)["verdict"] == "changed"
    assert evaluate(_fp(bytes=5000), _MATCHING_BASELINE)["verdict"] == "changed"
    # ...and inside it is not.
    assert evaluate(_fp(bytes=1300), _MATCHING_BASELINE)["verdict"] == "ok"


def test_changed_dimensions_are_reported():
    verdict = evaluate(_fp(width=512, height=512), _MATCHING_BASELINE)
    assert verdict["verdict"] == "changed"
    assert any("dimensions" in r for r in verdict["reasons"])


def test_missing_baseline_is_its_own_verdict():
    """"No baseline" is not "healthy" — a new layer must not read as green."""
    assert evaluate(_fp(), None)["verdict"] == "no_baseline"


# ── Shipped baseline fixture ───────────────────────────────────────────


def test_shipped_baseline_covers_every_registry_layer():
    baseline = load_baseline()
    assert baseline, "baseline fixture is missing from the image"
    assert baseline["captured_at"]
    assert set(baseline["layers"]) == {layer.id for layer in LAYERS}


def test_shipped_baseline_records_the_url_the_probe_will_request():
    """A baseline captured against a different URL than the probe fetches
    would compare two unrelated tiles forever."""
    baseline = load_baseline()
    for layer_id, entry in baseline["layers"].items():
        assert entry["url"] == probe_url(LAYERS_BY_ID[layer_id])


def test_shipped_baseline_holds_no_blank_tile():
    """A baseline captured from a blank filler bakes the defect in as normal."""
    for layer_id, entry in load_baseline()["layers"].items():
        assert (entry["ahash"], entry["dhash"]) != ("0000000000000000", "0000000000000000"), layer_id
        assert entry["bytes"] > 0
        assert (entry["width"], entry["height"]) == (256, 256)


# ── run_probe ──────────────────────────────────────────────────────────


def _fake_fetcher(by_layer: dict[str, TileFingerprint]):
    return lambda layer: by_layer[layer.id]


def _synthetic_baseline() -> tuple[dict, dict[str, TileFingerprint]]:
    """A baseline plus matching fingerprints for every registry layer."""
    clean = _map_like_tile()
    fingerprints, layers = {}, {}
    for layer in LAYERS:
        fingerprints[layer.id] = _fingerprint_of(clean, layer.id)
        layers[layer.id] = _baseline_of(clean)
    return {"captured_at": "2026-09-21T00:00:00+00:00", "layers": layers}, fingerprints


def test_run_probe_reports_all_green():
    baseline, fingerprints = _synthetic_baseline()
    result = run_probe(fetcher=_fake_fetcher(fingerprints), baseline=baseline)

    assert result["ok"] is True
    assert result["failing"] == []
    assert result["layers_ok"] == result["layers_total"] == len(LAYERS)
    assert result["baseline_captured_at"] == "2026-09-21T00:00:00+00:00"
    assert set(result["layers"]) == {layer.id for layer in LAYERS}


def test_run_probe_names_the_failing_layer_and_keeps_the_rest_green():
    baseline, fingerprints = _synthetic_baseline()
    fingerprints["esri_dark_base"] = _fingerprint_of(_watermarked(_map_like_tile()), "esri_dark_base")

    result = run_probe(fetcher=_fake_fetcher(fingerprints), baseline=baseline)

    assert result["ok"] is False
    assert result["failing"] == ["esri_dark_base"]
    assert result["layers_ok"] == len(LAYERS) - 1
    assert result["layers"]["esri_dark_base"]["verdict"] == "changed"
    assert result["layers"]["osm_standard"]["verdict"] == "ok"


def test_run_probe_result_is_json_serialisable():
    """It is persisted into system_settings.value as text."""
    baseline, fingerprints = _synthetic_baseline()
    result = run_probe(fetcher=_fake_fetcher(fingerprints), baseline=baseline)
    assert json.loads(json.dumps(result))["ok"] is True


# ── Alert gate + shaping ───────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["true", "TRUE", " yes ", "1", "on"])
def test_ntfy_gate_opens_only_on_an_explicit_affirmative(raw):
    assert ntfy_enabled(raw) is True


@pytest.mark.parametrize("raw", [None, "", "false", "0", "no", "off", "maybe", "  "])
def test_ntfy_gate_is_closed_by_default(raw):
    assert ntfy_enabled(raw) is False


def _failing_result(failing: list[str]) -> dict:
    return {
        "layers_ok": 5 - len(failing), "layers_total": 5, "failing": failing,
        "baseline_captured_at": "2026-09-21T00:00:00+00:00",
        "layers": {lid: {"verdict": "changed", "reasons": ["perceptual hash moved"]} for lid in failing},
    }


def test_alert_dedup_key_is_the_failing_set_not_the_run():
    """A key containing anything run-unique would make the 24h cooldown a
    decoration — every run would be a fresh alert."""
    _, _, first = alert_payload(_failing_result(["esri_dark_base"]))
    _, _, second = alert_payload(_failing_result(["esri_dark_base"]))
    assert first == second

    _, _, wider = alert_payload(_failing_result(["esri_dark_base", "osm_standard"]))
    assert wider != first


def test_alert_names_every_failing_layer_and_its_reason():
    title, message, _ = alert_payload(_failing_result(["esri_dark_base", "osm_standard"]))
    assert "esri_dark_base" in title and "osm_standard" in title
    assert "perceptual hash moved" in message
    assert "3/5" in message


# ── Scheduling ─────────────────────────────────────────────────────────


def test_the_probe_is_actually_scheduled():
    """A probe nobody runs is a probe that reports nothing. Pins the beat
    entry, the task name it dispatches, and that the name is registered."""
    from app.tasks.celery_tasks import celery_app

    entry = celery_app.conf.beat_schedule["basemap-tile-health"]
    assert entry["task"] == "probe_basemap_tiles"
    assert "probe_basemap_tiles" in celery_app.tasks

    # Weekly, and outside the ADR-0037 quiet window (22:00-07:00 Pacific)
    # once the UTC schedule is converted — 15:47 UTC is ~08:47 PDT.
    schedule = entry["schedule"]
    assert schedule.day_of_week == {1}
    assert schedule.hour == {15}
    assert schedule.minute == {47}
