"""Tests for plain-language authoring and its fidelity guard.

The model is mocked (no live API): a fake complete_json returns canned author
and judge payloads, keyed off the system prompt. Real records are never used.
"""

import pytest

from src import explain, model


# --- deterministic backstop (pure, no model) --------------------------------

def test_clean_general_statement_passes():
    assert explain.deterministic_violations(
        "Amoxicillin is an antibiotic used for bacterial infections.", set()
    ) == []


def test_second_person_caught():
    assert "second_person" in explain.deterministic_violations("Your throat is inflamed.", set())


def test_new_number_caught():
    v = explain.deterministic_violations("A normal reading is below 5.7 percent.", set())
    assert any(x.startswith("introduces_number") for x in v)


def test_number_from_term_is_allowed():
    # "5%" is part of the term the author was given, so it is not a new number.
    assert explain.deterministic_violations("Mometasone 5% is a topical steroid.", {"5"}) == []


def test_directive_caught():
    assert "directive" in explain.deterministic_violations("Please consult your doctor.", set())


def test_too_long_caught():
    assert "too_many_sentences" in explain.deterministic_violations(
        "One thing. Two thing. Three thing.", set()
    )


# --- fixtures ---------------------------------------------------------------

def record_one_of_each():
    return {
        "diagnosis": {"stated_text": "Pharyngitis", "plain_language": None},
        "medications": [{
            "name": "Amoxicillin", "strength": "500 mg", "form": "tablet",
            "dose": "1 tablet", "frequency": "three times daily",
            "duration": "5 days", "purpose_plain": None,
        }],
        "investigations": [{
            "name": "HbA1c", "value": "7.2", "unit": "%",
            "reference_range": "< 5.7", "flag": "high", "plain_note": None,
        }],
        "flags": [],
    }


def record_one_investigation():
    return {
        "diagnosis": {"stated_text": None, "plain_language": None},
        "medications": [],
        "investigations": [{
            "name": "HbA1c", "value": "7.2", "unit": "%",
            "reference_range": "< 5.7", "flag": "high", "plain_note": None,
        }],
        "flags": [],
    }


class FakeModel:
    """Returns queued author / judge payloads, chosen by the system prompt."""

    def __init__(self, author_seq, judge_seq):
        self.author_seq = list(author_seq)
        self.judge_seq = list(judge_seq)
        self.author_calls = 0
        self.judge_calls = 0

    def complete_json(self, *, system, user, tier="judgment", max_tokens=4000):
        if system == explain.AUTHOR_SYSTEM:
            out = self.author_seq[min(self.author_calls, len(self.author_seq) - 1)]
            self.author_calls += 1
            return out
        if system == explain.JUDGE_SYSTEM:
            out = self.judge_seq[min(self.judge_calls, len(self.judge_seq) - 1)]
            self.judge_calls += 1
            return out
        raise AssertionError("unexpected system prompt")


def install(monkeypatch, author_seq, judge_seq):
    fake = FakeModel(author_seq, judge_seq)
    monkeypatch.setattr(model, "complete_json", fake.complete_json)
    return fake


# --- author -> guard -> gate ------------------------------------------------

def test_clean_record_authors_all_fields(monkeypatch):
    author = {
        "diagnosis_plain": "Pharyngitis is inflammation of the throat.",
        "medications": [{"index": 0, "purpose_plain": "Amoxicillin is an antibiotic used for bacterial infections."}],
        "investigations": [{"index": 0, "plain_note": "HbA1c reflects average blood sugar over about three months."}],
    }
    judge = {"verdicts": [
        {"key": "diagnosis", "verdict": "pass", "reason": "ok"},
        {"key": "med:0", "verdict": "pass", "reason": "ok"},
        {"key": "inv:0", "verdict": "pass", "reason": "ok"},
    ]}
    install(monkeypatch, [author], [judge])

    rec, report = explain.explain_record(record_one_of_each())
    assert rec["diagnosis"]["plain_language"].startswith("Pharyngitis")
    assert rec["medications"][0]["purpose_plain"].startswith("Amoxicillin")
    assert rec["investigations"][0]["plain_note"].startswith("HbA1c")
    assert report.withheld == []
    assert not any(f.startswith("explanation_withheld") for f in rec["flags"])


def test_deterministic_failure_withheld_when_no_retry(monkeypatch):
    # Author interprets the value and addresses the reader: caught without a model judge.
    author = {"diagnosis_plain": None, "medications": [],
              "investigations": [{"index": 0, "plain_note": "Your HbA1c of 7.2 is high."}]}
    install(monkeypatch, [author], [])  # judge never called (only failing candidate)

    rec, report = explain.explain_record(record_one_investigation(), max_retries=0)
    assert rec["investigations"][0]["plain_note"] is None
    assert "inv:0" in report.withheld
    assert "explanation_withheld:investigations[0]" in rec["flags"]


def test_retry_recovers_a_bad_field(monkeypatch):
    bad = {"diagnosis_plain": None, "medications": [],
           "investigations": [{"index": 0, "plain_note": "Your reading of 7.2 is high."}]}
    good = {"diagnosis_plain": None, "medications": [],
            "investigations": [{"index": 0, "plain_note": "HbA1c is a measure of average blood sugar."}]}
    judge_pass = {"verdicts": [{"key": "inv:0", "verdict": "pass", "reason": "ok"}]}
    install(monkeypatch, [bad, good], [judge_pass])

    rec, report = explain.explain_record(record_one_investigation(), max_retries=1)
    assert rec["investigations"][0]["plain_note"] == "HbA1c is a measure of average blood sugar."
    assert report.retries_used == 1
    assert report.withheld == []


def test_judge_failure_withholds_hallucinated_entity(monkeypatch):
    # Passes the deterministic layer but names a condition not in the record.
    author = {"diagnosis_plain": None,
              "medications": [{"index": 0, "purpose_plain": "Amoxicillin is used for diabetes."}],
              "investigations": []}
    judge = {"verdicts": [{"key": "med:0", "verdict": "fail", "reason": "names diabetes, not in record"}]}
    record = {
        "diagnosis": {"stated_text": None, "plain_language": None},
        "medications": [{"name": "Amoxicillin", "strength": None, "form": "tablet",
                         "dose": None, "frequency": None, "duration": None, "purpose_plain": None}],
        "investigations": [],
        "flags": [],
    }
    install(monkeypatch, [author], [judge])

    rec, report = explain.explain_record(record, max_retries=0)
    assert rec["medications"][0]["purpose_plain"] is None
    assert "med:0" in report.withheld
    assert "explanation_withheld:medications[0]" in rec["flags"]


def test_author_null_stays_null_without_flag(monkeypatch):
    author = {"diagnosis_plain": None,
              "medications": [{"index": 0, "purpose_plain": None}],
              "investigations": []}
    record = {
        "diagnosis": {"stated_text": None, "plain_language": None},
        "medications": [{"name": "Obscure-Compound", "strength": None, "form": None,
                         "dose": None, "frequency": None, "duration": None, "purpose_plain": None}],
        "investigations": [],
        "flags": [],
    }
    install(monkeypatch, [author], [])  # nothing to judge

    rec, report = explain.explain_record(record)
    assert rec["medications"][0]["purpose_plain"] is None
    assert report.withheld == []
    assert not any(f.startswith("explanation_withheld") for f in rec["flags"])
