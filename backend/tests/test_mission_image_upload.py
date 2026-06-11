"""v2.68.7 — POST /api/missions/{id}/images memory-safe upload pipeline.

Regression suite for the 2026-06-11 incident: 40–46 MB DJI stills uploaded
from the mission editor OOM-killed uvicorn (cgroup 1 GiB cap) because the
route buffered the full file in RAM (`await file.read()`) and PIL decoded
the full-resolution 48 MP image (~150 MB pixels + an EXIF-transpose copy).
Every in-flight upload died with the worker; the operator saw "all failed".

Contract under test:
  * Size cap is 60 MB (raised from 50 MB per operator requirement —
    DJI stills routinely run 40–55 MB).
  * A 55 MB upload is ACCEPTED (the old 50 MB cap rejected it).
  * A >60 MB upload is rejected 413 with a message naming the 60 MB cap.
  * Non-image content types are rejected 400 BEFORE any byte processing.
  * Real JPEGs are resized to ≤1920 px on the long edge and saved as .jpg
    under <upload_dir>/<mission_id>/.
  * Unparseable "images" fall back to a raw streamed copy (previous
    behavior preserved).

Exercised through the full FastAPI ASGI stack per ADR-0013. Large-file
tests run with Starlette's default 1 MB spool threshold, which doubles as
proof that the multipart spool limit does NOT reject big file parts (the
v2.39.3 misunderstanding that led to the 200 MB in-RAM spool).
"""

from __future__ import annotations

import io
import os
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage

from tests.test_missions_post_rejects_id_in_body import _FakeSession


def _build_app(execute_results: list) -> tuple[FastAPI, _FakeSession]:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    fake_db = _FakeSession(execute_results)

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


def _mission_stub() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


def _upload(client: TestClient, mission_id, content: bytes, *,
            filename: str = "DJI_test.JPG", content_type: str = "image/jpeg"):
    return client.post(
        f"/api/missions/{mission_id}/images",
        files={"file": (filename, io.BytesIO(content), content_type)},
        data={"caption": ""},
    )


def _real_jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), (30, 90, 160)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ── Size cap ──────────────────────────────────────────────────────────


def test_55mb_upload_accepted(tmp_path, monkeypatch):
    """55 MB is over the OLD 50 MB cap but under the new 60 MB cap.

    Random bytes are not a decodable image, so this also exercises the
    raw-copy fallback — the bytes must land on disk unmodified.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    payload = os.urandom(55 * 1024 * 1024)
    resp = _upload(client, mission.id, payload)

    assert resp.status_code == 201, f"55MB upload must pass the 60MB cap: {resp.status_code} {resp.text}"
    saved = resp.json()["file_path"]
    assert os.path.isfile(saved)
    assert os.path.getsize(saved) == len(payload), "raw-copy fallback must preserve bytes"
    assert str(mission.id) in saved, "files must be stored under the mission's own directory"


def test_over_60mb_upload_rejected_413(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    resp = _upload(client, mission.id, os.urandom(61 * 1024 * 1024))

    assert resp.status_code == 413, resp.text
    assert "60MB" in resp.json()["detail"]
    # Nothing may be written for a rejected upload.
    mission_dir = tmp_path / str(mission.id)
    leftovers = list(mission_dir.iterdir()) if mission_dir.is_dir() else []
    assert leftovers == [], f"413 must not leave files behind: {leftovers}"


# ── Content type ──────────────────────────────────────────────────────


def test_non_image_type_rejected_400(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    resp = _upload(client, mission.id, b"%PDF-1.4 not an image",
                   filename="report.pdf", content_type="application/pdf")

    assert resp.status_code == 400, resp.text


def test_unknown_mission_404(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    app, _db = _build_app([None])  # mission lookup returns nothing
    client = TestClient(app)

    resp = _upload(client, uuid.uuid4(), _real_jpeg(100, 100))

    assert resp.status_code == 404, resp.text


# ── Resize pipeline ───────────────────────────────────────────────────


def test_large_jpeg_resized_to_1920_and_saved_as_jpg(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    resp = _upload(client, mission.id, _real_jpeg(4000, 3000))

    assert resp.status_code == 201, resp.text
    saved = resp.json()["file_path"]
    assert saved.endswith(".jpg")
    with PILImage.open(saved) as img:
        assert max(img.width, img.height) <= 1920, f"long edge must be ≤1920, got {img.size}"
        assert img.width / img.height == 4 / 3 or abs(img.width / img.height - 4 / 3) < 0.01


def test_small_jpeg_not_upscaled(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    resp = _upload(client, mission.id, _real_jpeg(800, 600))

    assert resp.status_code == 201, resp.text
    with PILImage.open(resp.json()["file_path"]) as img:
        assert img.size == (800, 600)


def test_exif_orientation_applied(tmp_path, monkeypatch):
    """A portrait shot stored rotated with EXIF Orientation=6 must come out
    with width/height swapped (the operator sees it upright)."""
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    img = PILImage.new("RGB", (1600, 900), (10, 10, 10))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation: rotate 270 CW to display
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)

    resp = _upload(client, mission.id, buf.getvalue())

    assert resp.status_code == 201, resp.text
    with PILImage.open(resp.json()["file_path"]) as out:
        assert (out.width, out.height) == (900, 1600), f"orientation not applied: {out.size}"


# ── Memory discipline ─────────────────────────────────────────────────


def test_route_does_not_buffer_whole_file_via_read(tmp_path, monkeypatch):
    """The OOM root cause was `content = await file.read()` — the route
    must never materialize the full upload in one bytes object. We patch
    UploadFile.read to detect a whole-file read (no/huge size arg)."""
    from app.config import settings
    from starlette.datastructures import UploadFile as StarletteUploadFile

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    mission = _mission_stub()
    app, _db = _build_app([mission, 0])
    client = TestClient(app)

    original_read = StarletteUploadFile.read
    whole_file_reads: list[int] = []

    async def guarded_read(self, size: int = -1):
        if size is None or size < 0 or size > 8 * 1024 * 1024:
            whole_file_reads.append(size if size is not None else -1)
        return await original_read(self, size)

    monkeypatch.setattr(StarletteUploadFile, "read", guarded_read)

    resp = _upload(client, mission.id, _real_jpeg(3000, 2000))

    assert resp.status_code == 201, resp.text
    assert whole_file_reads == [], (
        "route called UploadFile.read() for the whole file — this is the "
        f"OOM regression (sizes requested: {whole_file_reads})"
    )
