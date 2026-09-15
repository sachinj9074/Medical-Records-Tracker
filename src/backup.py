"""Backup and restore of a user's local store (real mode; deterministic, no model).

A backup is a self-contained ZIP holding every record JSON, its retained original
scan, and a small manifest, so the whole archive is portable and survives the loss
of one machine. It can optionally be encrypted with a passphrase (scrypt KDF via
stdlib hashlib, AES-GCM via the `cryptography` library).

Restore MERGES a backup into a store by record_id (upsert): existing records are
kept, matching ids updated, nothing is deleted. Only validated members are
extracted (no absolute paths, no `..` traversal, no unexpected files), so a
malformed or hostile archive can never write outside the store.

Nothing here touches Streamlit, so it is fully unit-testable. The `cryptography`
import is lazy, so plain (unencrypted) backup and restore work without it.

See PROJECT_SPEC.md sections 3, 7.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import zipfile

MAGIC = b"MRTBAK1\n"        # marks an encrypted backup blob
_MANIFEST = "manifest.json"
_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


class BackupError(RuntimeError):
    """A backup could not be built, read, decrypted, or restored."""


# --- pack -------------------------------------------------------------------

def make_backup(store, *, passphrase: str | None = None) -> bytes:
    """Build a backup of everything in `store`. Encrypt it if a passphrase is given.

    Reads through the Store's public API (list + original_bytes), so it works for
    any backend: an encrypted store is decrypted on the way in, and the ZIP holds
    plaintext records and originals. Protect the file itself with a passphrase.
    """
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rec in store.list():
            rid = rec.get("record_id")
            if not rid:
                continue
            z.writestr(f"records/{rid}.json", json.dumps(rec, indent=2, ensure_ascii=False))
            n += 1
            got = store.original_bytes(rid)
            if got:
                data, ext = got
                z.writestr(f"originals/{rid}{ext}", data)
        manifest = {
            "app": "medical-records-tracker", "backup_version": 1,
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "records": n,
        }
        z.writestr(_MANIFEST, json.dumps(manifest, indent=2))
    data = buf.getvalue()
    return encrypt(data, passphrase) if passphrase else data


# --- unpack -----------------------------------------------------------------

def is_encrypted(data: bytes) -> bool:
    return data[:len(MAGIC)] == MAGIC


def _safe_member(name: str) -> bool:
    if name in (_MANIFEST,):
        return True
    if not (name.startswith("records/") or name.startswith("originals/")):
        return False
    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
        return False
    return True


def restore_backup(store, data: bytes, *, passphrase: str | None = None) -> dict:
    """Merge a backup into `store` (upsert by record_id). Returns counts."""
    if is_encrypted(data):
        if not passphrase:
            raise BackupError("this backup is encrypted; a passphrase is required")
        data = decrypt(data, passphrase)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("this file is not a valid backup")

    names = zf.namelist()
    for name in names:
        if not _safe_member(name):
            raise BackupError(f"unsafe entry in backup: {name!r}")

    added = updated = 0
    for name in names:
        if name.startswith("records/") and name.endswith(".json"):
            try:
                rec = json.loads(zf.read(name).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise BackupError(f"corrupt record in backup: {name!r}")
            rid = rec.get("record_id")
            if not rid:
                continue
            existed = store.exists(rid)
            store.save(rec)
            updated += 1 if existed else 0
            added += 0 if existed else 1

    for name in names:
        if name.startswith("originals/"):
            base = os.path.basename(name)   # "<record_id><ext>"
            rid, ext = os.path.splitext(base)
            if not rid:
                continue
            store.save_original(rid, zf.read(name), ext)

    return {"added": added, "updated": updated, "records": added + updated}


# --- encryption (passphrase -> scrypt key -> AES-GCM) -----------------------

def _key(passphrase: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, **_SCRYPT)


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise BackupError("encrypted backups require the 'cryptography' package") from e
    return AESGCM


def encrypt(data: bytes, passphrase: str) -> bytes:
    AESGCM = _aesgcm()
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(passphrase, salt)).encrypt(nonce, data, None)
    return MAGIC + salt + nonce + ct


def decrypt(blob: bytes, passphrase: str) -> bytes:
    AESGCM = _aesgcm()
    from cryptography.exceptions import InvalidTag
    body = blob[len(MAGIC):]
    salt, nonce, ct = body[:16], body[16:28], body[28:]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, None)
    except InvalidTag:
        raise BackupError("wrong passphrase, or the backup is corrupted")
