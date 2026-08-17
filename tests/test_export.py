"""Tests for the doctor-ready Markdown export. Synthetic records, no model."""

from src import export, guard


def med(name, **kw):
    base = {"name": name, "strength": None, "form": None, "dose": None,
            "frequency": None, "duration": None, "purpose_plain": None}
    base.update(kw)
    return base


def inv(name, **kw):
    base = {"name": name, "value": None, "unit": None, "reference_range": None,
            "flag": "unknown", "plain_note": None}
    base.update(kw)
    return base


def rec(rid, *, date="2025-01-01", episode_id="ep_1", provider="Dr Tupe",
        dtype="prescription", diagnosis=None, dx_plain=None, meds=None, invs=None,
        advice=None, follow_up=None, needs_review="N", patient_name="Rahul Mehta",
        sex="M", age=28, source="scan.jpg"):
    return {
        "record_id": rid, "episode_id": episode_id, "record_date": date,
        "date_processed": "2026-08-17", "document_type": dtype,
        "source_filename": source,
        "provider": {"name": provider, "specialty": None, "clinic": "Skin Clinic"},
        "patient": {"name": patient_name, "age": age, "sex": sex},
        "diagnosis": {"stated_text": diagnosis, "plain_language": dx_plain},
        "medications": meds or [],
        "investigations": invs or [],
        "advice_verbatim": advice, "follow_up": follow_up,
        "confidence": "high", "flags": [], "needs_review": needs_review,
    }


# --- header -----------------------------------------------------------------

def test_header_has_patient_period_generated_and_notice():
    out = export.render_summary([rec("a", date="2024-09-21"), rec("b", date="2026-03-23")])
    assert "# Medical records summary" in out
    assert "Rahul Mehta (M, 28)" in out
    assert "2024-09-21 to 2026-03-23" in out
    assert "**Generated:**" in out
    assert guard.STANDING_NOTICE in out
    assert "source of truth" in out


# --- facts present, lay gloss absent ---------------------------------------

def test_verbatim_facts_present():
    r = rec("a", diagnosis="DRY ECZEMA",
            meds=[med("Amoxicillin", strength="500 mg", form="tablet",
                      dose="1 tablet", frequency="twice daily", duration="5 days")],
            invs=[inv("HbA1c", value="7.2", unit="%", reference_range="< 5.7", flag="high")],
            advice="Get well soon.", follow_up="after 10 days")
    out = export.render_summary([r])
    assert "DRY ECZEMA" in out
    assert "Amoxicillin 500 mg (tablet): 1 tablet, twice daily, 5 days" in out
    assert "HbA1c = 7.2 % [HIGH] (ref < 5.7)" in out
    assert "Get well soon." in out
    assert "after 10 days" in out


def test_plain_language_is_omitted():
    r = rec("a", diagnosis="Pharyngitis", dx_plain="SECRET_DX_GLOSS",
            meds=[med("Amoxicillin", purpose_plain="SECRET_MED_GLOSS")],
            invs=[inv("HbA1c", value="7.2", plain_note="SECRET_INV_GLOSS")])
    out = export.render_summary([r])
    assert "SECRET_DX_GLOSS" not in out
    assert "SECRET_MED_GLOSS" not in out
    assert "SECRET_INV_GLOSS" not in out


# --- flags exactly as stored ------------------------------------------------

def test_printed_flag_shown_unknown_suppressed():
    r = rec("a", invs=[inv("HbA1c", value="7.2", flag="high"),
                       inv("Cholesterol", value="180", flag="unknown")])
    out = export.render_summary([r])
    assert "[HIGH]" in out
    assert "UNKNOWN" not in out
    assert "Cholesterol = 180" in out  # still listed, just no flag


# --- needs_review caveat ----------------------------------------------------

def test_needs_review_gets_caveat():
    out = export.render_summary([rec("a", needs_review="Y", diagnosis="X")])
    assert "unverified extraction" in out


def test_verified_record_has_no_caveat():
    out = export.render_summary([rec("a", needs_review="N", diagnosis="X")])
    assert "unverified extraction" not in out


# --- structure & ordering ---------------------------------------------------

def test_episodes_newest_first_records_oldest_first_within():
    recs = [
        rec("old1", date="2024-01-01", episode_id="ep_A"),
        rec("old2", date="2024-02-01", episode_id="ep_A"),
        rec("new", date="2025-01-01", episode_id="ep_B"),
    ]
    out = export.render_summary(recs)
    # newest episode block appears before the older one
    assert out.index("### 2025-01-01") < out.index("### 2024-01-01")
    # within the older episode, oldest record first
    assert out.index("### 2024-01-01") < out.index("### 2024-02-01")


# --- robustness -------------------------------------------------------------

def test_empty_records_message():
    out = export.render_summary([])
    assert "No records to summarise." in out


def test_null_fields_do_not_crash_and_sections_omitted():
    r = rec("a", diagnosis=None, meds=[], invs=[], advice=None, follow_up=None)
    out = export.render_summary([r])
    assert "**Medications:**" not in out
    assert "**Investigations:**" not in out
    assert "**Diagnosis" not in out


# --- selection helpers ------------------------------------------------------

def test_filter_by_date_range():
    recs = [rec("a", date="2024-05-01"), rec("b", date="2025-05-01")]
    kept = export.filter_by_date_range(recs, date_from="2025-01-01", date_to="2025-12-31")
    assert [r["record_id"] for r in kept] == ["b"]


def test_filter_by_episode():
    recs = [rec("a", episode_id="ep_1"), rec("b", episode_id="ep_2")]
    assert [r["record_id"] for r in export.filter_by_episode(recs, "ep_2")] == ["b"]
