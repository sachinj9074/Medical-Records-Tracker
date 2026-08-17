"""Tests for the ingestion pipeline.

extract and explain are monkeypatched (no live API); validate and store are
real, run against a tmp_path store. Verifies the chain, the escalation branch,
and graceful degradation on an explanation failure.
"""

import copy

import pytest

from src import explain, extract, ingest, model
from src.store import Store


def valid_record(rid="rec_ing0001", document_type="prescription", confidence="high", flags=None):
    return {
        "record_id": rid,
        "episode_id": None,
        "source_filename": "scan.png",
        "date_processed": "2026-08-17",
        "document_type": document_type,
        "record_date": "2024-09-21",
        "provider": {"name": "Dr X", "specialty": None, "clinic": None},
        "patient": {"name": "Test", "age": 30, "sex": "M"},
        "diagnosis": {"stated_text": None, "plain_language": None},
        "medications": [{
            "name": "Amoxicillin", "strength": "500 mg", "form": "tablet",
            "dose": "1 tablet", "frequency": "twice daily", "duration": "5 days",
            "purpose_plain": None,
        }],
        "investigations": [],
        "advice_verbatim": None,
        "follow_up": None,
        "confidence": confidence,
        "flags": list(flags or []),
        "needs_review": "N",
    }


@pytest.fixture
def original(tmp_path):
    p = tmp_path / "scan.png"
    p.write_bytes(b"fake")
    return str(p)


def _no_op_explain(record, tier="judgment", **kw):
    return record, explain.ExplainReport(outcomes=[], retries_used=0, withheld=[])


def test_clean_ingest_no_escalation(tmp_path, original, monkeypatch):
    monkeypatch.setattr(extract, "extract_record",
                        lambda path, tier="fast", **kw: valid_record(confidence="high"))
    monkeypatch.setattr(explain, "explain_record", _no_op_explain)
    store = Store(str(tmp_path / "store"))

    record, report = ingest.ingest_record(original, store)
    assert report.tier_used == "fast"
    assert report.escalated is False
    assert report.needs_review == "N"
    assert store.load(record["record_id"])["record_id"] == record["record_id"]
    assert store.original_path(record["record_id"]) is not None


def test_escalation_branch_rereads_on_judgment(tmp_path, original, monkeypatch):
    def fake_extract(path, tier="fast", **kw):
        if tier == "fast":
            # a handwritten prescription -> should_escalate is True
            return valid_record(confidence="medium", flags=["handwritten"])
        return valid_record(confidence="low", flags=["handwritten", "low_confidence"])

    monkeypatch.setattr(extract, "extract_record", fake_extract)
    monkeypatch.setattr(explain, "explain_record", _no_op_explain)
    store = Store(str(tmp_path / "store"))

    record, report = ingest.ingest_record(original, store)
    assert report.escalated is True
    assert report.tier_used == "judgment"
    assert report.needs_review == "Y"  # low confidence after re-read


def test_explanation_failure_degrades_but_stores(tmp_path, original, monkeypatch):
    monkeypatch.setattr(extract, "extract_record",
                        lambda path, tier="fast", **kw: valid_record(confidence="high"))

    def boom(record, tier="judgment", **kw):
        raise model.ModelError("explain unavailable")

    monkeypatch.setattr(explain, "explain_record", boom)
    store = Store(str(tmp_path / "store"))

    record, report = ingest.ingest_record(original, store)
    assert any("explanation_failed" in e for e in report.errors)
    assert "explanation_failed" in record["flags"]
    assert report.needs_review == "Y"
    assert store.load(record["record_id"])["flags"].count("explanation_failed") == 1


def test_ingest_ephemeral_returns_record_without_a_store(original, monkeypatch):
    monkeypatch.setattr(extract, "extract_record",
                        lambda path, tier="fast", **kw: valid_record(confidence="high"))
    monkeypatch.setattr(explain, "explain_record", _no_op_explain)
    rec, report = ingest.ingest_ephemeral(original)
    assert rec["record_id"] == report.record_id
    assert report.needs_review in ("Y", "N")


def test_escalation_failure_falls_back_to_fast(tmp_path, original, monkeypatch):
    def fake_extract(path, tier="fast", **kw):
        if tier == "fast":
            return valid_record(confidence="medium", flags=["handwritten"])
        raise model.ModelError("judgment tier down")

    monkeypatch.setattr(extract, "extract_record", fake_extract)
    monkeypatch.setattr(explain, "explain_record", _no_op_explain)
    store = Store(str(tmp_path / "store"))

    record, report = ingest.ingest_record(original, store)
    assert report.escalated is False
    assert report.tier_used == "fast"
    assert any("escalation_failed" in e for e in report.errors)
    assert store.exists(record["record_id"])
