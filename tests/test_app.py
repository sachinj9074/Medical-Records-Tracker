"""Light tests for app.py pure helpers. The UI itself is verified by running it.

Importing app must not launch the UI (main() is guarded by __name__), so this
also guards against import-time errors in the module.
"""

import os

from src import app


def test_export_filename():
    assert app.export_filename("2026-08-17") == "medical_summary_2026-08-17.md"


def test_store_root_demo_layout():
    # store_root now serves the demo's local plaintext archive only.
    assert app.store_root("demo", "rahul").endswith(os.path.join("demo_cache", "users", "rahul", "store"))


def test_store_root_isolates_users():
    # Two users resolve to different roots; that is the isolation guarantee.
    assert app.store_root("demo", "rahul") != app.store_root("demo", "ananya")


def test_nz_normalises_blanks():
    assert app._nz("  x ") == "x"
    assert app._nz("   ") is None
    assert app._nz(None) is None


def test_cfg_reports_key_presence_and_no_locked_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("R2_BUCKET", raising=False)
    c = app.cfg()
    assert c["has_key"] is False
    assert c["storage"] == "local"
    assert "mode" not in c            # mode is a per-session choice now, not config
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert app.cfg()["has_key"] is True


def test_cfg_selects_r2_when_bucket_configured(monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "mrt-bucket")
    c = app.cfg()
    assert c["storage"] == "r2" and c["r2"]["bucket"] == "mrt-bucket"


def test_cfg_real_cap_default_and_override(monkeypatch):
    monkeypatch.delenv("REAL_UPLOADS_PER_DAY", raising=False)
    assert app.cfg()["real_cap"] == 25
    monkeypatch.setenv("REAL_UPLOADS_PER_DAY", "5")
    assert app.cfg()["real_cap"] == 5


def test_cfg_demo_live_cap_default_and_override(monkeypatch):
    monkeypatch.delenv("DEMO_LIVE_UPLOADS", raising=False)
    assert app.cfg()["demo_live_cap"] == 2
    monkeypatch.setenv("DEMO_LIVE_UPLOADS", "0")
    assert app.cfg()["demo_live_cap"] == 0


def test_cfg_real_access_code(monkeypatch):
    monkeypatch.delenv("REAL_ACCESS_CODE", raising=False)
    assert app.cfg()["real_access_code"] == ""     # blank = no gate
    monkeypatch.setenv("REAL_ACCESS_CODE", "let-me-in")
    assert app.cfg()["real_access_code"] == "let-me-in"


def test_demo_samples_load_prebaked_runs():
    # Pre-baked runs let the demo show extraction with no API call.
    samples = app._demo_samples()
    assert len(samples) >= 1
    for s in samples:
        assert s["record"].get("record_id")   # a genuine extraction record
        assert os.path.exists(s["image"])      # a bundled original image
        assert s["title"]


def test_record_title_prefers_diagnosis():
    r = {"record_date": "2025-01-01", "document_type": "prescription",
         "diagnosis": {"stated_text": "DRY ECZEMA"}}
    assert app._record_title(r) == "2025-01-01 · DRY ECZEMA"
    r2 = {"record_date": "2025-01-01", "document_type": "lab_report", "diagnosis": {"stated_text": None}}
    assert app._record_title(r2) == "2025-01-01 · lab report"


def test_doc_icon_maps_types():
    assert app._doc_icon({"document_type": "prescription"}) == "💊"
    assert app._doc_icon({"document_type": "lab_report"}) == "🧪"
    assert app._doc_icon({"document_type": "discharge_summary"}) == "🏥"
    assert app._doc_icon({"document_type": "weird"}) == "📄"


def test_headline_prefers_diagnosis_then_lab_summary():
    assert app._headline({"diagnosis": {"stated_text": "Acute pharyngitis"}}) == "Acute pharyngitis"
    lab = {"document_type": "lab_report", "diagnosis": {"stated_text": None},
           "investigations": [{"name": "HbA1c"}, {"name": "FPG"}, {"name": "TChol"}]}
    assert app._headline(lab) == "Lab: HbA1c, FPG +1 more"
    assert app._headline({"document_type": "prescription", "diagnosis": {}}) == "Prescription"


def test_overview_counts_and_span():
    recs = [
        {"record_date": "2024-01-01", "needs_review": "N"},
        {"record_date": "2025-06-01", "needs_review": "Y"},
        {"record_date": None, "needs_review": "N"},
    ]
    ov = app._overview(recs)
    assert ov["records"] == 3
    assert ov["needs_review"] == 1
    assert ov["date_from"] == "2024-01-01" and ov["date_to"] == "2025-06-01"
    empty = app._overview([])
    assert empty["records"] == 0 and empty["date_from"] is None
