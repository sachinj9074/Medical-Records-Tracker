"""Tests for chat-style retrieval. The model call is injected (a fake completer),
so no API is used; the real guard and search run. Synthetic records only."""

import pytest

from src import chat, guard


def med(name, **kw):
    base = {"name": name, "strength": None, "form": None, "dose": None,
            "frequency": None, "duration": None, "purpose_plain": None}
    base.update(kw)
    return base


def rec(rid, *, date="2025-01-01", provider="Dr X", dtype="prescription",
        diagnosis=None, meds=None, invs=None):
    return {
        "record_id": rid, "episode_id": None, "record_date": date,
        "date_processed": "2026-08-17", "document_type": dtype,
        "provider": {"name": provider, "specialty": None, "clinic": None},
        "diagnosis": {"stated_text": diagnosis, "plain_language": None},
        "medications": meds or [], "investigations": invs or [],
        "advice_verbatim": None, "follow_up": None,
        "confidence": "high", "flags": [], "needs_review": "N",
    }


def corpus():
    return [
        rec("r_amox", date="2024-01-01", provider="Dr One", meds=[med("Amoxicillin")]),
        rec("r_augmentin", date="2025-06-01", provider="Dr Mehta", meds=[med("Augmentin")]),
        rec("r_lab", date="2025-03-01", dtype="lab_report",
            invs=[{"name": "HbA1c", "value": "7.2", "unit": "%", "reference_range": None,
                   "flag": "high", "plain_note": "Average blood sugar over three months."}]),
    ]


def completer_returning(intent):
    def _c(system, user):
        return dict(intent)
    return _c


def exploding_completer(system, user):
    raise RuntimeError("model unavailable")


def forbidden_completer(system, user):
    raise AssertionError("the model must not be called for a refused question")


# --- guard runs first, before any model call --------------------------------

def test_judgment_question_is_refused_without_calling_the_model():
    ans = chat.answer("is my hba1c dangerous?", corpus(), completer=forbidden_completer)
    assert ans.status == "refused"
    assert ans.message == guard.REFUSAL_MESSAGE
    assert ans.records == []


# --- intent drives deterministic retrieval ----------------------------------

def test_list_shape_returns_matching_records():
    ans = chat.answer("what amoxicillin have I had?", corpus(),
                      completer=completer_returning({"terms": ["amoxicillin"], "shape": "list"}))
    assert ans.status == "ok"
    assert [r["record_id"] for r in ans.records] == ["r_amox"]
    assert ans.lead == "1 record match."


def test_medications_shape_builds_history():
    ans = chat.answer("what antibiotics have I been prescribed?", corpus(),
                      completer=completer_returning(
                          {"terms": ["amoxicillin", "augmentin"], "shape": "medications"}))
    assert ans.status == "ok"
    keys = {m.key for m in ans.medicines}
    assert "amoxicillin" in keys and "augmentin" in keys
    assert "medicine" in ans.lead


def test_latest_shape_reports_most_recent():
    ans = chat.answer("when did I last see Dr Mehta?", corpus(),
                      completer=completer_returning({"terms": ["mehta"], "shape": "latest"}))
    assert ans.status == "ok"
    assert ans.records[0]["record_id"] == "r_augmentin"     # the Dr Mehta record
    assert ans.lead.startswith("Most recent: 2025-06-01")


def test_count_shape_ors_the_terms():
    ans = chat.answer("count my antibiotic prescriptions", corpus(),
                      completer=completer_returning(
                          {"terms": ["amoxicillin", "augmentin"], "shape": "count"}))
    # Terms are alternatives (OR): both antibiotic records count.
    assert ans.status == "ok"
    assert {r["record_id"] for r in ans.records} == {"r_amox", "r_augmentin"}
    assert ans.lead == "2 records match."


def test_document_type_filter_is_applied():
    ans = chat.answer("my blood sugar lab", corpus(),
                      completer=completer_returning(
                          {"terms": ["sugar"], "shape": "list", "document_type": "lab_report"}))
    assert ans.status == "ok"
    assert [r["record_id"] for r in ans.records] == ["r_lab"]


# --- validation and safety --------------------------------------------------

def test_off_list_intent_fields_are_dropped():
    intent = chat.parse_intent(
        "x", completer=completer_returning(
            {"terms": ["amoxicillin", 5, "  "], "shape": "wizardry",
             "document_type": "bogus", "date_from": "not-a-date"}))
    assert intent.terms == ["amoxicillin"]     # non-strings/blanks dropped, lowercased
    assert intent.shape == "list"              # unknown shape -> safe default
    assert intent.document_type is None and intent.date_from is None


def test_empty_terms_gives_not_searchable():
    ans = chat.answer("tell me a joke", corpus(),
                      completer=completer_returning({"terms": [], "shape": "list"}))
    assert ans.status == "empty"
    assert ans.message == chat.NOT_SEARCHABLE


def test_model_failure_falls_back_to_plain_search():
    ans = chat.answer("amoxicillin please", corpus(), completer=exploding_completer)
    assert ans.status == "ok"
    assert [r["record_id"] for r in ans.records] == ["r_amox"]


def test_no_match_returns_constant():
    ans = chat.answer("penicillamine", corpus(),
                      completer=completer_returning({"terms": ["penicillamine"], "shape": "list"}))
    assert ans.status == "no_match"
    assert ans.message == guard.NO_MATCH_MESSAGE


def test_answer_only_references_retrieved_records():
    src = corpus()
    ans = chat.answer("augmentin", src,
                      completer=completer_returning({"terms": ["augmentin"], "shape": "list"}))
    assert all(r in src for r in ans.records)   # grounded: nothing invented
