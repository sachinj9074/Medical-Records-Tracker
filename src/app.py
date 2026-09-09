"""Streamlit UI: sign in -> timeline home -> record detail -> search -> doctor summary.

Value-forward by design: the app opens on your health story (the timeline with an
overview and episode cards), dramatises the "messy original vs clean reading" trust
story on each record, makes review one tap, and sells the doctor-ready export. All
logic lives in the tested modules (ingest, timeline, search, export, store, guard,
validate, auth); this file is presentation and flow only.

Identity and modes:
  - Every user signs in, and each user's records live under their own store root
    (see store_root), so one user can never see another's records: isolation by
    construction (src/auth.py owns identity).
  - real  (a key is present locally): one local account (you). Records are stored
    privately under local_records/, never leave the machine.
  - demo  (a deploy, or no key): seeded demo profiles, each owning a synthetic
    archive under demo_cache/. A reviewer logs in as one profile, then another,
    and sees only that profile's records. Where a key is configured, a logged-in
    demo profile can also upload; those uploads are kept ONLY in the session.

See PROJECT_SPEC.md sections 5, 8, 10, 15.
"""

from __future__ import annotations

import datetime
import html
import os
import shutil
import sys
import tempfile

# `streamlit run src/app.py` puts src/ on sys.path, not the repo root, so make
# the repo root importable before pulling in the src package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st  # noqa: E402

from src import auth, backup, export, guard, ingest, search, timeline, validate  # noqa: E402
from src.store import Store  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_SECRET_KEYS = (
    "ANTHROPIC_API_KEY", "APP_MODE", "MAX_UPLOADS_PER_SESSION",
    "APP_USER", "APP_USER_NAME", "APP_PASSWORD",
)

# Navigation: internal key -> sidebar label. Keys stay stable for routing/tests.
NAV = [("Timeline", "🗓  Timeline"), ("Upload", "➕  Add a document"),
       ("Search", "🔍  Search"), ("Export", "📤  Doctor summary")]

_DOC_ICONS = {"prescription": "💊", "lab_report": "🧪", "discharge_summary": "🏥", "other": "📄"}

_CSS = """
<style>
.mrt-pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:0.72rem;
  font-weight:600;vertical-align:middle;white-space:nowrap;}
.mrt-ok{background:rgba(46,160,67,0.16);color:#2e9e4b;}
.mrt-review{background:rgba(191,135,0,0.20);color:#c08a00;}
.mrt-head{font-weight:600;font-size:0.98rem;}
.mrt-sub{opacity:0.72;font-size:0.85rem;}
</style>
"""


# --- config & stores (pure helpers, testable) -------------------------------

def _secret(name: str, default: str = "") -> str:
    """Read config from Streamlit secrets (hosted) or the environment (local)."""
    try:
        if name in st.secrets:  # raises if no secrets file exists
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def _hosted() -> bool:
    """True when Streamlit secrets are configured, i.e. this is a deploy."""
    try:
        return any(k in st.secrets for k in _SECRET_KEYS)
    except Exception:
        return False


def bridge_secrets() -> None:
    """Copy Streamlit secrets into the environment so the SDK and config see them."""
    try:
        for k in _SECRET_KEYS:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        pass


def cfg() -> dict:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    has_key = bool(key and key != "paste-your-key-here")
    # An explicit APP_MODE always wins. A deploy (secrets present) defaults to the
    # safe demo even when a key is set, so a hosted app never silently runs real
    # mode. Locally, real mode is the default when a key is present.
    explicit = _secret("APP_MODE", "").lower()
    if explicit in ("demo", "real"):
        mode = explicit
    elif _hosted():
        mode = "demo"
    else:
        mode = "real" if has_key else "demo"
    return {
        "mode": mode,
        "max_uploads": int(_secret("MAX_UPLOADS_PER_SESSION", "10") or 10),
        "has_key": has_key,
    }


def store_root(mode: str, user_id: str) -> str:
    """Per-user store root. Isolation lives here: the user_id picks the folder,
    and no code path ever reads across users."""
    if mode == "demo":
        return os.path.join(_REPO, "demo_cache", "users", user_id, "store")
    return os.path.join(_REPO, "local_records", "store", "users", user_id)


