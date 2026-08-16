"""Tests for deterministic schema validation and needs_review flagging.

Uses synthetic minimal records only. Real extracted records are PHI and stay
under local_records/ (gitignored); they are never used as fixtures.
"""

from src import validate


def base_record() -> dict:
    """A schema-valid, clean, high-confidence prescription: no review needed."""
    return {
        "record_id": "rec_test000001",
        "episode_id": None,
        "source_filename": "test.png",
        "date_processed": "2026-08-16",
        "document_type": "prescription",
        "record_date": "2024-09-21",
        "provider": {"name": "Dr X", "specialty": None, "clinic": None},
        "patient": {"name": "Test Patient", "age": 30, "sex": "M"},
        "diagnosis": {"stated_text": None, "plain_language": None},
        "medications": [
            {
                "name": "Amoxicillin", "strength": "500 mg", "form": "tablet",
                "dose": "1 tablet", "frequency": "three times daily",
                "duration": "5 days", "purpose_plain": None,
            }
        ],
        "investigations": [],
        "advice_verbatim": None,
        "follow_up": None,
        "confidence": "high",
        "flags": [],
        "needs_review": "N",
    }


# --- schema gate -----------------------------------------------------------

def test_clean_record_valid_and_no_review():
    res = validate.evaluate(base_record())
    assert res.schema_ok
    assert res.needs_review == "N"
    assert res.reasons == []


def test_schema_invalid_is_detected_and_forces_review():
    r = base_record()
    r["confidence"] = "very-sure"  # not in the enum
    res = validate.evaluate(r)
    assert not res.schema_ok
    assert res.schema_errors
    assert "schema_invalid" in res.reasons
    assert res.needs_review == "Y"


# --- needs_review signals (each a real reason) -----------------------------

def test_low_confidence_flags():
    r = base_record()
    r["confidence"] = "low"
    res = validate.evaluate(r)
    assert res.needs_review == "Y"
    assert "low_confidence" in res.reasons


def test_missing_date_flags():
    r = base_record()
    r["record_date"] = None
    assert "missing_date" in validate.evaluate(r).reasons


def test_unclassified_document_flags():
    r = base_record()
    r["document_type"] = "other"
    assert "unclassified_document" in validate.evaluate(r).reasons


def test_prescription_with_no_meds_flags():
    r = base_record()
    r["medications"] = []
    assert "no_medications_read" in validate.evaluate(r).reasons


def test_lab_report_with_no_investigations_flags():
    r = base_record()
    r["document_type"] = "lab_report"
    r["medications"] = []
    r["investigations"] = []
    assert "no_investigations_read" in validate.evaluate(r).reasons


def test_incomplete_dosing_flags_named_med():
    r = base_record()
    r["medications"][0]["dose"] = None
    r["medications"][0]["frequency"] = None
    reasons = validate.evaluate(r).reasons
    assert "incomplete_dosing:Amoxicillin" in reasons


def test_medium_confidence_with_legibility_flag_flags():
    r = base_record()
    r["confidence"] = "medium"
    r["flags"] = ["handwritten"]
    assert "medium_confidence_hard_read" in validate.evaluate(r).reasons


# --- noise control: weak signals must stay quiet ---------------------------

def test_medium_confidence_alone_is_quiet():
    r = base_record()
    r["confidence"] = "medium"
    res = validate.evaluate(r)
    assert res.needs_review == "N"
    assert res.reasons == []


def test_missing_duration_only_is_quiet():
    r = base_record()
    r["medications"][0]["duration"] = None  # dose and frequency still present
    assert validate.evaluate(r).needs_review == "N"


def test_missing_patient_name_alone_is_quiet():
    r = base_record()
    r["patient"]["name"] = None
    assert validate.evaluate(r).needs_review == "N"


# --- apply() mutates the record --------------------------------------------

def test_apply_writes_needs_review():
    r = base_record()
    r["confidence"] = "low"
    r["needs_review"] = "N"
    result = validate.apply(r)
    assert result.needs_review == "Y"
    assert r["needs_review"] == "Y"


# --- should_escalate (advisory routing) ------------------------------------

def test_escalate_fast_prescription_medium():
    r = base_record()
    r["confidence"] = "medium"
    assert validate.should_escalate(r, "fast") is True


def test_no_escalate_clean_high_read():
    assert validate.should_escalate(base_record(), "fast") is False


def test_escalate_fast_prescription_high_but_handwritten():
    r = base_record()
    r["flags"] = ["handwritten"]
    assert validate.should_escalate(r, "fast") is True


def test_no_escalate_when_already_judgment():
    r = base_record()
    r["confidence"] = "low"
    assert validate.should_escalate(r, "judgment") is False


def test_no_escalate_non_prescription():
    r = base_record()
    r["document_type"] = "lab_report"
    r["confidence"] = "low"
    assert validate.should_escalate(r, "fast") is False
