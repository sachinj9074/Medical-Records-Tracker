"""User identity and per-user isolation (deterministic, no model calls).

The app is single-user for real use and multi-user for the demo, and this module
is the one place that decides who a request belongs to. The safety property is
isolation by construction: a `user_id` picks a store root (see app.store_root),
and no code path ever enumerates across users, so one user can never see
another's records.

Two account sources:
  - Real mode: one local account (you). Frictionless by default; if APP_PASSWORD
    is set it must be entered. Its records live under a private, gitignored root.
  - Demo mode: a small set of seeded accounts loaded from config/demo_users.json,
    each owning a synthetic archive under demo_cache/users/<id>/. Their passwords
    are deliberately shown on the login screen (the data is fictional), so a
    reviewer can log in as one profile, then another, and see the isolation.

Passwords are stored only as PBKDF2-HMAC-SHA256 hashes and checked in constant
time. Nothing here touches Streamlit, so it is all unit-testable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_USERS_PATH = os.path.join(_ROOT, "config", "demo_users.json")

_PBKDF2_ITERATIONS = 120_000
_ALGO = "pbkdf2_sha256"


# --- password hashing -------------------------------------------------------

def hash_password(password: str, *, salt: bytes | None = None,
                  iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Return a self-describing hash string: 'pbkdf2_sha256$<iter>$<salt>$<hash>'."""
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash string."""
    if not isinstance(stored, str) or stored.count("$") != 3:
        return False
    algo, iter_s, salt_hex, hash_hex = stored.split("$")
    if algo != _ALGO:
        return False
    try:
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(got, expected)


# --- user model -------------------------------------------------------------

@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    is_demo: bool
    password_hint: str | None = None  # shown on the demo login screen only

    def public(self) -> dict:
        """The small, serialisable shape stored in the session."""
        return {"id": self.user_id, "name": self.name, "is_demo": self.is_demo}


# --- demo accounts ----------------------------------------------------------

def load_demo_users() -> list[dict]:
    """Raw demo-account records from config/demo_users.json (empty if missing)."""
    try:
        with open(DEMO_USERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def demo_users() -> list[User]:
    """The seeded demo profiles, in file order, without their password hashes."""
    out = []
    for u in load_demo_users():
        if isinstance(u, dict) and u.get("user_id") and u.get("name"):
            out.append(User(u["user_id"], u["name"], is_demo=True,
                            password_hint=u.get("password_hint")))
    return out


def _demo_record(user_id: str) -> dict | None:
    for u in load_demo_users():
        if isinstance(u, dict) and u.get("user_id") == user_id:
            return u
    return None


def authenticate_demo(user_id: str, password: str) -> User | None:
    """Return the demo User if the password matches its stored hash, else None."""
    rec = _demo_record(user_id)
    if not rec or not verify_password(password or "", rec.get("password_hash", "")):
        return None
    return User(rec["user_id"], rec["name"], is_demo=True, password_hint=rec.get("password_hint"))


# --- real (local) account ---------------------------------------------------

def real_user() -> User:
    """The single local account. Id and name are configurable via env."""
    uid = os.getenv("APP_USER", "me") or "me"
    name = os.getenv("APP_USER_NAME", "You") or "You"
    return User(uid, name, is_demo=False)


def real_password_required() -> bool:
    """True when a local password gate is configured (APP_PASSWORD set)."""
    return bool(os.getenv("APP_PASSWORD"))


def authenticate_real(password: str) -> User | None:
    """Check a typed password against APP_PASSWORD (plaintext, local only)."""
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        return real_user()  # no gate configured: open
    if hmac.compare_digest(password or "", expected):
        return real_user()
    return None