def store_for(mode: str, user_id: str) -> Store:
    return Store(store_root(mode, user_id))


def export_filename(day: str | None = None) -> str:
    return f"medical_summary_{day or datetime.date.today().isoformat()}.md"


def _nz(s):
    return s.strip() if isinstance(s, str) and s.strip() else None


# --- record sources ---------------------------------------------------------

def all_records(store: Store, c: dict) -> list:
    """Stored records, plus this session's ephemeral demo uploads (demo mode)."""
    records = list(store.list())
    extra = st.session_state.get("session_records") or []
    if c["mode"] == "demo" and extra:
        records = records + list(extra)
        timeline.assign_episodes(records)  # cluster the combined set for display only
    return records


def find_record(store: Store, rid: str):
    for r in (st.session_state.get("session_records") or []):
        if r.get("record_id") == rid:
            return r
    try:
        return store.load(rid)
    except Exception:
        return None


# --- small view helpers (pure, testable) ------------------------------------

def _record_title(r: dict) -> str:
    dx = (r.get("diagnosis") or {}).get("stated_text")
    what = dx or (r.get("document_type") or "record").replace("_", " ")
    return f"{r.get('record_date') or 'Undated'} · {what}"


def _doc_icon(r: dict) -> str:
    return _DOC_ICONS.get(r.get("document_type"), "📄")


def _doc_label(r: dict) -> str:
    return (r.get("document_type") or "record").replace("_", " ")


def _headline(r: dict) -> str:
    """A human title for a record: the diagnosis, else a lab summary, else the type."""
    dx = (r.get("diagnosis") or {}).get("stated_text")
    if dx:
        return dx
    if r.get("document_type") == "lab_report":
        names = [i.get("name") for i in (r.get("investigations") or []) if i.get("name")]
        if names:
            more = f" +{len(names) - 2} more" if len(names) > 2 else ""
            return "Lab: " + ", ".join(names[:2]) + more
    return _doc_label(r).capitalize()


def _overview(records: list) -> dict:
    """At-a-glance counts for the timeline header. Pure; episode count is added by
    the caller (it needs the clustering)."""
    dates = sorted(d for r in records if isinstance((d := r.get("record_date")), str))
    return {
        "records": len(records),
        "needs_review": sum(1 for r in records if r.get("needs_review") == "Y"),
        "date_from": dates[0] if dates else None,
        "date_to": dates[-1] if dates else None,
    }


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _pill(r: dict) -> str:
    if r.get("needs_review") == "Y":
        return '<span class="mrt-pill mrt-review">Needs review</span>'
    return '<span class="mrt-pill mrt-ok">Reviewed</span>'


def _provider_str(r: dict) -> str:
    p = r.get("provider") or {}
    return p.get("name") or p.get("clinic") or ""


# --- reading (right-hand column of a record) --------------------------------

def _render_reading(rec: dict) -> None:
    d = rec.get("diagnosis") or {}
    if d.get("stated_text"):
        st.markdown(f"**Diagnosis:** {d['stated_text']}")
        if d.get("plain_language"):
            st.markdown(f'<span class="mrt-sub">{_esc(d["plain_language"])}</span>', unsafe_allow_html=True)

    meds = rec.get("medications") or []
    if meds:
        st.markdown("**Medications**")
        for m in meds:
            st.markdown(f"- {export._med_line(m)}")
            if m.get("purpose_plain"):
                st.markdown(f'<span class="mrt-sub">&nbsp;&nbsp;&nbsp;{_esc(m["purpose_plain"])}</span>',
                            unsafe_allow_html=True)

    invs = rec.get("investigations") or []
    if invs:
        st.markdown("**Investigations**")
        for inv in invs:
            st.markdown(f"- {export._inv_line(inv)}")
            if inv.get("plain_note"):
                st.markdown(f'<span class="mrt-sub">&nbsp;&nbsp;&nbsp;{_esc(inv["plain_note"])}</span>',
                            unsafe_allow_html=True)

    if rec.get("advice_verbatim"):
        st.markdown(f"**Advice (verbatim):** {rec['advice_verbatim']}")
    if rec.get("follow_up"):
        st.markdown(f"**Follow-up:** {rec['follow_up']}")


