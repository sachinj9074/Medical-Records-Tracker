"""Tests for identity and per-user isolation helpers (no model, no Streamlit)."""

import pytest

from src import auth


# --- password hashing -------------------------------------------------------

def test_hash_roundtrip_and_reject():
    h = auth.hash_password("correct horse")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("correct horse", h) is True
    assert auth.verify_password("wrong", h) is False


def test_hash_is_salted_so_two_hashes_differ():
    assert auth.hash_password("same") != auth.hash_password("same")


def test_verify_rejects_malformed_stored():
    assert auth.verify_password("x", "") is False
    assert auth.verify_password("x", "not$enough") is False
    assert auth.verify_password("x", "bcrypt$1$aa$bb") is False


# --- demo accounts ----------------------------------------------------------

def test_demo_users_are_seeded():
    ids = {u.user_id for u in auth.demo_users()}
    assert {"rahul", "ananya"} <= ids
    assert all(u.is_demo for u in auth.demo_users())


def test_authenticate_demo_success_and_failure():
    assert auth.authenticate_demo("rahul", "rahul-demo") is not None
    assert auth.authenticate_demo("rahul", "wrong") is None
    assert auth.authenticate_demo("nobody", "whatever") is None


def test_demo_user_names_carry_through():
    u = auth.authenticate_demo("ananya", "ananya-demo")
    assert u is not None and u.name == "Ananya Sharma" and u.is_demo is True


# --- real (local) account ---------------------------------------------------

def test_real_user_defaults_and_env(monkeypatch):
    monkeypatch.delenv("APP_USER", raising=False)
    monkeypatch.delenv("APP_USER_NAME", raising=False)
    u = auth.real_user()
    assert u.user_id == "me" and u.is_demo is False
    monkeypatch.setenv("APP_USER", "sachin")
    assert auth.real_user().user_id == "sachin"


def test_real_password_gate(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert auth.real_password_required() is False
    # No gate configured: any/blank password authenticates the local user.
    assert auth.authenticate_real("") is not None

    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert auth.real_password_required() is True
    assert auth.authenticate_real("s3cret") is not None
    assert auth.authenticate_real("nope") is None


def test_public_shape_is_minimal():
    u = auth.authenticate_demo("rahul", "rahul-demo")
    pub = u.public()
    assert set(pub) == {"id", "name", "is_demo"}
    assert "password_hash" not in pub
