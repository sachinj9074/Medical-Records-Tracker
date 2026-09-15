"""Record and original-image persistence (deterministic, no model calls).

Persists structured records plus their original uploaded files. The original is
the source of truth; the structured JSON is a fallible convenience layer stored
alongside it. Store is a thin records-and-originals layer over two pluggable
pieces (see storage.py and crypto.py):

  - a StorageBackend (local files, or Cloudflare R2 for the hosted deploy), and
  - an optional Cipher: when present, every record and original is encrypted
    before it reaches the backend, so a hosted store holds only ciphertext.

Logical keys are stable regardless of backend or encryption:
    records/<record_id>.json       the structured record
    originals/<record_id>.<ext>    the retained original

Backward compatible: Store(root) still builds a local, unencrypted store with
the same on-disk layout as before, so existing local and demo archives read
unchanged. Writes are atomic, and save() is an upsert. The original is located
by record_id convention, so no field is added to the record.

See PROJECT_SPEC.md sections 3, 7, 15.
"""

from __future__ import annotations

import json
import os

from src.storage import LocalBackend

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(_REPO, "local_records", "store")


class StoreError(RuntimeError):
    """A record could not be saved, loaded, or located."""


class Store:
    def __init__(self, root: str | None = None, *, backend=None, cipher=None):
        if backend is None:
            backend = LocalBackend(root or DEFAULT_ROOT)
        self.backend = backend
        self.cipher = cipher
        # A local, unencrypted store keeps its concrete directories exposed, so
        # filesystem-level callers (backup.py) keep working unchanged. Other
        # backends have no such paths.
        if isinstance(backend, LocalBackend) and cipher is None:
            self.root = backend.root
            self.records_dir = os.path.join(backend.root, "records")
            self.originals_dir = os.path.join(backend.root, "originals")
            os.makedirs(self.records_dir, exist_ok=True)
            os.makedirs(self.originals_dir, exist_ok=True)
        else:
            self.root = getattr(backend, "root", None)
            self.records_dir = None
            self.originals_dir = None

    # --- keys & codec ------------------------------------------------------

    @staticmethod
    def _record_key(record_id: str) -> str:
        return f"records/{record_id}.json"

    def _encode(self, data: bytes) -> bytes:
        return self.cipher.encrypt(data) if self.cipher else data

    def _decode(self, data: bytes) -> bytes:
        return self.cipher.decrypt(data) if self.cipher else data

    def _original_key(self, record_id: str) -> str | None:
        want = f"{record_id}."
        for key in self.backend.list("originals/"):
            if key.split("/")[-1].startswith(want):
                return key
        return None

    # --- records -----------------------------------------------------------

    def exists(self, record_id: str) -> bool:
        return self.backend.exists(self._record_key(record_id))

    def save(self, record: dict, original_path: str | None = None) -> dict:
        """Upsert a record. If original_path is given, retain a copy of it.

        On re-save (for example after episode_id is assigned) pass no original;
        the already-retained one is left in place.
        """
        rid = record.get("record_id")
        if not rid:
            raise StoreError("record has no record_id")
        if original_path:
            self._retain_original(rid, original_path)
        data = json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8")
        self.backend.put(self._record_key(rid), self._encode(data))
        return record

    def load(self, record_id: str) -> dict:
        try:
            raw = self.backend.get(self._record_key(record_id))
        except KeyError:
            raise StoreError(f"no record: {record_id}")
        return json.loads(self._decode(raw).decode("utf-8"))

    def list(self) -> list[dict]:
        """Every stored record. A corrupt or undecryptable file is skipped."""
        out = []
        for key in self.backend.list("records/"):
            if not key.endswith(".json"):
                continue
            try:
                out.append(json.loads(self._decode(self.backend.get(key)).decode("utf-8")))
            except Exception:
                continue
        return out

    # --- originals ---------------------------------------------------------

    def original_bytes(self, record_id: str):
        """The original as (bytes, extension), decrypted, or None. Backend-agnostic."""
        key = self._original_key(record_id)
        if key is None:
            return None
        return self._decode(self.backend.get(key)), os.path.splitext(key)[1].lower()

    def save_original(self, record_id: str, data: bytes, ext: str) -> None:
        """Store an original's raw bytes directly (encrypting when a cipher is set).

        Used on restore, where the bytes come from a backup rather than a file on
        disk. Complements _retain_original, which copies from a path."""
        ext = ext.lower()
        if ext and not ext.startswith("."):
            ext = "." + ext
        self.backend.put(f"originals/{record_id}{ext}", self._encode(data))

    def original_path(self, record_id: str) -> str | None:
        """A real filesystem path to the original, for a local unencrypted store
        only (where the bytes on disk are the original itself). None otherwise:
        callers that must work for any backend use original_bytes()."""
        if self.records_dir is None:  # non-local or encrypted: no plaintext path
            return None
        key = self._original_key(record_id)
        return os.path.join(self.root, *key.split("/")) if key else None

    def delete(self, record_id: str) -> bool:
        """Remove a record and its retained original(s). Returns True if a record
        was removed. Idempotent: missing keys are ignored."""
        removed = False
        rk = self._record_key(record_id)
        if self.backend.exists(rk):
            self.backend.delete(rk)
            removed = True
        want = f"{record_id}."
        for key in list(self.backend.list("originals/")):
            if key.split("/")[-1].startswith(want):
                self.backend.delete(key)
        return removed

    # --- internals ---------------------------------------------------------

    def _retain_original(self, record_id: str, original_path: str) -> None:
        if not os.path.exists(original_path):
            raise StoreError(f"original not found: {original_path}")
        ext = os.path.splitext(original_path)[1].lower()
        dest_key = f"originals/{record_id}{ext}"
        # A local unencrypted store can be handed the file it already holds
        # (re-save); skip rewriting it onto itself.
        if self.records_dir is not None:
            dest_path = os.path.join(self.root, *dest_key.split("/"))
            if os.path.abspath(original_path) == os.path.abspath(dest_path):
                return
        with open(original_path, "rb") as f:
            self.backend.put(dest_key, self._encode(f.read()))