# --- upload -----------------------------------------------------------------

def page_upload(store: Store, c: dict) -> None:
    st.subheader("➕  Add a document")
    if c["mode"] == "demo":
        _demo_upload(c)
        return

    if not store.list():   # guided first run for a brand-new archive
        st.markdown(
            "**How it works**\n\n"
            "1. Add a photo or PDF of a prescription, lab report, or discharge summary.\n"
            "2. It is read and explained in plain language, and filed on your timeline.\n"
            "3. Find it again later, or export a clean summary to hand a doctor."
        )
    st.caption("Read, explained, and saved privately on this machine.")
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

    if st.button("Read this document", type="primary"):
        rec, report = _run_ingest(up, lambda p: _stored_ingest(store, p))
        if rec is None:
            return
        st.session_state.uploads += 1
        note = "needs your review" if report.needs_review == "Y" else "filed"
        st.success(f"Done ({report.tier_used} read). This record {note}.")
        st.session_state.selected = rec["record_id"]
        st.rerun()


def _stored_ingest(store: Store, path: str):
    rec, report = ingest.ingest_record(path, store)
    timeline.recluster(store)
    return rec, report


def _run_ingest(up, ingest_fn):
    """Write the upload to a temp file, run ingest_fn(path), clean up. Returns
    (record, report) or (None, None) on failure (error already surfaced)."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, up.name)
    with open(path, "wb") as f:
        f.write(up.getbuffer())
    try:
        with st.spinner("Reading and explaining the document..."):
            return ingest_fn(path)
    except Exception as e:
        st.error(f"Could not process this document: {e}")
        return None, None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _demo_upload(c: dict) -> None:
    if not c["has_key"]:
        st.info(
            "Uploading is not enabled in this demo deployment. Browse this profile's "
            "records from the Timeline or Search."
        )
        return

    st.caption(
        "Processed live and kept only in this browser session: never saved to the "
        "app, and not visible to any other profile or visitor."
    )
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

    if st.button("Read this document", type="primary"):
        rec, report = _run_ingest(up, lambda p: ingest.ingest_ephemeral(p))
        if rec is None:
            return
        st.session_state.session_records.append(rec)
        st.session_state.session_originals[rec["record_id"]] = (up.getvalue(), up.name)
        st.session_state.uploads += 1
        note = "needs review" if report.needs_review == "Y" else "read cleanly"
        st.success(f"Done ({report.tier_used} read), {note}. Kept in this session only.")
        st.session_state.selected = rec["record_id"]
        st.rerun()


# --- timeline (home) --------------------------------------------------------

def _open(rid: str) -> None:
    st.session_state.selected = rid
    st.rerun()


def _record_row(r: dict) -> None:
    left, right = st.columns([6, 1])
    with left:
        st.markdown(
            f'{_doc_icon(r)} <span class="mrt-head">{_esc(_headline(r))}</span> &nbsp;{_pill(r)}<br>'
            f'<span class="mrt-sub">{_esc(r.get("record_date") or "Undated")}'
            + (f' · {_esc(_provider_str(r))}' if _provider_str(r) else "")
            + '</span>',
            unsafe_allow_html=True,
        )
    if right.button("Open", key="tl_" + r["record_id"]):
        _open(r["record_id"])


_TYPE_FILTER = {
    "All documents": None, "Prescriptions": "prescription", "Lab reports": "lab_report",
    "Discharge summaries": "discharge_summary", "Other": "other",
}


def page_timeline(store: Store, c: dict) -> None:
    st.subheader("🗓  Your timeline")
    records = all_records(store, c)
    if not records:
        _empty_timeline(c)
        return

    ov = _overview(records)
    episodes_all = timeline.build_timeline(records)
    m1, m2, m3 = st.columns(3)
    m1.metric("Records", ov["records"])
    m2.metric("Episodes", len(episodes_all))
    m3.metric("Needs review", ov["needs_review"])
    span = f'Spanning {ov["date_from"]} to {ov["date_to"]}. ' if ov["date_from"] else ""
    st.caption(span + "Grouped into episodes of care, newest first. Open one to see the original beside the reading.")

    f1, f2 = st.columns([2, 1])
    type_choice = f1.selectbox("Show", list(_TYPE_FILTER), key="tl_type")
    review_only = f2.toggle("Needs review only", key="tl_review_only")

    shown = records
    if _TYPE_FILTER[type_choice]:
        shown = [r for r in shown if r.get("document_type") == _TYPE_FILTER[type_choice]]
    if review_only:
        shown = [r for r in shown if r.get("needs_review") == "Y"]

    episodes = timeline.build_timeline(shown)
    if not episodes:
        st.info("No records match these filters.")
        return

    for ep in episodes:
        with st.container(border=True):
            st.markdown(f"**{_esc(ep.label)}**")
            for r in ep.records:
                _record_row(r)


def _empty_timeline(c: dict) -> None:
    st.info("Your timeline is empty.")
    st.markdown(
        "Add your first prescription or report. It will be read, explained in plain "
        "language, and filed here so you can find it again and hand a clean summary to a doctor."
    )
    if c["mode"] != "demo" and st.button("➕  Add your first document", type="primary"):
        st.session_state.nav = "Upload"
        st.rerun()


# --- search -----------------------------------------------------------------

def page_search(store: Store, c: dict) -> None:
    st.subheader("🔍  Search")
    st.caption("Find a record by what is written on it. Medical-advice questions are declined and sent back to a doctor.")

    examples = ["amoxicillin", "HbA1c", "dermatitis", "thyroid"]
    cols = st.columns(len(examples) + 1)
    cols[0].caption("Try:")
    for i, ex in enumerate(examples):
        if cols[i + 1].button(ex, key="ex_" + ex):
            st.session_state.search_q = ex   # set before the input is built this run
            st.rerun()

    q = st.text_input("Search", key="search_q", label_visibility="collapsed",
                      placeholder="a medicine, a test, a doctor, a condition")
    if not q:
        return
    resp = search.search(q, all_records(store, c))
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
        with st.container(border=True):
            left, right = st.columns([6, 1])
            matched = ", ".join(h.matched_fields[:4])
            with left:
                st.markdown(
                    f'{_doc_icon(r)} <span class="mrt-head">{_esc(_headline(r))}</span> &nbsp;{_pill(r)}<br>'
                    f'<span class="mrt-sub">{_esc(r.get("record_date") or "Undated")}'
                    + (f' · {_esc(_provider_str(r))}' if _provider_str(r) else "")
                    + f' · matched: {_esc(matched)}</span>',
                    unsafe_allow_html=True,
                )
            if right.button("Open", key="se_" + r["record_id"]):
                _open(r["record_id"])


# --- doctor summary (export) ------------------------------------------------

def page_export(store: Store, c: dict) -> None:
    st.subheader("📤  Doctor summary")
    st.caption("A clean, facts-only summary: diagnoses, medicines, and tests exactly as written, grouped by episode. Download and hand it to a doctor.")
    records = all_records(store, c)
    if not records:
        st.info("No records to summarise yet.")
        return

    pre_ep = st.session_state.pop("export_episode", None)  # optional preselect from a record
    options = ["All records", "By episode", "By date range"]
    scope = st.radio("Include", options, index=(1 if pre_ep else 0), horizontal=True)

    selected = records
    if scope == "By episode":
        eps = timeline.build_timeline(records)
        labels = {e.label: e.episode_id for e in eps}
        names = list(labels)
        idx = next((i for i, e in enumerate(eps) if e.episode_id == pre_ep), 0)
        pick = st.selectbox("Episode", names, index=idx)
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
    pdf_bytes = _pdf_or_none(selected)
    d1, d2 = st.columns(2)
    if pdf_bytes is not None:
        d1.download_button("⬇  Download PDF", pdf_bytes, file_name=export_filename().replace(".md", ".pdf"),
                           mime="application/pdf", type="primary")
    else:
        d1.caption("PDF export unavailable (install fpdf2).")
    d2.download_button("⬇  Download Markdown (.md)", md, file_name=export_filename(), mime="text/markdown")
    with st.expander("Preview", expanded=True):
        st.markdown(md)


def _pdf_or_none(records: list):
    """PDF bytes for the summary, or None if PDF rendering is unavailable."""
    try:
        return export.render_pdf(records)
    except Exception:
        return None


# --- data: backup and restore (real mode only) ------------------------------

def page_data(store: Store, c: dict) -> None:
    st.subheader("🗄  Data and backup")

    st.markdown("**Back up your archive**")
    st.caption(
        "Download a complete, portable copy of your records and their original scans. "
        "Keep it somewhere safe: if this machine is lost, your archive lives on."
    )
    pw = st.text_input(
        "Protect with a passphrase (recommended)", type="password", key="bk_pw",
        help="Encrypts the backup. You need this exact passphrase to restore it; if you lose it, the backup cannot be opened.",
    )
    if st.button("Create backup", type="primary"):
        try:
            data = backup.make_backup(store, passphrase=pw or None)
        except backup.BackupError as e:
            st.error(str(e))
        else:
            ext = "mrtbak" if pw else "zip"
            st.session_state.backup_blob = (data, f"medical_backup_{datetime.date.today().isoformat()}.{ext}")
    blob = st.session_state.get("backup_blob")
    if blob:
        st.download_button("⬇  Download backup", blob[0], file_name=blob[1],
                           mime="application/octet-stream")
        if blob[1].endswith(".mrtbak"):
            st.caption("Encrypted. Restore needs the same passphrase.")

    st.divider()
    st.markdown("**Restore from a backup**")
    st.caption("Merges into this archive: existing records are kept, matching ids updated, nothing deleted.")
    up = st.file_uploader("Backup file", type=["zip", "mrtbak"], key="restore_up")
    rpw = st.text_input("Passphrase (only if the backup is encrypted)", type="password", key="rs_pw")
    if up is not None and st.button("Restore from this backup"):
        try:
            res = backup.restore_backup(store, up.getvalue(), passphrase=rpw or None)
            timeline.recluster(store)
        except backup.BackupError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Could not restore: {e}")
        else:
            st.success(f"Restored: {res['added']} added, {res['updated']} updated.")


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


def _mark_reviewed(store: Store, rec: dict) -> None:
    rec["needs_review"] = "N"
    store.save(rec)
    timeline.recluster(store)
    st.session_state.editing = None


def _edit_form(store: Store, rec: dict) -> None:
    rid = rec["record_id"]
    flags = ["unknown", "normal", "high", "low"]
    st.markdown("**Correct the fields, then save**")
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

    if st.button("Cancel"):
        st.session_state.editing = None
        st.rerun()

    if submitted:
        _apply_edits(rec, dx, med_inputs, inv_inputs, advice, follow)
        _mark_reviewed(store, rec)
        st.session_state.selected = rec["record_id"]
        st.success("Saved and marked reviewed.")
        st.rerun()


def _show_image(src) -> None:
    """Render an image, degrading to a note if the bytes/file cannot be decoded,
    so one unreadable original never breaks the whole record view."""
    try:
        st.image(src, use_container_width=True)
    except Exception:
        st.info("The original scan is on file but could not be displayed here.")


def _original_panel(store: Store, rec: dict, is_session: bool) -> None:
    rid = rec["record_id"]
    st.markdown("**The original** (the source of truth)")
    if is_session:
        data, name = st.session_state.session_originals[rid]
        if os.path.splitext(name)[1].lower() in _IMAGE_EXTS:
            _show_image(data)
        else:
            st.info(f"Original on file: {name}")
        return
    op = store.original_path(rid)
    if op and os.path.splitext(op)[1].lower() in _IMAGE_EXTS:
        _show_image(op)
    elif op:
        st.info(f"Original on file: {os.path.basename(op)}")
    else:
        st.caption("No original on file.")


def render_record_detail(store: Store, rec: dict, c: dict) -> None:
    if st.button("←  Back to timeline"):
        st.session_state.selected = None
        st.session_state.editing = None
        st.rerun()

    rid = rec["record_id"]
    is_session = rid in (st.session_state.get("session_originals") or {})
    editable = c["mode"] != "demo" and not is_session

    sub = (f"{rec.get('record_date') or 'Undated'} · {_doc_label(rec)}"
           + (f" · {_provider_str(rec)}" if _provider_str(rec) else "")
           + f" · confidence {rec.get('confidence')}")
    st.markdown(f"### {_doc_icon(rec)}  {_esc(_headline(rec))}")
    st.markdown(f'<span class="mrt-sub">{_esc(sub)}</span> &nbsp; {_pill(rec)}', unsafe_allow_html=True)
    st.caption("🛈 " + guard.STANDING_NOTICE)

    # One-tap review for an editable, flagged record (unless already editing).
    if editable and rec.get("needs_review") == "Y" and st.session_state.get("editing") != rid:
        st.warning("Flagged for a quick check: " + ", ".join(validate.evaluate(rec).reasons))
        b1, b2 = st.columns(2)
        if b1.button("Looks right, mark reviewed", type="primary"):
            _mark_reviewed(store, rec)
            st.session_state.selected = rid
            st.rerun()
        if b2.button("Correct fields"):
            st.session_state.editing = rid
            st.rerun()

    left, right = st.columns(2)
    with left:
        _original_panel(store, rec, is_session)
    with right:
        st.markdown("**What the tool read**")
        _render_reading(rec)

    if st.button("📤  Add to doctor summary"):
        st.session_state.export_episode = rec.get("episode_id")
        st.session_state.selected = None
        st.session_state.nav = "Export"
        st.rerun()

    # Editing writes to the store, so it is only offered for stored records.
    if editable:
        st.divider()
        if st.session_state.get("editing") == rid:
            _edit_form(store, rec)
        elif rec.get("needs_review") != "Y":
            if st.button("Edit fields"):
                st.session_state.editing = rid
                st.rerun()

    # Delete: your own records only (a stored real record, or a session upload).
    # Seeded demo records are fixtures and cannot be deleted.
    if editable or is_session:
        _delete_control(store, rid, is_session)


def _delete_control(store: Store, rid: str, is_session: bool) -> None:
    with st.expander("Delete this record"):
        st.caption("Removes the record and its stored original. This cannot be undone.")
        if st.session_state.get("confirm_delete") != rid:
            if st.button("Delete this record", key="del_" + rid):
                st.session_state.confirm_delete = rid
                st.rerun()
            return
        st.warning("Delete this record permanently?")
        yes, no = st.columns(2)
        if yes.button("Yes, delete", type="primary", key="delok_" + rid):
            if is_session:
                st.session_state.session_records = [
                    r for r in st.session_state.session_records if r.get("record_id") != rid
                ]
                st.session_state.session_originals.pop(rid, None)
            else:
                store.delete(rid)
                timeline.recluster(store)
            st.session_state.confirm_delete = None
            st.session_state.selected = None
            st.rerun()
        if no.button("Cancel", key="delcancel_" + rid):
            st.session_state.confirm_delete = None
            st.rerun()


# --- entry point ------------------------------------------------------------

def _reset_session_for(uid: str) -> None:
    """Clear per-user session state on sign-in / sign-out, so nothing from one
    user's session ever leaks into another's, and land on the timeline."""
    ss = st.session_state
    ss.session_records = []
    ss.session_originals = {}
    ss.uploads = 0
    ss.selected = None
    ss.editing = None
    ss.active_uid = uid
    ss.nav = "Timeline"


def _sign_in(user: auth.User) -> None:
    st.session_state.user = user.public()
    _reset_session_for(user.user_id)
    st.rerun()


def _login(c: dict) -> None:
    """Render the sign-in gate for the current mode and sign the user in.

    Real mode with no APP_PASSWORD is frictionless (auto sign-in). Demo mode
    shows the seeded profiles so a reviewer can switch between isolated archives.
    """
    mode = c["mode"]
    if mode == "real" and not auth.real_password_required():
        _sign_in(auth.real_user())
        return

    st.markdown("## 🩺 Medical Records Tracker")
    st.markdown("Turn messy, handwritten prescriptions and reports into a readable, searchable health record.")
    st.caption("🛈 " + guard.STANDING_NOTICE)
    st.divider()

    if mode == "real":
        st.subheader("Sign in")
        with st.form("login_real"):
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", type="primary"):
                user = auth.authenticate_real(pw)
                if user:
                    _sign_in(user)
                else:
                    st.error("Incorrect password.")
        return

    # Demo: choose an isolated profile.
    st.subheader("Choose a demo profile")
    st.caption(
        "A portfolio demo of per-user access control: each profile is a separate "
        "private archive, and one profile never sees another's records."
    )
    users = auth.demo_users()
    if not users:
        st.error("No demo profiles are configured.")
        return
    by_name = {u.name: u for u in users}
    pick = st.radio("Profile", list(by_name), index=0)
    with st.form("login_demo"):
        pw = st.text_input("Password", type="password")
        st.caption(
            "Demo passwords (fictional data): "
            + " · ".join(f"{u.name} = {u.password_hint}" for u in users if u.password_hint)
        )
        if st.form_submit_button("Enter", type="primary"):
            user = auth.authenticate_demo(by_name[pick].user_id, pw)
            if user:
                _sign_in(user)
            else:
                st.error("Incorrect password for that profile.")


def _sidebar(c: dict) -> None:
    items = list(NAV)
    if c["mode"] == "real":   # backup/restore is a personal-use, real-mode feature
        items = items + [("Data", "🗄  Data")]
    with st.sidebar:
        st.markdown("### 🩺 Medical Records Tracker")
        for key, label in items:
            active = st.session_state.nav == key
            if st.button(label, key="nav_" + key, use_container_width=True,
                         type="primary" if active else "secondary"):
                st.session_state.nav = key
                st.session_state.selected = None
                st.session_state.editing = None
                st.rerun()
        st.divider()
        _account_card(c)


def _account_card(c: dict) -> None:
    """Sidebar account card, plus a log-out control where signing out is meaningful."""
    user = st.session_state.user
    if c["mode"] == "real":
        st.success("**Local mode**")
        st.caption(f"Signed in as {user['name']}. Records are saved privately on this machine.")
    else:
        st.info(f"**Demo profile: {user['name']}**")
        st.caption(
            "You are viewing this profile's records only. Nothing here is visible "
            "to other profiles or visitors."
        )
    if c["mode"] == "demo" or auth.real_password_required():
        if st.button("Log out"):
            st.session_state.user = None
            _reset_session_for("")
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Medical Records Tracker", page_icon="🩺", layout="wide")
    bridge_secrets()
    st.markdown(_CSS, unsafe_allow_html=True)

    ss = st.session_state
    ss.setdefault("selected", None)
    ss.setdefault("editing", None)
    ss.setdefault("confirm_delete", None)
    ss.setdefault("uploads", 0)
    ss.setdefault("session_records", [])
    ss.setdefault("session_originals", {})
    ss.setdefault("user", None)
    ss.setdefault("active_uid", None)
    ss.setdefault("nav", "Timeline")

    c = cfg()

    # Identity gate: nothing renders until we know who this is.
    if not ss.user:
        _login(c)
        return

    store = store_for(c["mode"], ss.user["id"])
    _sidebar(c)

    st.markdown("#### 🩺 Medical Records Tracker")
    st.caption("Your messy medical records, made readable. 🛈 " + guard.STANDING_NOTICE)

    if ss.selected:
        rec = find_record(store, ss.selected)
        if rec:
            render_record_detail(store, rec, c)
            return
        ss.selected = None

    page = ss.nav
    if page == "Upload":
        page_upload(store, c)
    elif page == "Search":
        page_search(store, c)
    elif page == "Export":
        page_export(store, c)
    elif page == "Data" and c["mode"] == "real":
        page_data(store, c)
    else:
        page_timeline(store, c)


if __name__ == "__main__":
    main()
