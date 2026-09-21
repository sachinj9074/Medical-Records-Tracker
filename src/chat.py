"""Chat-style retrieval over a user's records (guarded, retrieval-only).

The safety property, and the reason an LLM is allowed here at all: the model
never sees a single record. Its only job is to turn a natural-language question
into a validated search intent (terms + filters + a result shape). Retrieval and
the answer are then fully deterministic (src/search.py + src/timeline.py), so the
model cannot interpret a value, invent a fact, leak data, or give advice. It can
only choose what to search for.

Every question passes the medical-advice guard first (guard.classify_question),
exactly as search does, so a judgment question is refused before any model call.
On any model or parse failure, the question degrades to a plain search of the raw
text. The deterministic "lead" line reports only counts and dates read straight
off the matched records; it never interprets them.

The model call is an injectable seam (`completer`), so this module is fully
unit-testable without the API. See PROJECT_SPEC.md sections 4, 5, 6, 10.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from src import guard, model, search, timeline

SHAPES = ("list", "latest", "count", "medications", "timeline")
_DOC_TYPES = ("prescription", "lab_report", "discharge_summary", "other")
_MAX_TERMS = 12

NOT_SEARCHABLE = (
    "I can only look things up in your records. Try asking about a medicine, a "
    "test, a condition, a doctor, or a date."
)

_SYSTEM = (
    "You convert a person's question about THEIR OWN medical records into a search "
    "query for a records app. You never answer medical questions, never give advice, "
    "interpretation, or a diagnosis, and never state any medical fact. You do not "
    "see the records; you only translate the question into search terms and a "
    "result shape.\n"
    'Return ONLY a JSON object with these keys: {"terms": [string], '
    '"document_type": string or null, "date_from": "YYYY-MM-DD" or null, '
    '"date_to": "YYYY-MM-DD" or null, "shape": one of '
    '"list"|"latest"|"count"|"medications"|"timeline"}.\n'
    "terms: the medicines, tests, conditions, providers, or keywords to search for, "
    "lowercase. Include obvious clinical equivalents of lay words (for 'sugar' add "
    "'glucose' and 'hba1c'; for a drug class like 'antibiotics' add common members). "
    "document_type: one of prescription, lab_report, discharge_summary, other, or "
    "null. shape: 'medications' for medicine questions, 'latest' for 'when did I "
    "last', 'count' for 'how many', 'timeline' for 'over time' or 'history', else "
    "'list'. If the question cannot be answered by searching records (small talk, "
    'advice, general knowledge), return {"terms": [], "shape": "list"}.'
)


@dataclass
class Intent:
    terms: list
    shape: str = "list"
    document_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None


@dataclass
class ChatAnswer:
    status: str            # "ok" | "refused" | "empty" | "no_match"
    question: str
    lead: str | None = None
    records: list = field(default_factory=list)     # matched records, for card rendering
    medicines: list = field(default_factory=list)   # MedHistory list, for the medications shape
    shape: str = "list"
    intent_terms: list = field(default_factory=list)
    expansions: list = field(default_factory=list)
    message: str | None = None


# --- intent parsing (the only place the model is used) ----------------------

def _user_prompt(question: str, history: list) -> str:
    lines = []
    if history:
        lines.append("Earlier questions in this conversation (for resolving references only):")
        lines += [f"- {q}" for q in history[-4:]]
        lines.append("")
    lines.append(f"Question: {question}")
    lines.append("Return the JSON query object now.")
    return "\n".join(lines)


def _valid_date(s):
    if not isinstance(s, str):
        return None
    try:
        datetime.date.fromisoformat(s)
        return s
    except ValueError:
        return None


def _validate_intent(raw) -> Intent:
    """Coerce the model's output onto the whitelist. Anything off-list is dropped,
    so a malformed or creative response can never widen what the app will do."""
    if not isinstance(raw, dict):
        raise ValueError("intent is not an object")
    raw_terms = raw.get("terms")
    terms = []
    if isinstance(raw_terms, list):
        for t in raw_terms:
            if isinstance(t, str) and t.strip():
                terms.append(t.strip().lower())
    shape = raw.get("shape") if raw.get("shape") in SHAPES else "list"
    dtype = raw.get("document_type") if raw.get("document_type") in _DOC_TYPES else None
    return Intent(terms=terms[:_MAX_TERMS], shape=shape, document_type=dtype,
                  date_from=_valid_date(raw.get("date_from")), date_to=_valid_date(raw.get("date_to")))


def _default_completer(system: str, user: str) -> dict:
    return model.complete_json(system=system, user=user, tier="fast", max_tokens=600)


def parse_intent(question: str, history=None, *, completer=None) -> Intent:
    """Turn a question into a validated search Intent. On any failure, degrade to a
    literal search of the question (the same tokens plain search would use)."""
    completer = completer or _default_completer
    try:
        return _validate_intent(completer(_SYSTEM, _user_prompt(question, history or [])))
    except Exception:
        return Intent(terms=search._query_tokens(question or ""), shape="list")


# --- deterministic answer rendering -----------------------------------------

def _latest(records: list):
    dated = [r for r in records if isinstance(r.get("record_date"), str)]
    return max(dated, key=lambda r: r["record_date"]) if dated else None


def _span(records: list) -> str:
    dates = sorted(r["record_date"] for r in records if isinstance(r.get("record_date"), str))
    if not dates:
        return ""
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _render(ans: ChatAnswer, records: list) -> None:
    ans.records = records
    if ans.shape == "medications":
        ans.medicines = timeline.build_medication_history(records)
        ans.lead = f"{_plural(len(ans.medicines), 'medicine')} across {_plural(len(records), 'record')}."
    elif ans.shape == "count":
        ans.lead = f"{_plural(len(records), 'record')} match."
    elif ans.shape == "latest":
        latest = _latest(records)
        if latest:
            prov = (latest.get("provider") or {}).get("name")
            ans.lead = f"Most recent: {latest['record_date']}" + (f", {prov}" if prov else "") + "."
            ans.records = [latest] + [r for r in records if r is not latest]
        else:
            ans.lead = f"{_plural(len(records), 'record')} match, none dated."
    elif ans.shape == "timeline":
        span = _span(records)
        ans.lead = f"{_plural(len(records), 'record')}" + (f", {span}" if span else "") + "."
    else:  # list
        ans.lead = f"{_plural(len(records), 'record')} match."


def answer(question: str, records: list, *, history=None, completer=None) -> ChatAnswer:
    """Answer a chat question from the user's records: guard, understand, retrieve,
    render. The model (via `completer`) only ever produces the search intent.

    The intent's terms are alternatives (OR): a question like "what antibiotics
    have I had" lists records matching any of them. Each term is searched on its
    own (so search's guard and synonyms apply per term) and the matches are unioned
    and re-ranked by score then recency.
    """
    q = (question or "").strip()

    verdict = guard.classify_question(q)   # deterministic, before any model call
    if not verdict.allowed:
        return ChatAnswer("refused", q, message=verdict.message)

    intent = parse_intent(q, history, completer=completer)
    if not intent.terms:
        return ChatAnswer("empty", q, message=NOT_SEARCHABLE, shape=intent.shape)

    best: dict = {}          # record_id -> (score, record)
    expansions: set = set()
    for term in intent.terms:
        resp = search.search(term, records, document_type=intent.document_type,
                             date_from=intent.date_from, date_to=intent.date_to)
        if resp.status == "refused":
            return ChatAnswer("refused", q, message=resp.message)
        if resp.status == "ok":
            expansions.update(resp.expansions)
            for h in resp.hits:
                rid = h.record.get("record_id") or id(h.record)
                if rid not in best or h.score > best[rid][0]:
                    best[rid] = (h.score, h.record)

    if not best:
        return ChatAnswer("no_match", q, message=guard.NO_MATCH_MESSAGE,
                          shape=intent.shape, intent_terms=intent.terms)

    ranked = sorted(best.values(), key=lambda t: (t[0], search._recency_key(t[1])), reverse=True)
    ans = ChatAnswer("ok", q, shape=intent.shape, intent_terms=intent.terms,
                     expansions=sorted(expansions))
    _render(ans, [r for _, r in ranked])
    return ans
