"""Streamlit UI: upload -> review-if-needed -> record + timeline + search + export.

Ties the pipeline together. Shows the original image next to every extraction
(the original is the source of truth), surfaces the standing "record aid, not
medical advice" line, and routes any typed question through the guard first.

Thin by design: all logic lives in the tested modules (ingest, timeline, search,
export, store, guard, validate). Real mode uses local_records/store/ (gitignored);
demo mode reads synthetic records from demo_cache/store/ and is read-only.

See PROJECT_SPEC.md sections 5, 8, 10, 15.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import tempfile

# `streamlit run src/app.py` puts src/ on sys.path, not the repo root, so make
# the repo root importable before pulling in the src package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st  # noqa: E402

from src import export, guard, ingest, search, timeline, validate  # noqa: E402
from src.store import DEFAULT_ROOT, Store  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


# --- config & stores (pure helpers, testable) -------------------------------

def _secret(name: str, default: str = "") -> str:
    """Read config from Streamlit secrets (hosted) or the environment (local)."""
    try:
        if name in st.secrets:  # raises if no secrets file exists
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def cfg() -> dict:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    has_key = bool(key and key != "paste-your-key-here")
    # Without a key we cannot run real mode, so fall back to the safe read-only
    # demo. An explicit APP_MODE always wins. This keeps a public deploy, which
    # carries no key, in demo mode by default.
    explicit = _secret("APP_MODE", "")
    mode = (explicit or ("real" if has_key else "demo")).lower()
    return {
        "mode": mode,
        "demo_password": _secret("DEMO_PASSWORD", ""),
        "max_uploads": int(_secret("MAX_UPLOADS_PER_SESSION", "10") or 10),
        "has_key": has_key,
    }


def store_root(mode: str) -> str:
    if mode == "demo":
        return os.path.join(_REPO, "demo_cache", "store")
    return DEFAULT_ROOT


def store_for(mode: str) -> Store:
    return Store(store_root(mode))


def export_filename(day: str | None = None) -> str:
    return f"medical_summary_{day or datetime.date.today().isoformat()}.md"


def _nz(s):
    return s.strip() if isinstance(s, str) and s.strip() else None


# --- small view helpers -----------------------------------------------------

def _record_title(r: dict) -> str:
    dx = (r.get("diagnosis") or {}).get("stated_text")
    what = dx or (r.get("document_type") or "record").replace("_", " ")
    return f"{r.get('record_date') or 'Undated'} · {what}"


def _render_readonly(rec: dict) -> None:
    d = rec.get("diagnosis") or {}
    if d.get("stated_text"):
        st.markdown(f"**Diagnosis (as stated):** {d['stated_text']}")
    if d.get("plain_language"):
        st.caption(d["plain_language"])

    meds = rec.get("medications") or []
    if meds:
        st.markdown("**Medications**")
        for m in meds:
            st.markdown(f"- {export._med_line(m)}")
            if m.get("purpose_plain"):
                st.caption("↳ " + m["purpose_plain"])

    invs = rec.get("investigations") or []
    if invs:
        st.markdown("**Investigations**")
        for inv in invs:
            st.markdown(f"- {export._inv_line(inv)}")
            if inv.get("plain_note"):
                st.caption("↳ " + inv["plain_note"])

    if rec.get("advice_verbatim"):
        st.markdown(f"**Advice (verbatim):** {rec['advice_verbatim']}")
    if rec.get("follow_up"):
        st.markdown(f"**Follow-up:** {rec['follow_up']}")


# --- pages ------------------------------------------------------------------

def page_upload(store: Store, c: dict) -> None:
    st.header("Upload a document")
    if c["mode"] == "demo":
        st.info("Upload is disabled in demo mode.")
        return
    if not c["has_key"]:
        st.warning("No ANTHROPIC_API_KEY found. Paste your key into .env to enable extraction.")
    st.caption(f"Uploads this session: {st.session_state.uploads} / {c['max_uploads']}")

    up = st.file_uploader(
        "Prescription, lab report, or discharge summary",
        type=["png", "jpg", "jpeg", "webp", "pdf"],
    )
    if up is None:
        return
    if st.session_state.uploads >= c["max_uploads"]:
        st.error("Upload limit reached for this session.")
        return

    if st.button("Digitise this document", type="primary"):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, up.name)
        with open(path, "wb") as f:
            f.write(up.getbuffer())
        try:
            with st.spinner("Reading, explaining, and filing the document..."):
                rec, report = ingest.ingest_record(path, store)
                timeline.recluster(store)
        except Exception as e:
            st.error(f"Could not process this document: {e}")
            return
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        st.session_state.uploads += 1
        note = "needs your review" if report.needs_review == "Y" else "filed"
        st.success(f"Done ({report.tier_used} read) — this record {note}.")
        st.session_state.selected = rec["record_id"]
        st.rerun()


def page_timeline(store: Store) -> None:
    st.header("Timeline")
    records = store.list()
    if not records:
        st.info("No records yet. Upload a document to get started.")
        return
    for ep in timeline.build_timeline(records):
        with st.expander(ep.label):
            for r in ep.records:
                cols = st.columns([5, 3, 2, 1])
                cols[0].write(_record_title(r))
                cols[1].write((r.get("provider") or {}).get("name") or "")
                cols[2].write("⚠ needs review" if r.get("needs_review") == "Y" else "")
                if cols[3].button("Open", key="tl_" + r["record_id"]):
                    st.session_state.selected = r["record_id"]
                    st.rerun()


def page_search(store: Store) -> None:
    st.header("Search")
    q = st.text_input("Search your records (a medicine, a test, a doctor, a condition)")
    if not q:
        return
    resp = search.search(q, store.list())
    if resp.status == "refused":
        st.warning(resp.message)
        return
    if resp.status == "empty":
        return
    if resp.status == "no_match":
        st.info(resp.message)
        return
    st.caption(f"{len(resp.hits)} match(es)")
    for h in resp.hits:
        r = h.record
        cols = st.columns([5, 3, 1])
        matched = ", ".join(h.matched_fields[:4])
        cols[0].markdown(f"{_record_title(r)}  \n_matched: {matched}_")
        cols[1].write((r.get("provider") or {}).get("name") or "")
        if cols[2].button("Open", key="se_" + r["record_id"]):
            st.session_state.selected = r["record_id"]
            st.rerun()


def page_export(store: Store) -> None:
    st.header("Export a doctor-ready summary")
    records = store.list()
    if not records:
        st.info("No records to export yet.")
        return

    scope = st.radio("Scope", ["All records", "By episode", "By date range"], horizontal=True)
    selected = records
    if scope == "By episode":
        eps = timeline.build_timeline(records)
        labels = {e.label: e.episode_id for e in eps}
        pick = st.selectbox("Episode", list(labels))
        selected = export.filter_by_episode(records, labels[pick])
    elif scope == "By date range":
        dates = sorted(d for r in records if (d := r.get("record_date")))
        if not dates:
            st.info("These records have no readable dates; exporting all.")
        else:
            lo = datetime.date.fromisoformat(dates[0])
            hi = datetime.date.fromisoformat(dates[-1])
            c1, c2 = st.columns(2)
            df = c1.date_input("From", value=lo, min_value=lo, max_value=hi)
            dt = c2.date_input("To", value=hi, min_value=lo, max_value=hi)
            selected = export.filter_by_date_range(records, df.isoformat(), dt.isoformat())

    md = export.render_summary(selected)
    st.download_button("Download summary (.md)", md, file_name=export_filename(), mime="text/markdown")
    with st.expander("Preview", expanded=True):
        st.markdown(md)


# --- record detail + review -------------------------------------------------

def _apply_edits(rec, dx, med_inputs, inv_inputs, advice, follow) -> None:
    (rec.setdefault("diagnosis", {}))["stated_text"] = _nz(dx)
    meds = rec.get("medications") or []
    for i, name, strength, form, dose, freq, dur in med_inputs:
        if i < len(meds):
            meds[i]["name"] = _nz(name) or meds[i].get("name") or "?"
            meds[i]["strength"] = _nz(strength)
            meds[i]["form"] = _nz(form)
            meds[i]["dose"] = _nz(dose)
            meds[i]["frequency"] = _nz(freq)
            meds[i]["duration"] = _nz(dur)
    invs = rec.get("investigations") or []
    for i, iname, ival, iunit, iref, iflag in inv_inputs:
        if i < len(invs):
            invs[i]["name"] = _nz(iname) or invs[i].get("name") or "?"
            invs[i]["value"] = _nz(ival)
            invs[i]["unit"] = _nz(iunit)
            invs[i]["reference_range"] = _nz(iref)
            invs[i]["flag"] = iflag
    rec["advice_verbatim"] = _nz(advice)
    rec["follow_up"] = _nz(follow)


def _edit_form(store: Store, rec: dict) -> None:
    rid = rec["record_id"]
    flags = ["unknown", "normal", "high", "low"]
    with st.expander("Correct fields, then mark reviewed", expanded=(rec.get("needs_review") == "Y")):
        with st.form("edit_" + rid):
            dx = st.text_input("Diagnosis (as stated)", value=(rec.get("diagnosis") or {}).get("stated_text") or "")

            med_inputs = []
            if rec.get("medications"):
                st.markdown("**Medications**")
            for i, m in enumerate(rec.get("medications") or []):
                a, b, cc = st.columns(3)
                name = a.text_input(f"Name {i+1}", value=m.get("name") or "", key=f"mn{i}_{rid}")
                strength = b.text_input(f"Strength {i+1}", value=m.get("strength") or "", key=f"ms{i}_{rid}")
                form = cc.text_input(f"Form {i+1}", value=m.get("form") or "", key=f"mf{i}_{rid}")
                d, e, f = st.columns(3)
                dose = d.text_input(f"Dose {i+1}", value=m.get("dose") or "", key=f"md{i}_{rid}")
                freq = e.text_input(f"Frequency {i+1}", value=m.get("frequency") or "", key=f"mq{i}_{rid}")
                dur = f.text_input(f"Duration {i+1}", value=m.get("duration") or "", key=f"mu{i}_{rid}")
                med_inputs.append((i, name, strength, form, dose, freq, dur))

            inv_inputs = []
            if rec.get("investigations"):
                st.markdown("**Investigations**")
            for i, inv in enumerate(rec.get("investigations") or []):
                a, b, cc = st.columns(3)
                iname = a.text_input(f"Test {i+1}", value=inv.get("name") or "", key=f"in{i}_{rid}")
                ival = b.text_input(f"Value {i+1}", value=inv.get("value") or "", key=f"iv{i}_{rid}")
                iunit = cc.text_input(f"Unit {i+1}", value=inv.get("unit") or "", key=f"iu{i}_{rid}")
                d, e = st.columns(2)
                iref = d.text_input(f"Reference {i+1}", value=inv.get("reference_range") or "", key=f"ir{i}_{rid}")
                cur = inv.get("flag") if inv.get("flag") in flags else "unknown"
                iflag = e.selectbox(f"Printed flag {i+1}", flags, index=flags.index(cur), key=f"if{i}_{rid}")
                inv_inputs.append((i, iname, ival, iunit, iref, iflag))

            advice = st.text_area("Advice (verbatim)", value=rec.get("advice_verbatim") or "")
            follow = st.text_input("Follow-up", value=rec.get("follow_up") or "")
            submitted = st.form_submit_button("Save corrections and mark reviewed", type="primary")

        if submitted:
            _apply_edits(rec, dx, med_inputs, inv_inputs, advice, follow)
            rec["needs_review"] = "N"
            store.save(rec)
            timeline.recluster(store)
            st.session_state.selected = rec["record_id"]
            st.success("Saved and marked reviewed.")
            st.rerun()


def render_record_detail(store: Store, rec: dict, c: dict) -> None:
    if st.button("← Back"):
        st.session_state.selected = None
        st.rerun()

    left, right = st.columns(2)
    with left:
        st.subheader("Original")
        op = store.original_path(rec["record_id"])
        if op and os.path.splitext(op)[1].lower() in _IMAGE_EXTS:
            st.image(op, use_container_width=True)
        elif op:
            st.info(f"Original on file: {os.path.basename(op)}")
        else:
            st.caption("No original on file.")

    with right:
        badge = "⚠ Needs review" if rec.get("needs_review") == "Y" else "✓ Reviewed"
        st.subheader(f"Extracted record · {badge}")
        st.caption(
            f"{rec.get('record_date') or 'Undated'} · "
            f"{(rec.get('document_type') or '').replace('_', ' ')} · confidence {rec.get('confidence')}"
        )
        if rec.get("needs_review") == "Y":
            st.warning("Flagged for review: " + ", ".join(validate.evaluate(rec).reasons))
        _render_readonly(rec)

    if c["mode"] != "demo":
        st.divider()
        _edit_form(store, rec)


# --- entry point ------------------------------------------------------------

def _demo_gate(c: dict) -> bool:
    if not c["demo_password"] or st.session_state.demo_ok:
        return True
    st.subheader("Demo access")
    pw = st.text_input("Demo password", type="password")
    if st.button("Enter"):
        if pw == c["demo_password"]:
            st.session_state.demo_ok = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


def main() -> None:
    st.set_page_config(page_title="Medical Records Tracker", layout="wide")
    ss = st.session_state
    ss.setdefault("selected", None)
    ss.setdefault("uploads", 0)
    ss.setdefault("demo_ok", False)

    c = cfg()
    store = store_for(c["mode"])

    st.title("Medical Records Tracker")
    st.caption("🛈 " + guard.STANDING_NOTICE)

    if c["mode"] == "demo" and not _demo_gate(c):
        return

    with st.sidebar:
        st.markdown(f"**Mode:** {c['mode']}")
        if c["mode"] == "demo":
            st.caption("Read-only demo (synthetic records).")
        default = 1 if c["mode"] == "demo" else 0
        page = st.radio("Go to", ["Upload", "Timeline", "Search", "Export"], index=default)
        if ss.selected and st.button("Clear selection"):
            ss.selected = None
            st.rerun()

    if ss.selected:
        rec = None
        try:
            rec = store.load(ss.selected)
        except Exception:
            ss.selected = None
        if rec:
            render_record_detail(store, rec, c)
            return

    if page == "Upload":
        page_upload(store, c)
    elif page == "Timeline":
        page_timeline(store)
    elif page == "Search":
        page_search(store)
    elif page == "Export":
        page_export(store)


if __name__ == "__main__":
    main()
