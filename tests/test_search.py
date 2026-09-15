"""Tests for guarded keyword search. Synthetic records, real guard, no model."""

from src import guard, search


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


def rec(rid, *, date="2025-01-01", provider="Dr X", clinic=None, specialty=None,
        dtype="prescription", diagnosis=None, meds=None, invs=None, advice=None):
    return {
        "record_id": rid, "episode_id": None, "record_date": date,
        "date_processed": "2026-08-17", "document_type": dtype,
        "provider": {"name": provider, "specialty": specialty, "clinic": clinic},
        "patient": {"name": None, "age": None, "sex": None},
        "diagnosis": {"stated_text": diagnosis, "plain_language": None},
        "medications": meds or [],
        "investigations": invs or [],
        "advice_verbatim": advice, "follow_up": None,
        "confidence": "high", "flags": [], "needs_review": "N",
    }


def corpus():
    return [
        rec("rec_eczema", date="2026-03-23", provider="Dr Tupe", diagnosis="DRY ECZEMA",
            meds=[med("Wellmoist Cream")]),
        rec("rec_lab", date="2026-03-01", provider="Agilus", dtype="lab_report",
            invs=[inv("HbA1c", value="7.2", flag="high",
                      plain_note="Reflects average blood sugar over about three months.")]),
        rec("rec_diab", date="2025-06-17", provider="Dr Talegaonkar", specialty="Diabetology",
            diagnosis="A & C OF DM", meds=[med("Insulin")]),
        rec("rec_amox", date="2024-01-01", provider="Dr One",
            meds=[med("Amoxicillin", purpose_plain="An antibiotic used for infections.")]),
    ]


def ids(resp):
    return [h.record["record_id"] for h in resp.hits]


# --- guard on the query path -----------------------------------------------

def test_judgment_query_is_refused():
    resp = search.search("is my hba1c high?", corpus())
    assert resp.status == "refused"
    assert resp.message == guard.REFUSAL_MESSAGE
    assert resp.hits == []


def test_empty_and_noise_only_queries():
    assert search.search("", corpus()).status == "empty"
    assert search.search("show me my records", corpus()).status == "empty"


# --- matching ---------------------------------------------------------------

def test_match_in_diagnosis():
    resp = search.search("eczema", corpus())
    assert resp.status == "ok"
    assert ids(resp) == ["rec_eczema"]
    assert "diagnosis.stated_text" in resp.hits[0].matched_fields


def test_match_in_stored_explanation_only():
    resp = search.search("average", corpus())  # only in the lab's plain_note
    assert ids(resp) == ["rec_lab"]
    assert any(f.endswith("plain_note") for f in resp.hits[0].matched_fields)


def test_and_semantics_requires_all_tokens():
    assert search.search("dry eczema", corpus()).status == "ok"
    assert search.search("dry diabetes", corpus()).status == "no_match"


def test_substring_either_way():
    resp = search.search("diabet", corpus())  # matches "Diabetology"
    assert ids(resp) == ["rec_diab"]


def test_no_match_returns_constant():
    resp = search.search("nonexistentxyz", corpus())
    assert resp.status == "no_match"
    assert resp.message == guard.NO_MATCH_MESSAGE


def test_internal_fields_not_searchable():
    resp = search.search("rec_eczema", corpus())  # a record_id, not clinical text
    assert resp.status == "no_match"


# --- ranking ----------------------------------------------------------------

def test_primary_field_outranks_explanation():
    records = [
        rec("secondary", meds=[med("Placebo", purpose_plain="contains insulin analog")]),
        rec("primary", meds=[med("Insulin")]),
    ]
    resp = search.search("insulin", records)
    assert resp.hits[0].record["record_id"] == "primary"


def test_recency_breaks_ties():
    records = [
        rec("older", date="2024-01-01", meds=[med("Metformin")]),
        rec("newer", date="2025-01-01", meds=[med("Metformin")]),
    ]
    resp = search.search("metformin", records)
    assert resp.hits[0].record["record_id"] == "newer"


# --- filters ----------------------------------------------------------------

def test_document_type_filter():
    resp = search.search("insulin", corpus(), document_type="lab_report")
    assert resp.status == "no_match"  # insulin is only in a prescription


def test_date_range_filter():
    records = [
        rec("y2024", date="2024-05-01", meds=[med("Metformin")]),
        rec("y2025", date="2025-05-01", meds=[med("Metformin")]),
    ]
    resp = search.search("metformin", records, date_from="2025-01-01", date_to="2025-12-31")
    assert ids(resp) == ["y2025"]


# --- curated synonyms -------------------------------------------------------

DIABETES = [["diabetes", "diabetic", "dm", "blood sugar", "sugar", "hba1c", "fasting glucose", "glucose"]]


def test_synonym_finds_clinical_record_from_lay_term():
    # "diabetes" appears literally in neither record, but the concept does.
    resp = search.search("diabetes", corpus(), synonyms=DIABETES)
    assert resp.status == "ok"
    got = set(ids(resp))
    assert "rec_lab" in got     # matched via HbA1c / "blood sugar" in the plain note
    assert "rec_diab" in got    # matched via "DM"
    assert "rec_eczema" not in got and "rec_amox" not in got
    assert "hba1c" in resp.expansions    # the related terms that matched are reported


def test_synonym_multiword_term_needs_all_its_tokens():
    # "blood sugar" should match the lab (plain note has both words), not a record
    # that merely contains "blood" somewhere.
    only_blood = [rec("bloodonly", diagnosis="blood in urine")]
    resp = search.search("diabetes", only_blood, synonyms=DIABETES)
    assert resp.status == "no_match"


def test_synonym_expansion_still_refuses_judgment():
    # The guard runs first: a judgment question is refused even with synonym terms.
    resp = search.search("is my diabetes dangerous?", corpus(), synonyms=DIABETES)
    assert resp.status == "refused"


def test_partial_word_does_not_activate_a_concept():
    # "diabet" is not an exact term, so it must not pull in the diabetes concept;
    # it still substring-matches "Diabetology" directly.
    resp = search.search("diabet", corpus(), synonyms=DIABETES)
    assert ids(resp) == ["rec_diab"]
    assert resp.expansions == []


def test_non_synonym_query_unaffected():
    resp = search.search("eczema", corpus(), synonyms=DIABETES)
    assert ids(resp) == ["rec_eczema"]
    assert resp.expansions == []


def test_committed_synonyms_file_loads_and_expands():
    groups = search.load_synonyms()
    assert any("hba1c" in [t.lower() for t in g] for g in groups)   # diabetes concept present
    resp = search.search("diabetes", corpus())      # uses the real committed map
    assert resp.status == "ok" and "rec_lab" in set(ids(resp))
