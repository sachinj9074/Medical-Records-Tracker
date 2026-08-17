"""Light tests for app.py pure helpers. The UI itself is verified by running it.

Importing app must not launch the UI (main() is guarded by __name__), so this
also guards against import-time errors in the module.
"""

import os

from src import app


def test_export_filename():
    assert app.export_filename("2026-08-17") == "medical_summary_2026-08-17.md"


def test_store_root_real_vs_demo():
    assert app.store_root("real").endswith(os.path.join("local_records", "store"))
    assert app.store_root("demo").endswith(os.path.join("demo_cache", "store"))


def test_nz_normalises_blanks():
    assert app._nz("  x ") == "x"
    assert app._nz("   ") is None
    assert app._nz(None) is None


def test_record_title_prefers_diagnosis():
    r = {"record_date": "2025-01-01", "document_type": "prescription",
         "diagnosis": {"stated_text": "DRY ECZEMA"}}
    assert app._record_title(r) == "2025-01-01 · DRY ECZEMA"
    r2 = {"record_date": "2025-01-01", "document_type": "lab_report", "diagnosis": {"stated_text": None}}
    assert app._record_title(r2) == "2025-01-01 · lab report"
