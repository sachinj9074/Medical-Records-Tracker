"""Tests for the pluggable storage backends (storage.py).

LocalBackend and InMemoryBackend are exercised directly. R2Backend is exercised
against a fake S3 client (no network, no boto3), which is the same seam the app
uses to inject a real client.
"""

import pytest

from src.storage import InMemoryBackend, LocalBackend, R2Backend, StorageError


# --- a fake S3/R2 client ----------------------------------------------------

class _Missing(Exception):
    """Mimics botocore's ClientError shape for a missing key."""
    response = {"Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeS3:
    def __init__(self, page_size=1000):
        self.store: dict[str, bytes] = {}
        self.page_size = page_size

    def put_object(self, *, Bucket, Key, Body):
        self.store[Key] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise _Missing()
        import io
        return {"Body": io.BytesIO(self.store[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise _Missing()
        return {}

    def delete_object(self, *, Bucket, Key):
        self.store.pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.store if k.startswith(Prefix))
        start = int(ContinuationToken) if ContinuationToken else 0
        page = keys[start:start + self.page_size]
        nxt = start + self.page_size
        truncated = nxt < len(keys)
        resp = {"Contents": [{"Key": k} for k in page], "IsTruncated": truncated}
        if truncated:
            resp["NextContinuationToken"] = str(nxt)
        return resp


# --- a shared contract every backend must satisfy ---------------------------

def _roundtrip(b):
    b.put("records/a.json", b"one")
    b.put("originals/a.png", b"two")
    assert b.exists("records/a.json")
    assert not b.exists("records/missing.json")
    assert b.get("records/a.json") == b"one"
    assert b.list("records/") == ["records/a.json"]
    assert sorted(b.list("")) == ["originals/a.png", "records/a.json"]
    b.delete("records/a.json")
    assert not b.exists("records/a.json")
    b.delete("records/a.json")  # idempotent


def test_local_backend_contract(tmp_path):
    _roundtrip(LocalBackend(str(tmp_path)))


def test_inmemory_backend_contract():
    _roundtrip(InMemoryBackend())


def test_r2_backend_contract():
    _roundtrip(R2Backend("bucket", client=FakeS3()))


def test_missing_get_raises_keyerror():
    for b in (LocalBackend_from_tmp(), InMemoryBackend(), R2Backend("bk", client=FakeS3())):
        with pytest.raises(KeyError):
            b.get("records/nope.json")


def LocalBackend_from_tmp():
    import tempfile
    return LocalBackend(tempfile.mkdtemp())


def test_local_backend_writes_are_readable_bytes(tmp_path):
    b = LocalBackend(str(tmp_path))
    b.put("records/x.json", b"hello")
    assert (tmp_path / "records" / "x.json").read_bytes() == b"hello"


def test_r2_list_paginates():
    client = FakeS3(page_size=2)
    b = R2Backend("bucket", client=client)
    for i in range(5):
        b.put(f"records/{i}.json", b"x")
    assert b.list("records/") == [f"records/{i}.json" for i in range(5)]


def test_r2_get_wraps_real_errors_as_storageerror():
    class Boom(FakeS3):
        def get_object(self, *, Bucket, Key):
            raise RuntimeError("network down")
    b = R2Backend("bucket", client=Boom())
    with pytest.raises(StorageError):
        b.get("records/a.json")


def test_prefixed_backend_confines_and_isolates():
    from src.storage import PrefixedBackend
    base = InMemoryBackend()
    alice = PrefixedBackend(base, "users/alice/")
    bob = PrefixedBackend(base, "users/bob/")

    alice.put("records/1.json", b"alice-record")
    bob.put("records/1.json", b"bob-record")

    # Each sees only its own records, under plain (un-prefixed) keys.
    assert alice.list("records/") == ["records/1.json"]
    assert alice.get("records/1.json") == b"alice-record"
    assert bob.get("records/1.json") == b"bob-record"
    # Under the hood they are namespaced apart, and neither can name the other.
    assert set(base.list("")) == {"users/alice/records/1.json", "users/bob/records/1.json"}
    with pytest.raises(KeyError):
        bob.get("records/missing.json")

    alice.delete("records/1.json")
    assert not alice.exists("records/1.json")
    assert bob.exists("records/1.json")   # deleting one user's key leaves the other's
