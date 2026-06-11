"""Audit finding P1-4 (2026-06-11 ground-up audit) — CPU-bound work offloaded.

Two handlers did heavy PIL/PDF work synchronously inside async routes,
freezing the single uvicorn worker's event loop per request:

  1. ``POST /api/settings/branding/logo`` — PIL decode/resize/encode inline.
  2. ``GET  /api/intake/{customer_id}/signed-tos`` — PIL decode + reportlab
     canvas + pypdf merge inline.

Both now mirror the ``run_in_executor`` pattern in missions.py/aircraft.py.
These tests exercise the routes through the full FastAPI ASGI stack
(``TestClient`` + ``app.dependency_overrides``) with a fake session — no
Postgres/Redis pulled in — and assert the CPU-bound helpers actually run
OFF the event loop thread (``asyncio.get_running_loop()`` raises
RuntimeError inside a default-executor worker thread; it would succeed if
the helper were still called inline from the coroutine).
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage


# ── Fakes mimicking AsyncSession ─────────────────────────────────────


class _ScalarOneOrNone:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Async session double — returns queued values from ``execute``."""

    def __init__(self, execute_results: list[Any]):
        self._queue = list(execute_results)
        self.added: list = []
        self.committed = 0

    async def execute(self, _stmt):
        if not self._queue:
            return _ScalarOneOrNone(None)
        return _ScalarOneOrNone(self._queue.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):  # pragma: no cover - unused on these paths
        pass

    async def commit(self):
        self.committed += 1

    async def rollback(self):  # pragma: no cover
        pass

    async def close(self):  # pragma: no cover
        pass


