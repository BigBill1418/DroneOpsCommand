"""Audit 2026-06-11 P3-5c + FU-8 #5b — ``_get_stored_file_path`` glob lookup
and removal of the dead ``_save_original_file`` helper.

P3-5c replaced the O(N) ``_FLIGHT_LOGS_DIR.iterdir()`` scan (which listed
every stored flight log to find one hash) with a filesystem glob on the hash
stem. These tests prove the glob resolves the SAME file the old
``f.stem == file_hash`` predicate would have, for every name the original-file
store can produce: ``{hash}.txt`` (DJI logs), other single suffixes, the
``.bin`` fallback, and the extensionless ``{hash}`` form — while NOT matching a
different hash that merely shares a prefix.

FU-8 #5b: ``_save_original_file`` (the old whole-file-in-RAM ``write_bytes``
persister, superseded by the streaming ``_store_original_from_path``) had zero
callers and is gone — asserted here so it can't quietly return.
"""

from __future__ import annotations

import app.routers.flight_library as fl


_HASH = "a" * 64  # sha256 hex shape
_OTHER = "b" * 64


def test_get_stored_file_path_finds_txt(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path)
    target = tmp_path / f"{_HASH}.txt"
    target.write_bytes(b"log")
    assert fl._get_stored_file_path(_HASH) == target


def test_get_stored_file_path_finds_bin_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path)
    target = tmp_path / f"{_HASH}.bin"
    target.write_bytes(b"log")
    assert fl._get_stored_file_path(_HASH) == target


def test_get_stored_file_path_finds_arbitrary_suffix(monkeypatch, tmp_path):
    # The store writes whatever the upload's suffix was — not a fixed set.
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path)
    target = tmp_path / f"{_HASH}.dat"
    target.write_bytes(b"log")
    assert fl._get_stored_file_path(_HASH) == target


def test_get_stored_file_path_finds_extensionless(monkeypatch, tmp_path):
    # Old predicate matched f.stem == hash, which includes a bare, suffixless
    # file. The glob path checks that form explicitly.
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path)
    target = tmp_path / _HASH
    target.write_bytes(b"log")
    assert fl._get_stored_file_path(_HASH) == target


def test_get_stored_file_path_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path)
    assert fl._get_stored_file_path(_HASH) is None


def test_get_stored_file_path_no_dir_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path / "does-not-exist")
    assert fl._get_stored_file_path(_HASH) is None


def test_get_stored_file_path_does_not_match_other_hash(monkeypatch, tmp_path):
    # A different hash sharing no prefix must not be returned.
    monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_path)
    (tmp_path / f"{_OTHER}.txt").write_bytes(b"log")
    assert fl._get_stored_file_path(_HASH) is None


def test_save_original_file_removed():
    # FU-8 #5b: the dead helper must stay gone.
    assert not hasattr(fl, "_save_original_file")
