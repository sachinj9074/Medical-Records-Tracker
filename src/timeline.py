"""Episode clustering and the longitudinal timeline (deterministic, no model).

Records are grouped into episodes so the timeline reads as coherent visits
rather than scattered pages (PROJECT_SPEC.md section 9). Two records join the
same episode when they are both clinically related and temporally proximate:

  related    = same provider (name or clinic), OR a shared medication, OR a
               shared diagnosis keyword. All literal string matches: no medical
               concept map, so a comorbidity mentioned on an unrelated visit
               (for example "Diabetic type-1" noted on a skin prescription)
               never drags that visit into a diabetes episode.
  proximate  = record dates within GAP_DAYS of each other.

Cross-condition recall (for example "show all my diabetes records" across
different providers) is search's job, not clustering's; episodes stay tight.

Clustering is stable: a component that already contains an episode_id keeps it,
so adding records does not reshuffle ids. Manual link/split (section 9) needs
the app UI and is deferred to the app step.

See PROJECT_SPEC.md sections 8, 9, 15.
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass, field

GAP_DAYS = 120

_FORM_UNIT = {
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules",
    "inj", "injection", "syp", "syrup", "cream", "ointment", "oint", "gel",
    "lotion", "drops", "drop", "susp", "suspension", "serum", "wash",
    "mg", "ml", "mcg", "g", "gm", "iu", "units", "unit",
}
_DIAG_STOP = {
    "the", "and", "of", "for", "with", "type", "chronic", "acute", "left",
    "right", "stage", "grade", "disease", "syndrome", "disorder", "since",
}


# --- normalisation ----------------------------------------------------------

def _norm(s) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _parse_date(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _provider_keys(record: dict) -> set:
    p = record.get("provider") or {}
    keys = set()
    for k in ("name", "clinic"):
        v = _norm(p.get(k))
        if len(v) >= 3:
            keys.add(v)
    return keys


def _med_key(name) -> str:
    toks = [
        t for t in _norm(name).split()
        if t and t not in _FORM_UNIT and not t.replace(".", "").isdigit()
    ]
    return " ".join(toks)


def _med_keys(record: dict) -> set:
    out = set()
    for m in record.get("medications") or []:
        k = _med_key(m.get("name"))
        if k:
            out.add(k)
    return out


def _diag_tokens(record: dict) -> set:
    d = (record.get("diagnosis") or {}).get("stated_text")
    return {
        t for t in _norm(d).split()
        if len(t) >= 4 and t not in _DIAG_STOP and not t.isdigit()
    }


def _features(record: dict) -> dict:
    return {
        "prov": _provider_keys(record),
        "med": _med_keys(record),
        "diag": _diag_tokens(record),
        "date": _parse_date(record.get("record_date")),
    }


def _linked(a: dict, b: dict) -> bool:
    related = bool(a["prov"] & b["prov"]) or bool(a["med"] & b["med"]) or bool(a["diag"] & b["diag"])
    if not related:
        return False
    if a["date"] is None or b["date"] is None:
        return True  # relatedness-only fallback when a date is missing
    return abs((a["date"] - b["date"]).days) <= GAP_DAYS


# --- union-find -------------------------------------------------------------

def _find(parent: list, i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list, a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def new_episode_id() -> str:
    return "ep_" + uuid.uuid4().hex[:12]


def _sort_key(record: dict):
    d = _parse_date(record.get("record_date"))
    return (d or datetime.date.max, record.get("date_processed") or "", record.get("record_id") or "")


def _choose_episode_id(member_records: list) -> str:
    """Keep the id seeded by the earliest member; mint one if none has an id yet."""
    for r in sorted(member_records, key=_sort_key):
        if r.get("episode_id"):
            return r["episode_id"]
    return new_episode_id()


def assign_episodes(records: list) -> list:
    """Assign episode_id to each record in place, returning the same list."""
    n = len(records)
    parent = list(range(n))
    feats = [_features(r) for r in records]
    for i in range(n):
        for j in range(i + 1, n):
            if _linked(feats[i], feats[j]):
                _union(parent, i, j)

    comps: dict[int, list] = {}
    for i in range(n):
        comps.setdefault(_find(parent, i), []).append(i)
    for members in comps.values():
        member_records = [records[i] for i in members]
        eid = _choose_episode_id(member_records)
        for r in member_records:
            r["episode_id"] = eid
    return records


def order_records(records: list) -> list:
    """Oldest first; records with no readable date sort last."""
    return sorted(records, key=_sort_key)


# --- timeline view ----------------------------------------------------------

@dataclass
class Episode:
    episode_id: str
    label: str
    start_date: str | None
    end_date: str | None
    providers: list = field(default_factory=list)
    record_ids: list = field(default_factory=list)
    records: list = field(default_factory=list)
    count: int = 0


def _primary_provider(members_sorted: list) -> str:
    for r in reversed(members_sorted):  # most recent first
        name = (r.get("provider") or {}).get("name")
        if name:
            return name
    for r in reversed(members_sorted):
        clinic = (r.get("provider") or {}).get("clinic")
        if clinic:
            return clinic
    return "Unknown provider"


def _distinct_providers(members: list) -> list:
    out = []
    for r in members:
        p = r.get("provider") or {}
        who = p.get("name") or p.get("clinic")
        if who and who not in out:
            out.append(who)
    return out


def _month(iso: str | None) -> str:
    d = _parse_date(iso)
    return d.strftime("%b %Y") if d else ""


def _date_range_label(start: str | None, end: str | None) -> str:
    if not start and not end:
        return ""
    if not end or start == end:
        return _month(start)
    sm, em = _month(start), _month(end)
    return sm if sm == em else f"{sm} to {em}"


def _episode_label(members_sorted: list, start: str | None, end: str | None) -> str:
    n = len(members_sorted)
    parts = [_primary_provider(members_sorted), f"{n} record{'s' if n != 1 else ''}"]
    dr = _date_range_label(start, end)
    if dr:
        parts.append(dr)
    return " · ".join(parts)


def _build_episode(members_sorted: list) -> Episode:
    dates = [d for d in (_parse_date(r.get("record_date")) for r in members_sorted) if d]
    start = min(dates).isoformat() if dates else None
    end = max(dates).isoformat() if dates else None
    eid = next((r.get("episode_id") for r in members_sorted if r.get("episode_id")), None) \
        or (members_sorted[0].get("record_id") or "")
    return Episode(
        episode_id=eid,
        label=_episode_label(members_sorted, start, end),
        start_date=start,
        end_date=end,
        providers=_distinct_providers(members_sorted),
        record_ids=[r.get("record_id") for r in members_sorted],
        records=members_sorted,
        count=len(members_sorted),
    )


def build_timeline(records: list) -> list:
    """Group records into Episodes, each ordered oldest-first, episodes ordered
    by most recent activity (newest first). Expects episode_id already assigned;
    a record without one is treated as its own singleton episode.
    """
    groups: dict[str, list] = {}
    for r in records:
        key = r.get("episode_id") or ("singleton:" + (r.get("record_id") or ""))
        groups.setdefault(key, []).append(r)

    episodes = [_build_episode(order_records(members)) for members in groups.values()]
    episodes.sort(key=lambda e: (e.end_date or "0000-00-00"), reverse=True)
    return episodes


# --- store glue -------------------------------------------------------------

def recluster(store) -> int:
    """Re-cluster every stored record and re-save those whose episode_id changed.

    Returns the number of records updated. Called after ingestion.
    """
    records = store.list()
    before = {r.get("record_id"): r.get("episode_id") for r in records}
    assign_episodes(records)
    changed = 0
    for r in records:
        if r.get("episode_id") != before.get(r.get("record_id")):
            store.save(r)  # upsert; original already retained
            changed += 1
    return changed
