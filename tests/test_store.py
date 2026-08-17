"""Tests for local record + original persistence.

Uses a tmp_path root, never the real store. Records here are minimal synthetic
dicts; store.py does not validate schema, so they need not be complete records.
"""

import pytest

from src.store import Store, StoreError


def rec(rid="rec_abc123", episode_id=None):
    return {"record_id": rid, "episode_id": episode_id, "document_type": "prescription",
            "medications": [], "flags": []}


def test_save_and_load_round_trip(tmp_path):
    s = Store(str(tmp_path))
    s.save(rec())
    assert s.load("rec_abc123")["record_id"] == "rec_abc123"


def test_save_without_record_id_raises(tmp_path):
    s = Store(str(tmp_path))
    with pytest.raises(StoreError):
        s.save({"document_type": "other"})


def test_load_missing_raises(tmp_path):
    s = Store(str(tmp_path))
    with pytest.raises(StoreError):
        s.load("nope")


def test_original_retained_and_locatable(tmp_path):
    original = tmp_path / "scan.png"
    original.write_bytes(b"\x89PNG fake image bytes")
    s = Store(str(tmp_path / "store"))
    s.save(rec(), original_path=str(original))

    op = s.original_path("rec_abc123")
    assert op is not None
    import os
    assert os.path.basename(op) == "rec_abc123.png"
    with open(op, "rb") as f:
        assert f.read() == b"\x89PNG fake image bytes"


def test_original_path_none_when_absent(tmp_path):
    s = Store(str(tmp_path))
    s.save(rec())
    assert s.original_path("rec_abc123") is None


def test_list_returns_all(tmp_path):
    s = Store(str(tmp_path))
    s.save(rec("rec_1"))
    s.save(rec("rec_2"))
    ids = {r["record_id"] for r in s.list()}
    assert ids == {"rec_1", "rec_2"}


def test_upsert_overwrites_and_keeps_single(tmp_path):
    s = Store(str(tmp_path))
    s.save(rec("rec_x", episode_id=None))
    s.save(rec("rec_x", episode_id="ep_1"))  # re-save after clustering
    assert s.load("rec_x")["episode_id"] == "ep_1"
    assert len(s.list()) == 1


def test_missing_original_raises(tmp_path):
    s = Store(str(tmp_path))
    with pytest.raises(StoreError):
        s.save(rec(), original_path=str(tmp_path / "does_not_exist.png"))
