"""Doctor-ready summary export (deterministic, no model).

Assembles stored records into a Markdown summary a patient can hand to a
clinician. Verbatim and structured facts only (diagnosis as stated, medications
with dosing, investigations with printed flags, advice, follow-up); the
patient-facing plain-language explanations are omitted, since a clinician wants
the record, not the lay gloss. No interpretation, no recommendation, no new
sentences: it is a historical record, never current advice. Lab flags are shown
exactly as stored and never computed (PROJECT_SPEC.md sections 8 and 10).

Records marked needs_review carry an "(unverified extraction)" note, and the
summary states plainly that the original scans are the source of truth.

Pure string assembly: the app handles saving the result. See PROJECT_SPEC.md
sections 5, 8, 10, 15.
"""

from __future__ import annotations

import datetime

from src import guard, timeline


def _val(x):
    return x if isinstance(x, str) and x.strip() else None


def _join(parts, sep):
    return sep.join(p for p in parts if _val(p))


def _parse(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


# --- selection helpers (the app chooses what to export) ---------------------

def filter_by_date_range(records: list, date_from=None, date_to=None) -> list:
    lo, hi = _parse(date_from), _parse(date_to)
    out = []
    for r in records:
        d = _parse(_val(r.get("record_date")))
        if d is None:
            continue  # an undated record cannot be placed in a date window
        if lo and d < lo:
            continue
        if hi and d > hi:
            continue
        out.append(r)
    return out


def filter_by_episode(records: list, episode_id: str) -> list:
    return [r for r in records if r.get("episode_id") == episode_id]


# --- header -----------------------------------------------------------------

def _derive_patient(records: list) -> str:
    best = None
    for r in records:
        p = r.get("patient") or {}
        if _val(p.get("name")):
            key = _val(r.get("record_date")) or ""
            if best is None or key > best[0]:
                best = (key, p)
    if best is None:
        return ""
    p = best[1]
    extra = []
    if _val(p.get("sex")):
        extra.append(str(p["sex"]))
    if p.get("age") is not None:
        extra.append(str(p["age"]))
    return p["name"] + (f" ({', '.join(extra)})" if extra else "")


def _date_range(records: list) -> str:
    dates = sorted(d for r in records if (d := _val(r.get("record_date"))))
    if not dates:
        return ""
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"


# --- record rendering -------------------------------------------------------

def _med_line(m: dict) -> str:
    name = _val(m.get("name")) or "(unnamed)"
    head = " ".join(x for x in [name, _val(m.get("strength")),
                                f"({_val(m.get('form'))})" if _val(m.get("form")) else None] if x)
    dosing = _join([m.get("dose"), m.get("frequency"), m.get("duration")], ", ")
    return head + (f": {dosing}" if dosing else "")


def _inv_line(inv: dict) -> str:
    parts = [_val(inv.get("name")) or "(unnamed)"]
    vu = " ".join(x for x in [_val(inv.get("value")), _val(inv.get("unit"))] if x)
    if vu:
        parts.append(f"= {vu}")
    if inv.get("flag") in ("high", "low", "normal"):  # unknown => no flag; never computed
        parts.append(f"[{inv['flag'].upper()}]")
    line = " ".join(parts)
    ref = _val(inv.get("reference_range"))
    return line + (f" (ref {ref})" if ref else "")


def _render_record(rec: dict) -> list:
    date = _val(rec.get("record_date")) or "Undated"
    dtype = (_val(rec.get("document_type")) or "record").replace("_", " ")
    caveat = " _(unverified extraction)_" if rec.get("needs_review") == "Y" else ""
    out = [f"### {date} · {dtype}{caveat}"]

    prov = rec.get("provider") or {}
    provline = _join([prov.get("name"), prov.get("specialty"), prov.get("clinic")], " · ")
    if provline:
        out.append(f"- **Provider:** {provline}")

    dx = (rec.get("diagnosis") or {}).get("stated_text")
    if _val(dx):
        out.append(f"- **Diagnosis (as stated):** {dx}")

    meds = rec.get("medications") or []
    if meds:
        out.append("- **Medications:**")
        out.extend(f"    - {_med_line(m)}" for m in meds)

    invs = rec.get("investigations") or []
    if invs:
        out.append("- **Investigations:**")
        out.extend(f"    - {_inv_line(i)}" for i in invs)

    if _val(rec.get("advice_verbatim")):
        out.append(f"- **Advice (verbatim):** {rec['advice_verbatim']}")
    if _val(rec.get("follow_up")):
        out.append(f"- **Follow-up:** {rec['follow_up']}")
    if _val(rec.get("source_filename")):
        out.append(f"- **Original on file:** {rec['source_filename']}")
    return out


# --- summary ----------------------------------------------------------------

def render_summary(records: list, *, patient: str | None = None, title: str | None = None) -> str:
    """Render a Markdown doctor-ready summary of the given records."""
    lines = [f"# {title or 'Medical records summary'}", ""]

    who = patient or _derive_patient(records)
    if who:
        lines.append(f"**Patient:** {who}  ")
    dr = _date_range(records)
    if dr:
        lines.append(f"**Period:** {dr}  ")
    lines.append(f"**Records:** {len(records)}  ")
    lines.append(f"**Generated:** {datetime.date.today().isoformat()}  ")
    lines.append("")
    lines.append(
        f"_{guard.STANDING_NOTICE} Digitised from the patient's own documents; the "
        "original scans are the source of truth. A record tagged unverified was a "
        "low-confidence read; check it against the original scan._"
    )
    lines.append("")

    if not records:
        lines.append("No records to summarise.")
        return "\n".join(lines) + "\n"

    for ep in timeline.build_timeline(records):
        lines.append(f"## {ep.label}")
        lines.append("")
        for rec in ep.records:
            lines.extend(_render_record(rec))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
