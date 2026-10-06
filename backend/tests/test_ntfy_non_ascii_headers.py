"""ntfy headers must survive non-ASCII titles (em dash, accents).

2026-10-06: arming the basemap probe (ROADMAP MP-2) showed every alert whose
title held a non-ASCII character was DROPPED — httpx encodes header values as
ASCII and raised, on primary and fallback alike. The probe's own title is
"Basemap tiles changed — …". Non-ASCII header values are now sent as RFC 2047
encoded-words, which ntfy decodes.
"""
import base64

from app.services.ntfy import _build_headers


def _decode(v: str) -> str:
    assert v.startswith("=?UTF-8?B?") and v.endswith("?=")
    return base64.b64decode(v[len("=?UTF-8?B?"):-2]).decode("utf-8")


def test_non_ascii_title_is_ascii_encodable_and_round_trips():
    h = _build_headers(title="Basemap tiles changed — esri_imagery", priority=0, click=None,
                       tags=["warning"], publisher_token="t")
    for v in h.values():
        v.encode("ascii")  # must not raise
    assert _decode(h["Title"]).endswith("Basemap tiles changed — esri_imagery")


def test_ascii_title_is_sent_unchanged():
    h = _build_headers(title="Plain title", priority=0, click=None, tags=None, publisher_token=None)
    assert h["Title"].endswith("Plain title") and not h["Title"].startswith("=?")


def test_fallback_title_is_also_encoded():
    h = _build_headers(title="café — down", priority=1, click=None, tags=["möp"], publisher_token=None,
                       fallback=True)
    assert _decode(h["Title"]).startswith("[FALLBACK]")
    assert _decode(h["Tags"]) == "möp"
