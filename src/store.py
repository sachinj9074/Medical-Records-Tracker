"""Local record and original-image persistence (gitignored).

Persists structured records plus their original uploaded files under a store
root. The original is the source of truth; the structured JSON is a fallible
convenience layer stored alongside it. A Store is bound to a root directory, so
the same code serves the real store (local_records/store/, gitignored) and the
demo store (demo_cache/, synthetic only) by pointing at different roots.

Layout inside a root:
    <root>/records/<record_id>.json       the structured record
    <root>/originals/<record_id>.<ext>    the retained original

Pure persistence: no model calls. Writes are atomic (temp file + rename), and
save() is an upsert, so timeline.py can re-save a record after assigning an
episode_id. The original is located by record_id convention, so no field is
added to the record.

See PROJECT_SPEC.md sections 3, 7, 15.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(_REPO, "local_records", "store")


class StoreError(RuntimeError):
    """A record could not be saved, loaded, or located."""


class Store:
    def __init__(self, root: str | None = None):
        self.root = root or DEFAULT_ROOT
        self.records_dir = os.path.join(self.root, "records")
        self.originals_dir = os.path.join(self.root, "originals")
        os.makedirs(self.records_dir, exist_ok=True)
        os.makedirs(self.originals_dir, exist_ok=True)

    def _record_path(self, record_id: str) -> str:
        return os.path.join(self.records_dir, f"{record_id}.json")

    def exists(self, record_id: str) -> bool:
        return os.path.exists(self._record_path(record_id))

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
        self._atomic_write_json(self._record_path(rid), record)
        return record

    def load(self, record_id: str) -> dict:
        path = self._record_path(record_id)
        if not os.path.exists(path):
            raise StoreError(f"no record: {record_id}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def list(self) -> list[dict]:
        """Every stored record. A corrupt file is skipped, not fatal."""
        out = []
        for p in sorted(glob.glob(os.path.join(self.records_dir, "*.json"))):
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def original_path(self, record_id: str) -> str | None:
        matches = sorted(glob.glob(os.path.join(self.originals_dir, f"{record_id}.*")))
        return matches[0] if matches else None

    # --- internals ---------------------------------------------------------

    def _retain_original(self, record_id: str, original_path: str) -> None:
        if not os.path.exists(original_path):
            raise StoreError(f"original not found: {original_path}")
        ext = os.path.splitext(original_path)[1].lower()
        dest = os.path.join(self.originals_dir, f"{record_id}{ext}")
        if os.path.abspath(original_path) == os.path.abspath(dest):
            return  # already the retained copy; nothing to do
        shutil.copy2(original_path, dest)

    def _atomic_write_json(self, path: str, obj: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)  # atomic on the same filesystem
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
