"""Tests for backup/restore. tmp_path stores only; the encrypted path needs
the `cryptography` package (skipped if absent)."""

import io
import zipfile

import pytest

from src import backup
from src.store import Store


def _seed(root, rid="rec_bk0001"):
    s = Store(str(root))
    original = root / "orig.png"
    original.write_bytes(b"\x89PNG-original-bytes")
    s.save({"record_id": rid, "episode_id": None, "document_type": "prescription",
            "medications": [], "flags": []}, original_path=str(original))
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
