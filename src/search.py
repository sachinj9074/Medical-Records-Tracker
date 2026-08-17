"""Simple keyword and field search over stored records (v1, deterministic).

Search finds and reads back stored records and their already-checked
plain-language explanations; it never writes new medical content
(PROJECT_SPEC.md section 4). Every query passes through the medical-advice
guard first, so a judgment question ("is my sugar dangerous?") is refused before
any search runs, while a retrieval query ("show my diabetes records") proceeds.
When nothing matches, the answer is the honest NO_MATCH_MESSAGE, never general
medical knowledge (section 10).

Matching is literal: noise words are stripped from the query, and every
remaining content token must appear (substring either way) somewhere in a
record's clinical fields. There is no synonym or concept expansion, so "hba1c"
finds the lab but "diabetes" does not; and search can only find what extraction
captured. Stemming and synonyms are a later enhancement.

See PROJECT_SPEC.md sections 4, 5, 6, 10.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

from src import guard

# Grammatical filler and generic verbs that carry no record content. Stripped
# from the query so natural phrasing ("show my diabetes records") reduces to its
# content tokens ("diabetes"). Kept deliberately small so real terms survive.
_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "and", "or", "with",
    "my", "me", "mine", "our", "i", "you", "your",
    "what", "which", "that", "this", "when", "was", "were", "is", "are", "be",
    "did", "do", "does", "have", "has", "had",
    "show", "find", "see", "view", "list", "get", "got", "give", "given",
    "all", "any", "from", "please", "want", "need", "look", "up",
    "record", "records",
}

_PRIMARY = 2.0
_SECONDARY = 1.0


@dataclass
class SearchHit:
    record: dict
    score: float
    matched_fields: list = field(default_factory=list)


@dataclass
class SearchResponse:
    status: str            # "ok" | "refused" | "empty" | "no_match"
    query: str
    hits: list = field(default_factory=list)
    message: str | None = None


def _tokens(text) -> list:
    if not isinstance(text, str):
        return []
    return [t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if t]


def _query_tokens(query: str) -> list:
    return [t for t in _tokens(query) if t not in _STOPWORDS]


def _parse_date(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _fields(record: dict) -> list:
    """The searchable clinical fields as (label, weight, text). Internal fields
    (record_id, flags, needs_review, episode_id, filenames) are excluded."""
    out = []
    p = record.get("provider") or {}
    out.append(("provider.name", _PRIMARY, p.get("name")))
    out.append(("provider.clinic", _SECONDARY, p.get("clinic")))
    out.append(("provider.specialty", _SECONDARY, p.get("specialty")))

    d = record.get("diagnosis") or {}
    out.append(("diagnosis.stated_text", _PRIMARY, d.get("stated_text")))
    out.append(("diagnosis.plain_language", _SECONDARY, d.get("plain_language")))

    for i, m in enumerate(record.get("medications") or []):
        out.append((f"medications[{i}].name", _PRIMARY, m.get("name")))
        for k in ("strength", "form", "dose", "frequency", "duration", "purpose_plain"):
            out.append((f"medications[{i}].{k}", _SECONDARY, m.get(k)))

    for i, inv in enumerate(record.get("investigations") or []):
        out.append((f"investigations[{i}].name", _PRIMARY, inv.get("name")))
        for k in ("value", "unit", "reference_range", "flag", "plain_note"):
            out.append((f"investigations[{i}].{k}", _SECONDARY, inv.get(k)))

    out.append(("advice_verbatim", _SECONDARY, record.get("advice_verbatim")))
    out.append(("follow_up", _SECONDARY, record.get("follow_up")))
    out.append(("document_type", _SECONDARY, record.get("document_type")))
    out.append(("record_date", _SECONDARY, record.get("record_date")))

    return [(label, w, text) for (label, w, text) in out if isinstance(text, str) and text.strip()]


def _token_matches(q: str, field_tokens: list) -> bool:
    """Exact token match, or a substring either way when both parts are long
    enough (>= 4 chars) to be meaningful. The length floor keeps a one-letter
    record token (for example "a" in "A & C OF DM") from matching inside a
    longer query word like "eczema"."""
    for t in field_tokens:
        if q == t:
            return True
        if len(q) >= 4 and q in t:
            return True
        if len(t) >= 4 and t in q:
            return True
    return False


def _match_record(query_tokens: list, record: dict):
    """AND across query tokens. Returns (score, matched_fields) or None."""
    fields = [(label, w, _tokens(text)) for (label, w, text) in _fields(record)]
    matched_fields: set = set()
    score = 0.0
    for q in query_tokens:
        best = 0.0
        found = False
        for label, w, toks in fields:
            if _token_matches(q, toks):
                found = True
                matched_fields.add(label)
                best = max(best, w)
        if not found:
            return None
        score += best
    return score, sorted(matched_fields)


def _passes_filters(record: dict, document_type, date_from, date_to) -> bool:
    if document_type and record.get("document_type") != document_type:
        return False
    if date_from or date_to:
        pd = _parse_date(record.get("record_date"))
        if pd is None:
            return False  # an undated record cannot satisfy a date window
        lo, hi = _parse_date(date_from), _parse_date(date_to)
        if lo and pd < lo:
            return False
        if hi and pd > hi:
            return False
    return True


def _recency_key(record: dict) -> str:
    d = record.get("record_date")
    return d if isinstance(d, str) else ""


def search(query: str, records: list, *, document_type=None, date_from=None, date_to=None) -> SearchResponse:
    """Guarded keyword search over stored records. See module docstring."""
    q = (query or "").strip()

    verdict = guard.classify_question(q)
    if not verdict.allowed:
        return SearchResponse("refused", q, [], verdict.message)

    qtokens = _query_tokens(q)
    if not qtokens:
        return SearchResponse("empty", q, [], None)

    hits = []
    for r in records:
        if not _passes_filters(r, document_type, date_from, date_to):
            continue
        m = _match_record(qtokens, r)
        if m:
            hits.append(SearchHit(record=r, score=m[0], matched_fields=m[1]))

    if not hits:
        return SearchResponse("no_match", q, [], guard.NO_MATCH_MESSAGE)

    hits.sort(key=lambda h: (h.score, _recency_key(h.record)), reverse=True)
    return SearchResponse("ok", q, hits, None)
