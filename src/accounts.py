"""Real-user accounts for hosted, multi-user mode (deterministic, no model calls).

An account is a login handle plus the two things needed to authenticate a user
and unlock their encrypted records:

  - auth_hash : a PBKDF2 hash (auth.py) for the login check.
  - keyset    : the envelope-wrapped data key (crypto.py). Unwrapping it with the
                password yields the AES key for that user's store.

Both are safe to store in the clear: the hash is one-way, and the keyset holds
only the *wrapped* data key, never the key itself. Accounts live in an
unencrypted namespace on the same backend as the records (accounts/<user_id>.json)
so they are readable before the user has proven anything; the records themselves
live under a per-user prefix and are encrypted (see app.store_for). The host can
therefore never read a user's records without that user's password.

A per-day usage counter backs the cost cap, since every extraction spends the
operator's API budget. Isolation is by construction: a user_id names exactly one
account file and one record prefix, and no code path enumerates across users.

Concurrency note: create() is a read-then-write, so two simultaneous signups of
the same brand-new username could race. Acceptable at this scale (a handful of
trusted users); a hosted lock is future work.
"""

from __future__ import annotations

import datetime
import json
import re

from src import auth, crypto

_MIN_PASSWORD = 8
_USER_RE = re.compile(r"[^a-z0-9_.-]+")
_USAGE_KEEP_DAYS = 35


class AccountError(RuntimeError):
    """A friendly, user-facing account problem (taken username, weak password)."""


def normalize_username(username: str) -> str:
    """A stable id/handle: lowercase, keep only [a-z0-9_.-]. May be empty."""
    return _USER_RE.sub("", (username or "").strip().lower())


class AccountStore:
    """Reads and writes account records over an (unencrypted) StorageBackend."""

    def __init__(self, backend):
        self.backend = backend

    def _key(self, user_id: str) -> str:
        return f"accounts/{user_id}.json"

    def exists(self, user_id: str) -> bool:
        return self.backend.exists(self._key(user_id))

    def _load(self, user_id: str) -> dict | None:
        try:
            raw = self.backend.get(self._key(user_id))
        except KeyError:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _save(self, acct: dict) -> None:
        data = json.dumps(acct, indent=2, ensure_ascii=False).encode("utf-8")
        self.backend.put(self._key(acct["user_id"]), data)

    def create(self, username: str, name: str, password: str) -> dict:
        """Create a new account. Raises AccountError on a bad or taken username
        or a too-short password."""
        uid = normalize_username(username)
        if not uid:
            raise AccountError("Pick a username using letters, digits, or _ . -")
        if len(password or "") < _MIN_PASSWORD:
            raise AccountError(f"Password must be at least {_MIN_PASSWORD} characters.")
        if self.exists(uid):
            raise AccountError("That username is already taken.")
        acct = {
            "user_id": uid,
            "username": uid,
            "name": (name or "").strip() or uid,
            "auth_hash": auth.hash_password(password),
            "keyset": crypto.create_keyset(password),
            "created": datetime.date.today().isoformat(),
            "usage": {},
        }
        self._save(acct)
        return acct

    def authenticate(self, username: str, password: str):
        """Return (account, data_key) on success, else None. The password both
        passes the login hash and unwraps the encryption key."""
        acct = self._load(normalize_username(username))
        if not acct or not auth.verify_password(password or "", acct.get("auth_hash", "")):
            return None
        try:
            data_key = crypto.open_keyset(password, acct.get("keyset") or {})
        except crypto.CryptoError:
            return None
        return acct, data_key

    # --- usage / cost cap --------------------------------------------------

    def _count_today(self, user_id: str, field: str) -> int:
        acct = self._load(user_id) or {}
        return int((acct.get(field) or {}).get(datetime.date.today().isoformat(), 0))

    def _record(self, user_id: str, field: str, n: int) -> int:
        acct = self._load(user_id)
        if not acct:
            return 0
        today = datetime.date.today().isoformat()
        counts = dict(acct.get(field) or {})
        counts[today] = int(counts.get(today, 0)) + n
        cutoff = (datetime.date.today() - datetime.timedelta(days=_USAGE_KEEP_DAYS)).isoformat()
        acct[field] = {d: v for d, v in counts.items() if d >= cutoff}
        self._save(acct)
        return acct[field][today]

    # Extractions (document reads) and chat questions are metered separately, each
    # spending the operator's API budget.
    def usage_today(self, user_id: str) -> int:
        return self._count_today(user_id, "usage")

    def record_usage(self, user_id: str, n: int = 1) -> int:
        return self._record(user_id, "usage", n)

    def chats_today(self, user_id: str) -> int:
        return self._count_today(user_id, "chat_usage")

    def record_chat(self, user_id: str, n: int = 1) -> int:
        return self._record(user_id, "chat_usage", n)
