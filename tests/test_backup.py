"""Tests for backup/restore. tmp_path stores only; the encrypted path needs
the `cryptography` package (skipped if absent)."""

import io
import zipfile

import pytest

from src import backup, crypto
from src.storage import InMemoryBackend
from src.store import Store


def _seed(root, rid="rec_bk0001"):
    s = Store(str(root))
    original = root / "orig.png"
    original.write_bytes(b"\x89PNG-original-bytes")
    s.save({"record_id": rid, "episode_id": None, "document_type": "prescription",
            "medications": [], "flags": []}, original_path=str(original))
    return s


def _seed_encrypted(rid="rec_enc001", key=None):
    """An encrypted store (InMemoryBackend + cipher) seeded with one record + original."""
    s = Store(backend=InMemoryBackend(), cipher=crypto.Cipher(key or crypto.new_data_key()))
    s.save({"record_id": rid, "episode_id": None, "document_type": "prescription",
            "medications": [], "flags": []})
    s.save_original(rid, b"\x89PNG-original-bytes", ".png")
    return s


def test_plain_backup_round_trip(tmp_path):
    src = _seed(tmp_path / "src")
    blob = backup.make_backup(src)
    assert not backup.is_encrypted(blob)

    dst = Store(str(tmp_path / "dst"))
    res = backup.restore_backup(dst, blob)
    assert res == {"added": 1, "updated": 0, "records": 1}
    assert dst.exists("rec_bk0001")
    with open(dst.original_path("rec_bk0001"), "rb") as f:
        assert f.read() == b"\x89PNG-original-bytes"


def test_restore_merges_and_counts_updates(tmp_path):
    src = _seed(tmp_path / "src")
    blob = backup.make_backup(src)
    dst = _seed(tmp_path / "dst")           # already has the same id
    res = backup.restore_backup(dst, blob)
    assert res == {"added": 0, "updated": 1, "records": 1}


def test_restore_rejects_zip_slip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.json", "{}")
    dst = Store(str(tmp_path / "dst"))
    with pytest.raises(backup.BackupError):
        backup.restore_backup(dst, buf.getvalue())


def test_restore_rejects_non_backup(tmp_path):
    dst = Store(str(tmp_path / "dst"))
    with pytest.raises(backup.BackupError):
        backup.restore_backup(dst, b"not a zip at all")


def test_encrypted_round_trip(tmp_path):
    pytest.importorskip("cryptography")
    src = _seed(tmp_path / "src")
    blob = backup.make_backup(src, passphrase="hunter2")
    assert backup.is_encrypted(blob)

    dst = Store(str(tmp_path / "dst"))
    # No passphrase -> refused; wrong passphrase -> refused.
    with pytest.raises(backup.BackupError):
        backup.restore_backup(dst, blob)
    with pytest.raises(backup.BackupError):
        backup.restore_backup(dst, blob, passphrase="wrong")

    res = backup.restore_backup(dst, blob, passphrase="hunter2")
    assert res["records"] == 1 and dst.exists("rec_bk0001")


def test_encrypted_store_backup_holds_plaintext_and_restores():
    src = _seed_encrypted("rec_enc001")
    blob = backup.make_backup(src)                 # store decrypts on the way in
    assert not backup.is_encrypted(blob)

    # The ZIP payload is plaintext (protect the file itself with a passphrase).
    zf = zipfile.ZipFile(io.BytesIO(blob))
    assert b"rec_enc001" in zf.read("records/rec_enc001.json")
    assert zf.read("originals/rec_enc001.png") == b"\x89PNG-original-bytes"

    # Restore into a DIFFERENT encrypted store (its own key): must round-trip.
    dst = Store(backend=InMemoryBackend(), cipher=crypto.Cipher(crypto.new_data_key()))
    res = backup.restore_backup(dst, blob)
    assert res["records"] == 1 and dst.exists("rec_enc001")
    data, ext = dst.original_bytes("rec_enc001")
    assert data == b"\x89PNG-original-bytes" and ext == ".png"
    # The destination holds ciphertext at rest, not the plaintext record.
    assert b"rec_enc001" not in dst.backend.get("records/rec_enc001.json")


def test_encrypted_store_passphrase_backup_round_trip():
    src = _seed_encrypted("rec_enc002")
    blob = backup.make_backup(src, passphrase="pw-123456")
    assert backup.is_encrypted(blob)
    dst = Store(backend=InMemoryBackend(), cipher=crypto.Cipher(crypto.new_data_key()))
    res = backup.restore_backup(dst, blob, passphrase="pw-123456")
    assert res["records"] == 1 and dst.exists("rec_enc002")
