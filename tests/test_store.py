"""Tests for local record + original persistence.

Uses a tmp_path root, never the real store. Records here are minimal synthetic
dicts; store.py does not validate schema, so they need not be complete records.
"""

import pytest

from src import crypto
from src.storage import InMemoryBackend
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


def test_delete_removes_record_and_original(tmp_path):
    original = tmp_path / "scan.png"
    original.write_bytes(b"img")
    s = Store(str(tmp_path / "store"))
    s.save(rec(), original_path=str(original))
    assert s.exists("rec_abc123") and s.original_path("rec_abc123") is not None

    assert s.delete("rec_abc123") is True
    assert not s.exists("rec_abc123")
    assert s.original_path("rec_abc123") is None
    assert s.delete("rec_abc123") is False  # idempotent


# --- pluggable backend + encryption -----------------------------------------

def test_store_over_injected_backend():
    s = Store(backend=InMemoryBackend())
    s.save(rec("rec_1"))
    s.save(rec("rec_2"))
    assert s.load("rec_1")["record_id"] == "rec_1"
    assert {r["record_id"] for r in s.list()} == {"rec_1", "rec_2"}
    assert s.delete("rec_1") is True and len(s.list()) == 1


def test_encrypted_store_round_trip_and_ciphertext_at_rest(tmp_path):
    backend = InMemoryBackend()
    cipher = crypto.Cipher(crypto.new_data_key())
    s = Store(backend=backend, cipher=cipher)

    original = tmp_path / "scan.png"
    original.write_bytes(b"\x89PNG the raw original")
    s.save(rec("rec_e", episode_id="ep_9"), original_path=str(original))

    # What the backend holds must be ciphertext, not the plaintext record/image.
    raw_record = backend.get("records/rec_e.json")
    assert b"rec_e" not in raw_record and b"ep_9" not in raw_record
    raw_original = backend.get("originals/rec_e.png")
    assert b"the raw original" not in raw_original

    # But Store decrypts transparently.
    assert s.load("rec_e")["episode_id"] == "ep_9"
    data, ext = s.original_bytes("rec_e")
    assert data == b"\x89PNG the raw original" and ext == ".png"
    # No filesystem path is offered for an encrypted store.
    assert s.original_path("rec_e") is None


def test_encrypted_store_skips_records_it_cannot_decrypt():
    backend = InMemoryBackend()
    good = Store(backend=backend, cipher=crypto.Cipher(crypto.new_data_key()))
    good.save(rec("rec_good"))
    # A record encrypted under a different key lands in the same backend.
    other = Store(backend=backend, cipher=crypto.Cipher(crypto.new_data_key()))
    other.save(rec("rec_other"))
    # good.list() returns only what its key can read, never raising.
    assert {r["record_id"] for r in good.list()} == {"rec_good"}
