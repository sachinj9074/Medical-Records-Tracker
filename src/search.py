"""Simple keyword and field search over stored records (v1, deterministic).

Search finds and reads back stored records and their already-checked
plain-language explanations; it never writes new medical content
(PROJECT_SPEC.md section 4). Every query passes through the medical-advice
guard first, so a judgment question ("is my sugar dangerous?") is refused before
any search runs, while a retrieval query ("show my diabetes records") proceeds.
When nothing matches, the answer is the honest NO_MATCH_MESSAGE, never general
medical knowledge (section 10).

Matching is literal per token: noise words are stripped from the query, and
every remaining content concept must appear (substring either way) somewhere in
a record's clinical fields. A concept is the query word plus any curated synonyms
for it (config/synonyms.json), so a lay word finds its clinical record: "diabetes"
also matches an HbA1c lab. The synonym map is human-reviewed and committed, never
model-generated; it broadens retrieval only and is never a medical claim. A wrong
entry can at worst surface an unrelated record, never fabricate a fact. Which
related terms actually matched is reported back (SearchResponse.expansions) so the
behaviour is legible. Search can still only find what extraction captured.

See PROJECT_SPEC.md sections 4, 5, 6, 10.
"""

from __future__ import annotations

import datetime
import functools
import json
import os
import re
from dataclasses import dataclass, field

from src import guard

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNONYMS_PATH = os.path.join(_REPO, "config", "synonyms.json")

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
    expansions: list = field(default_factory=list)  # synonym terms that produced a hit


# --- curated synonyms -------------------------------------------------------

def load_synonyms(path: str = SYNONYMS_PATH) -> list:
    """Load the curated synonym groups (list of lists of terms). Empty if missing."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    groups = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if isinstance(g, list):
            terms = [str(t).strip() for t in g if str(t).strip()]
            if terms:
                out.append(terms)
    return out


@functools.lru_cache(maxsize=1)
def _default_synonyms() -> tuple:
    # Cached and hashable (a tuple of tuples) so it is loaded once per process.
    return tuple(tuple(g) for g in load_synonyms())


def _concept_terms(qtoken: str, groups) -> list:
    """The other terms of every group the query word *exactly* belongs to.

    Activation is exact (the whole query word equals one of a term's tokens), so a
    partial word like "diabet" does not pull in a concept; but once activated, the
    concept's terms are matched against records with the usual substring rules.
    """
    out = []
    for g in groups:
        term_toks = [(_tokens(t), t) for t in g]
        if any(qtoken in toks for toks, _ in term_toks):
            for toks, term in term_toks:
                if toks and toks != [qtoken]:
                    out.append(term)
    seen, res = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            res.append(t)
    return res


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


def _best_term_match(term_tokens: list, fields: list):
    """(best field weight, matched labels) if every token of a term matches some
    field, else (None, empty). A multi-word term (for example "blood sugar") needs
    all its tokens present; single-word terms behave exactly as before."""
    labels: set = set()
    best = 0.0
    for t in term_tokens:
        tw = None
        for label, w, ftoks in fields:
            if _token_matches(t, ftoks):
                tw = w if tw is None else max(tw, w)
                labels.add(label)
        if tw is None:
            return None, set()
        best = max(best, tw)
    return best, labels


def _match_record(query_tokens: list, record: dict, concepts: dict):
    """AND across query concepts, OR within a concept (the word plus its synonyms).
    Returns (score, matched_fields, matched_synonyms) or None."""
    fields = [(label, w, _tokens(text)) for (label, w, text) in _fields(record)]
    matched_fields: set = set()
    matched_synonyms: set = set()
    score = 0.0
    for q in query_tokens:
        candidates = [([q], None)] + [(_tokens(t), t) for t in concepts.get(q, [])]
        best = 0.0
        found = False
        for term_tokens, source in candidates:
            if not term_tokens:
                continue
            w, labels = _best_term_match(term_tokens, fields)
            if w is not None:
                found = True
                best = max(best, w)
                matched_fields |= labels
                if source is not None:
                    matched_synonyms.add(source)
        if not found:
            return None
        score += best
    return score, sorted(matched_fields), sorted(matched_synonyms)


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


def search(query: str, records: list, *, document_type=None, date_from=None,
           date_to=None, synonyms=None) -> SearchResponse:
    """Guarded keyword search over stored records. See module docstring.

    `synonyms` overrides the curated map (a list of term groups); by default the
    committed config/synonyms.json is used.
    """
    q = (query or "").strip()

    verdict = guard.classify_question(q)
    if not verdict.allowed:
        return SearchResponse("refused", q, [], verdict.message)

    qtokens = _query_tokens(q)
    if not qtokens:
        return SearchResponse("empty", q, [], None)

    groups = _default_synonyms() if synonyms is None else synonyms
    concepts = {t: _concept_terms(t, groups) for t in qtokens}

    hits = []
    matched_synonyms: set = set()
    for r in records:
        if not _passes_filters(r, document_type, date_from, date_to):
            continue
        m = _match_record(qtokens, r, concepts)
        if m:
            hits.append(SearchHit(record=r, score=m[0], matched_fields=m[1]))
            matched_synonyms.update(m[2])

    if not hits:
        return SearchResponse("no_match", q, [], guard.NO_MATCH_MESSAGE)

    hits.sort(key=lambda h: (h.score, _recency_key(h.record)), reverse=True)
    return SearchResponse("ok", q, hits, None, expansions=sorted(matched_synonyms))