def _assert_off_event_loop():
    """Raise if called on a thread with a running event loop.

    Inside ``loop.run_in_executor`` worker threads there is no running
    loop → ``get_running_loop`` raises RuntimeError → we pass. If a
    handler regresses to calling the helper inline, the coroutine's
    thread DOES have a running loop and this trips.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise AssertionError("CPU-bound helper executed ON the event loop thread")


def _png_bytes(width: int = 600, height: int = 200) -> bytes:
    """A real decodable PNG (PIL must succeed so the resize path runs)."""
    img = PILImage.new("RGBA", (width, height), (0, 212, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Logo upload (system_settings.py) ─────────────────────────────────


def _build_settings_app(tmp_path) -> tuple[FastAPI, _FakeSession]:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers import system_settings as ss_mod

    app = FastAPI()
    app.include_router(ss_mod.router)

    # No existing company_logo row → route inserts a new SystemSetting.
    fake_db = _FakeSession([None])

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


def test_logo_upload_resize_runs_in_executor(tmp_path, monkeypatch):
    """PIL resize + disk write must happen off the event loop thread."""
    from app.routers import system_settings as ss_mod

    monkeypatch.setattr(
        ss_mod, "app_settings", SimpleNamespace(upload_dir=str(tmp_path))
    )

    resize_calls: list = []
    real_resize = ss_mod._resize_logo

    def _spy_resize(content: bytes, ext: str) -> bytes:
        _assert_off_event_loop()
        resize_calls.append(ext)
        return real_resize(content, ext)

    write_calls: list = []
    real_write = ss_mod._write_file

    def _spy_write(path: str, content: bytes) -> None:
        _assert_off_event_loop()
        write_calls.append(path)
        real_write(path, content)

    monkeypatch.setattr(ss_mod, "_resize_logo", _spy_resize)
    monkeypatch.setattr(ss_mod, "_write_file", _spy_write)

    app, fake_db = _build_settings_app(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/settings/branding/logo",
        files={"file": ("logo.png", _png_bytes(), "image/png")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Response shape unchanged: {"company_logo": "branding/logo_<uuid>.png"}
    assert set(body.keys()) == {"company_logo"}
    assert body["company_logo"].startswith("branding/logo_")
    assert body["company_logo"].endswith(".png")

    assert resize_calls == [".png"], "resize helper did not run exactly once"
    assert len(write_calls) == 1, "write helper did not run exactly once"

    # File landed on disk, resized to <= 400px wide
    saved = os.path.join(str(tmp_path), body["company_logo"])
    assert os.path.exists(saved)
    with PILImage.open(saved) as img:
        assert img.width <= 400

    # DB row inserted + committed (transactional behavior unchanged)
    assert len(fake_db.added) == 1
    assert fake_db.committed == 1


def test_logo_upload_svg_skips_pil_entirely(tmp_path, monkeypatch):
    """SVG logos bypass PIL (previous behavior) — helper must NOT be called."""
    from app.routers import system_settings as ss_mod

    monkeypatch.setattr(
        ss_mod, "app_settings", SimpleNamespace(upload_dir=str(tmp_path))
    )

    def _boom(_content, _ext):  # pragma: no cover - failure path
        raise AssertionError("_resize_logo must not run for SVG uploads")

    monkeypatch.setattr(ss_mod, "_resize_logo", _boom)

    app, _fake_db = _build_settings_app(tmp_path)
    client = TestClient(app)

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    resp = client.post(
        "/api/settings/branding/logo",
        files={"file": ("logo.svg", svg, "image/svg+xml")},
    )

    assert resp.status_code == 200, resp.text
    saved = os.path.join(str(tmp_path), resp.json()["company_logo"])
    with open(saved, "rb") as f:
        assert f.read() == svg  # stored verbatim


def test_logo_upload_oversize_rejected_before_processing(tmp_path, monkeypatch):
    """5 MB cap unchanged — 413 fires before any PIL/disk work."""
    from app.routers import system_settings as ss_mod

    monkeypatch.setattr(
        ss_mod, "app_settings", SimpleNamespace(upload_dir=str(tmp_path))
    )

    def _boom(_content, _ext):  # pragma: no cover - failure path
        raise AssertionError("_resize_logo must not run for rejected uploads")

    monkeypatch.setattr(ss_mod, "_resize_logo", _boom)

    app, _fake_db = _build_settings_app(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/settings/branding/logo",
        files={"file": ("big.png", b"\x00" * 5_000_001, "image/png")},
    )
    assert resp.status_code == 413, resp.text


# ── Signed TOS render (intake.py) ────────────────────────────────────


class _CustomerStub:
    """Quacks like a ``Customer`` row for the signed-TOS path."""

    def __init__(self, **overrides: Any) -> None:
        self.id = uuid.uuid4()
        self.name = "Test Customer"
        self.email = "customer@test.example.com"
        self.tos_signed = True
        self.tos_signed_at = datetime(2026, 6, 11, 14, 30)
        self.tos_pdf_path = None
        # data-URL PNG signature, like SignaturePad produces
        self.signature_data = "data:image/png;base64," + base64.b64encode(
            _png_bytes(120, 40)
        ).decode()
        for key, value in overrides.items():
            setattr(self, key, value)


def _build_intake_app(customer) -> FastAPI:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.intake import router as intake_router

    app = FastAPI()
    app.include_router(intake_router)

    fake_db = _FakeSession([customer])

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app


def _write_minimal_pdf(path: str) -> None:
    """One-page PDF via reportlab (same lib the route depends on)."""
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(path, pagesize=(612, 792))
    c.drawString(72, 720, "Terms of Service — test fixture")
    c.save()


def test_signed_tos_render_runs_in_executor(tmp_path, monkeypatch):
    """PDF render/merge must happen off the event loop thread; output is
    a valid PDF with the same response headers as before."""
    from app.routers import intake as intake_mod

    tos_dir = tmp_path / "tos"
    tos_dir.mkdir()
    _write_minimal_pdf(str(tos_dir / "default_tos.pdf"))
    monkeypatch.setattr(
        intake_mod, "settings", SimpleNamespace(upload_dir=str(tmp_path))
    )

    render_calls: list = []
    real_render = intake_mod._render_signed_tos

    def _spy_render(tos_path, signature_data, signer_label, signed_at_dt):
        _assert_off_event_loop()
        render_calls.append(signer_label)
        return real_render(tos_path, signature_data, signer_label, signed_at_dt)

    monkeypatch.setattr(intake_mod, "_render_signed_tos", _spy_render)

    customer = _CustomerStub()
    app = _build_intake_app(customer)
    client = TestClient(app)

    resp = client.get(f"/api/intake/{customer.id}/signed-tos")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "TOS_Signed_Test_Customer.pdf" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF"), "response is not a PDF"
    assert render_calls == ["Test Customer"], "render helper did not run exactly once"


def test_signed_tos_404_when_unsigned(tmp_path, monkeypatch):
    """Guard ordering unchanged: unsigned customer 404s before any render."""
    from app.routers import intake as intake_mod

    monkeypatch.setattr(
        intake_mod, "settings", SimpleNamespace(upload_dir=str(tmp_path))
    )

    def _boom(*_args):  # pragma: no cover - failure path
        raise AssertionError("_render_signed_tos must not run for unsigned customers")

    monkeypatch.setattr(intake_mod, "_render_signed_tos", _boom)

    customer = _CustomerStub(tos_signed=False, signature_data=None)
    app = _build_intake_app(customer)
    client = TestClient(app)

    resp = client.get(f"/api/intake/{customer.id}/signed-tos")
    assert resp.status_code == 404, resp.text
    assert "not signed" in resp.json()["detail"]
