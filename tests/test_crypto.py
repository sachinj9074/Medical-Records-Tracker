"""Tests for the per-user encryption primitives (crypto.py).

These need the `cryptography` package; the module raises CryptoError without it.
"""

import pytest

from src import crypto
from src.crypto import Cipher, CryptoError


def test_cipher_round_trip():
    key = crypto.new_data_key()
    c = Cipher(key)
    blob = c.encrypt(b"the original scan bytes")
    assert blob != b"the original scan bytes"          # actually encrypted
    assert c.decrypt(blob) == b"the original scan bytes"


def test_cipher_nonce_is_fresh_each_time():
    c = Cipher(crypto.new_data_key())
    assert c.encrypt(b"same") != c.encrypt(b"same")    # random nonce per call


def test_decrypt_with_wrong_key_raises():
    blob = Cipher(crypto.new_data_key()).encrypt(b"secret")
    with pytest.raises(CryptoError):
        Cipher(crypto.new_data_key()).decrypt(blob)


def test_decrypt_tampered_ciphertext_raises():
    c = Cipher(crypto.new_data_key())
    blob = bytearray(c.encrypt(b"secret"))
    blob[-1] ^= 0x01                                    # flip a bit in the tag/ct
    with pytest.raises(CryptoError):
        c.decrypt(bytes(blob))


def test_bad_key_length_rejected():
    with pytest.raises(CryptoError):
        Cipher(b"tooshort")


def test_derive_key_is_deterministic_and_salt_sensitive():
    salt = crypto.new_salt()
    assert crypto.derive_key("pw", salt) == crypto.derive_key("pw", salt)
    assert crypto.derive_key("pw", salt) != crypto.derive_key("pw", crypto.new_salt())
    assert len(crypto.derive_key("pw", salt)) == 32


def test_keyset_round_trip_returns_same_data_key():
    ks = crypto.create_keyset("correct horse battery staple")
    assert set(ks) == {"salt_kdf", "wrapped_key"}
    dk1 = crypto.open_keyset("correct horse battery staple", ks)
    dk2 = crypto.open_keyset("correct horse battery staple", ks)
    assert dk1 == dk2 and len(dk1) == 32


def test_keyset_wrong_password_raises():
    ks = crypto.create_keyset("right")
    with pytest.raises(CryptoError):
        crypto.open_keyset("wrong", ks)


def test_keyset_stores_only_wrapped_key_never_the_data_key():
    ks = crypto.create_keyset("pw")
    data_key = crypto.open_keyset("pw", ks)
    # The stored wrapped key must not be the data key in the clear.
    assert data_key.hex() not in ks["wrapped_key"]


def test_rewrap_keeps_the_same_data_key():
    ks = crypto.create_keyset("old-pw")
    data_key = crypto.open_keyset("old-pw", ks)
    ks2 = crypto.rewrap_keyset("old-pw", "new-pw", ks)
    assert crypto.open_keyset("new-pw", ks2) == data_key   # records still decrypt
    with pytest.raises(CryptoError):
        crypto.open_keyset("old-pw", ks2)                  # old password no longer works
