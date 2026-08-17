"""Tests for episode clustering and the timeline view (deterministic).

Synthetic records only. Tests assert grouping (which records share an
episode_id) and labels, never the minted id string itself.
"""

from src import timeline
from src.store import Store


def r(rid, date, provider="Dr Tupe", clinic="Skin Clinic", meds=None, diagnosis=None, episode_id=None):
    return {
        "record_id": rid,
        "episode_id": episode_id,
        "record_date": date,
        "date_processed": "2026-08-17",
        "document_type": "prescription",
        "provider": {"name": provider, "specialty": None, "clinic": clinic},
        "diagnosis": {"stated_text": diagnosis, "plain_language": None},
        "medications": [{"name": m} for m in (meds or [])],
        "investigations": [],
    }


def eid(records, rid):
    return next(x["episode_id"] for x in records if x["record_id"] == rid)


# --- the core rule ----------------------------------------------------------

def test_related_and_proximate_merge():
    recs = [r("a", "2024-09-21"), r("b", "2024-10-06")]  # same provider, 15 days
    timeline.assign_episodes(recs)
    assert eid(recs, "a") == eid(recs, "b")


def test_same_provider_beyond_gap_splits():
    recs = [r("a", "2024-10-06"), r("b", "2026-03-23")]  # ~17 months
    timeline.assign_episodes(recs)
    assert eid(recs, "a") != eid(recs, "b")


def test_within_120_days_merges():
    recs = [r("a", "2025-01-01"), r("b", "2025-04-10")]  # 99 days apart
    timeline.assign_episodes(recs)
    assert eid(recs, "a") == eid(recs, "b")


def test_beyond_120_days_splits():
    recs = [r("a", "2025-01-01"), r("b", "2025-05-15")]  # 134 days apart
    timeline.assign_episodes(recs)
    assert eid(recs, "a") != eid(recs, "b")


def test_comorbidity_mention_does_not_merge_threads():
    skin = r("skin", "2024-10-06", provider="Dr Tupe", diagnosis="Diabetic type-1")
    diab = r("diab", "2025-06-17", provider="Dr Talegaonkar", clinic="Diabetes Center",
             diagnosis="A & C OF DM", meds=["Insulin"])
    timeline.assign_episodes([skin, diab])
    assert skin["episode_id"] != diab["episode_id"]


def test_shared_medication_links_across_providers():
    a = r("a", "2025-01-01", provider="Dr One", clinic="Clinic One", meds=["Metformin"])
    b = r("b", "2025-01-20", provider="Dr Two", clinic="Clinic Two", meds=["Metformin"])
    timeline.assign_episodes([a, b])
    assert a["episode_id"] == b["episode_id"]


def test_shared_diagnosis_keyword_links():
    a = r("a", "2025-01-01", provider="Dr One", clinic="Clinic One", diagnosis="Pharyngitis")
    b = r("b", "2025-01-20", provider="Dr Two", clinic="Clinic Two", diagnosis="Acute pharyngitis")
    timeline.assign_episodes([a, b])
    assert a["episode_id"] == b["episode_id"]


def test_unrelated_records_stay_separate():
    a = r("a", "2025-01-01", provider="Dr One", clinic="Clinic One", meds=["Amoxicillin"])
    b = r("b", "2025-01-05", provider="Dr Two", clinic="Clinic Two", meds=["Ibuprofen"])
    timeline.assign_episodes([a, b])
    assert a["episode_id"] != b["episode_id"]


# --- id stability -----------------------------------------------------------

def test_existing_id_preserved_when_new_record_joins():
    recs = [r("a", "2024-09-21", episode_id="ep_known"), r("b", "2024-10-06")]
    timeline.assign_episodes(recs)
    assert eid(recs, "a") == "ep_known"
    assert eid(recs, "b") == "ep_known"


def test_bridging_merge_adopts_earliest_id():
    a = r("a", "2024-01-01", episode_id="ep_1")
    b = r("b", "2024-02-01")                       # bridges a and c
    c = r("c", "2024-03-01", episode_id="ep_2")
    timeline.assign_episodes([a, b, c])
    assert a["episode_id"] == b["episode_id"] == c["episode_id"] == "ep_1"


def test_null_date_falls_back_to_relatedness():
    a = r("a", None, provider="Dr Tupe")
    b = r("b", "2024-10-06", provider="Dr Tupe")
    timeline.assign_episodes([a, b])
    assert a["episode_id"] == b["episode_id"]


# --- timeline view ----------------------------------------------------------

def test_order_records_nulls_last():
    recs = [r("late", "2025-05-01"), r("undated", None), r("early", "2025-01-01")]
    ordered = [x["record_id"] for x in timeline.order_records(recs)]
    assert ordered == ["early", "late", "undated"]


def test_build_timeline_groups_and_labels():
    recs = [r("a", "2024-09-21", provider="Dr S.S. Tupe"),
            r("b", "2024-10-06", provider="Dr S.S. Tupe")]
    timeline.assign_episodes(recs)
    eps = timeline.build_timeline(recs)
    assert len(eps) == 1
    ep = eps[0]
    assert ep.count == 2
    assert "Dr S.S. Tupe" in ep.label
    assert "2 records" in ep.label
    assert ep.start_date == "2024-09-21" and ep.end_date == "2024-10-06"


def test_build_timeline_orders_episodes_newest_first():
    recs = [r("old", "2023-01-01", provider="Dr A", clinic="A"),
            r("new", "2025-01-01", provider="Dr B", clinic="B")]
    timeline.assign_episodes(recs)
    eps = timeline.build_timeline(recs)
    assert eps[0].record_ids == ["new"]
    assert eps[1].record_ids == ["old"]


# --- store glue -------------------------------------------------------------

def test_recluster_saves_only_changed(tmp_path):
    store = Store(str(tmp_path))
    store.save(r("a", "2024-09-21"))
    store.save(r("b", "2024-10-06"))
    changed = timeline.recluster(store)
    assert changed == 2  # both got an episode_id assigned
    ids = {rec["record_id"]: rec["episode_id"] for rec in store.list()}
    assert ids["a"] == ids["b"] and ids["a"] is not None
    # a second recluster is a no-op (ids already stable)
    assert timeline.recluster(store) == 0
