"""Tests for real-user accounts (accounts.py) over an in-memory backend."""

import datetime

import pytest

from src import accounts
from src.accounts import AccountError, AccountStore, normalize_username
from src.storage import InMemoryBackend


def store():
    return AccountStore(InMemoryBackend())


def test_normalize_username():
    assert normalize_username("  Rahul Mehta! ") == "rahulmehta"
    assert normalize_username("a.b_c-d") == "a.b_c-d"
    assert normalize_username("***") == ""


def test_create_then_authenticate_returns_data_key():
    s = store()
    acct = s.create("Ananya", "Ananya Sharma", "s3cret-pass")
    assert acct["user_id"] == "ananya" and acct["name"] == "Ananya Sharma"
    res = s.authenticate("ananya", "s3cret-pass")
    assert res is not None
    got, data_key = res
    assert got["user_id"] == "ananya" and len(data_key) == 32


def test_authenticate_wrong_password_and_unknown_user():
    s = store()
    s.create("rahul", "Rahul", "correct-pass")
    assert s.authenticate("rahul", "wrong-pass") is None
    assert s.authenticate("nobody", "correct-pass") is None


def test_data_key_is_stable_across_logins():
    s = store()
    s.create("me", "Me", "my-long-pass")
    _, k1 = s.authenticate("me", "my-long-pass")
    _, k2 = s.authenticate("me", "my-long-pass")
    assert k1 == k2   # same account always unlocks the same key


def test_two_users_get_different_keys_and_are_isolated():
    s = store()
    a = s.create("alice", "Alice", "alice-pass-1")
    b = s.create("bob", "Bob", "bob-pass-12")
    _, ka = s.authenticate("alice", "alice-pass-1")
    _, kb = s.authenticate("bob", "bob-pass-12")
    assert ka != kb
    assert a["user_id"] != b["user_id"]


def test_duplicate_username_rejected():
    s = store()
    s.create("rahul", "Rahul", "correct-pass")
    with pytest.raises(AccountError):
        s.create("Rahul", "Another", "different-pass")


def test_short_password_and_empty_username_rejected():
    s = store()
    with pytest.raises(AccountError):
        s.create("shorty", "S", "1234567")        # 7 chars < minimum
    with pytest.raises(AccountError):
        s.create("***", "Bad", "long-enough-pass")  # normalises to empty


def test_stored_account_never_holds_the_data_key_in_clear():
    backend = InMemoryBackend()
    s = AccountStore(backend)
    s.create("me", "Me", "my-long-pass")
    _, data_key = s.authenticate("me", "my-long-pass")
    raw = backend.get("accounts/me.json")
    assert data_key.hex().encode() not in raw   # only the wrapped key is stored
    assert b"my-long-pass" not in raw           # nor the password


def test_usage_counter_increments_and_prunes_old_days():
    s = store()
    s.create("me", "Me", "my-long-pass")
    assert s.usage_today("me") == 0
    assert s.record_usage("me") == 1
    assert s.record_usage("me") == 2
    assert s.usage_today("me") == 2

    # An old day is pruned on the next write.
    acct = s._load("me")
    acct["usage"]["2000-01-01"] = 9
    s._save(acct)
    s.record_usage("me")
    assert "2000-01-01" not in s._load("me")["usage"]


def test_usage_on_unknown_user_is_zero():
    s = store()
    assert s.usage_today("ghost") == 0
    assert s.record_usage("ghost") == 0
