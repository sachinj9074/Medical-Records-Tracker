"""Plain-language explanations, authored once at ingestion under a fidelity guard.

Extraction leaves three fields null on purpose; this module fills them:
diagnosis.plain_language, each medications[].purpose_plain, and each
investigations[].plain_note. The safety property (PROJECT_SPEC.md section 11) is
that these add no claim absent from the source: general facts about a named
condition, drug, or test are allowed; any claim about THIS patient (severity,
cause, prognosis, what to do, or interpretation of a value) is not.

Design: author -> guard -> gate.
  - Author (judgment tier) is given only the NAMES of things, never the patient's
    values, flags, or dates, so it structurally cannot interpret them. It writes
    one short, general, third-person sentence per term, or null when nothing safe
    can be said.
  - Guard is two layers. A deterministic backstop (no second person, no new
    numbers, a length cap, no explicit directive) catches the mechanical leaks;
    an independent, adversarial judge (judgment tier) that sees the full record
    catches the semantic ones (a hallucinated entity, a patient-specific claim,
    an interpreted value).
  - Gate: a field that fails is re-authored once with the objection; if it still
    fails it is withheld (left null) and flagged explanation_withheld:<field>.
    validate.py turns that flag into a needs_review reason. The worst case is a
    missing explanation, never a wrong one.

The explanation is faithful to the extracted record, not a fresh read of the
image; explain.py makes no image call.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field

from src import model

# --- deterministic backstop -------------------------------------------------

_MAX_CHARS = 300
_MAX_SENTENCES = 2
_SECOND_PERSON = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)
_DIRECTIVE = re.compile(
    r"\b(you should|please|consult|see (a|your) doctor|seek|talk to your|"
    r"call your|take (it|this|them)|stop taking|start taking)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[.!?]+", text) if s.strip()]


def _numbers_in(*texts) -> set[str]:
    out: set[str] = set()
    for t in texts:
        if t:
            out |= set(_NUMBER.findall(t))
    return out


def deterministic_violations(text: str | None, allowed_numbers: set[str]) -> list[str]:
    """Mechanical fidelity checks that need no model. Empty list means clean.

    These are the low-false-positive rules (second person, new numbers, length,
    explicit directives). Semantic checks (hallucinated entity, interpreted
    value) are the judge's job, not here.
    """
    if not text:
        return []
    v: list[str] = []
    if len(text) > _MAX_CHARS:
        v.append("too_long")
    if len(_sentences(text)) > _MAX_SENTENCES:
        v.append("too_many_sentences")
    if _SECOND_PERSON.search(text):
        v.append("second_person")
    if _DIRECTIVE.search(text):
        v.append("directive")
    extra = set(_NUMBER.findall(text)) - set(allowed_numbers)
    if extra:
        v.append("introduces_number:" + ",".join(sorted(extra)))
    return v


# --- prompts ----------------------------------------------------------------

AUTHOR_SYSTEM = """You write short, plain-language readings of medical terms already extracted from a document, to help a lay reader understand what a word means. You are given only the names of things: a diagnosis term, medicine names, and test names. You are not given the patient's values, results, dates, or any flags.

Rules:
- Explain only the named term, in general words a non-expert can follow. One sentence, two at most. Keep it short.
- Allowed: what a named condition is; what a named medicine is and what it is generally used for; what a named test generally measures.
- Forbidden: anything about this particular patient. No severity, no cause, no prognosis, no advice, no interpretation of any result, no normal ranges, no telling anyone to do anything.
- Never address the reader. Do not use "you" or "your". Write in general, third-person terms.
- Do not introduce any number (a dose, threshold, range, or percentage) that is not already part of the term you were given.
- Do not name any condition, medicine, or test that you were not given.
- If there is nothing general and safe to say about a term, return null for it. Silence is correct when in doubt.
"""

JUDGE_SYSTEM = """You are a strict fidelity checker for a medical record tool. Its one rule: an explanation may state general facts about a named term, but must never make a claim about this particular patient that is not written in the record.

You are given the record's actual facts and a proposed plain-language explanation for each term. For each item, decide "pass" or "fail".

Fail the item if the explanation does any of these:
- Names a condition, medicine, or test that is not in the record (a hallucinated entity).
- Says anything about this patient's situation: severity, cause, prognosis, what will happen, or what to do.
- Interprets a result or value: calls it high, low, normal, abnormal, or controlled, compares it to a range, or states a normal range or threshold. The explanation was written without access to the values, so any such claim is an invention.
- Addresses the reader ("you" or "your") or gives an instruction.
- Introduces a number that is not part of the term it explains.

Pass the item only if the explanation is a correct, general statement about the named term with none of the above. If in doubt, fail it.
"""

_AUTHOR_SHAPE = (
    'Return ONLY this JSON object, no prose:\n'
    '{\n'
    '  "diagnosis_plain": <string or null>,\n'
    '  "medications": [ {"index": <int>, "purpose_plain": <string or null>} ],\n'
    '  "investigations": [ {"index": <int>, "plain_note": <string or null>} ]\n'
    '}\n'
    'Give one medications entry per medicine index and one investigations entry per test index shown above.'
)


# --- report -----------------------------------------------------------------

@dataclass
class FieldOutcome:
    field: str            # "diagnosis" | "medications[0]" | "investigations[1]"
    status: str           # "authored" | "empty" | "withheld"
    reason: str | None = None


@dataclass
class ExplainReport:
    outcomes: list = field(default_factory=list)
    retries_used: int = 0
    withheld: list = field(default_factory=list)


# --- entity payload (what the author is allowed to see) ---------------------

def _entities(record: dict) -> dict:
    dx = (record.get("diagnosis") or {}).get("stated_text")
    meds = [
        {"index": i, "name": m.get("name"), "form": m.get("form")}
        for i, m in enumerate(record.get("medications") or [])
    ]
    invs = [
        {"index": i, "name": inv.get("name")}
        for i, inv in enumerate(record.get("investigations") or [])
    ]
    return {"diagnosis_stated_text": dx, "medications": meds, "investigations": invs}


# --- author -----------------------------------------------------------------

def _normalize_author(raw, entities) -> dict:
    """Coerce the author's JSON into {diagnosis, med:{i}, inv:{i}} with safe defaults."""
    prop = {"diagnosis": None, "med": {}, "inv": {}}

    def _clean(x):
        return x if isinstance(x, str) and x.strip() else None

    if isinstance(raw, dict):
        prop["diagnosis"] = _clean(raw.get("diagnosis_plain"))
        for item in raw.get("medications") or []:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                prop["med"][item["index"]] = _clean(item.get("purpose_plain"))
        for item in raw.get("investigations") or []:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                prop["inv"][item["index"]] = _clean(item.get("plain_note"))

    if entities["diagnosis_stated_text"] is None:
        prop["diagnosis"] = None
    for m in entities["medications"]:
        prop["med"].setdefault(m["index"], None)
    for inv in entities["investigations"]:
        prop["inv"].setdefault(inv["index"], None)
    return prop


def _author(entities: dict, tier: str, feedback: str | None = None) -> dict:
    payload = {
        "diagnosis_stated_text": entities["diagnosis_stated_text"],
        "medications": entities["medications"],
        "investigations": entities["investigations"],
    }
    user = "Terms extracted from the document:\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n\n" + _AUTHOR_SHAPE
    if feedback:
        user += (
            "\n\nA previous attempt was rejected. Rewrite the offending fields as short, "
            "general, third-person statements about the term only, and return corrected JSON:\n"
            + feedback
        )
    raw = model.complete_json(system=AUTHOR_SYSTEM, user=user, tier=tier)
    return _normalize_author(raw, entities)


# --- guard ------------------------------------------------------------------

def _deterministic_fails(proposals: dict, entities: dict) -> dict:
    fails: dict[str, str] = {}
    if proposals["diagnosis"]:
        v = deterministic_violations(proposals["diagnosis"], _numbers_in(entities["diagnosis_stated_text"]))
        if v:
            fails["diagnosis"] = "deterministic:" + ";".join(v)
    for m in entities["medications"]:
        i = m["index"]
        t = proposals["med"].get(i)
        if t:
            v = deterministic_violations(t, _numbers_in(m.get("name"), m.get("form")))
            if v:
                fails[f"med:{i}"] = "deterministic:" + ";".join(v)
    for inv in entities["investigations"]:
        i = inv["index"]
        t = proposals["inv"].get(i)
        if t:
            v = deterministic_violations(t, _numbers_in(inv.get("name")))
            if v:
                fails[f"inv:{i}"] = "deterministic:" + ";".join(v)
    return fails


def _mask(proposals: dict, exclude: set) -> dict:
    """A copy of proposals with excluded keys nulled, so the judge skips them."""
    out = {"diagnosis": None if "diagnosis" in exclude else proposals["diagnosis"], "med": {}, "inv": {}}
    for i, t in proposals["med"].items():
        out["med"][i] = None if f"med:{i}" in exclude else t
    for i, t in proposals["inv"].items():
        out["inv"][i] = None if f"inv:{i}" in exclude else t
    return out


def _judge(record: dict, proposals: dict, entities: dict, tier: str) -> dict:
    """Ask the judge to verify non-null proposals. Returns {key: reason} for fails.

    Fails safe: any judged item without an explicit "pass" is treated as a fail.
    """
    items = []
    if proposals["diagnosis"]:
        items.append({
            "key": "diagnosis",
            "record_fact": {"stated_text": entities["diagnosis_stated_text"]},
            "proposed": proposals["diagnosis"],
        })
    meds = record.get("medications") or []
    for i, t in proposals["med"].items():
        if t:
            m = meds[i] if i < len(meds) else {}
            items.append({
                "key": f"med:{i}",
                "record_fact": {"name": m.get("name"), "strength": m.get("strength"), "form": m.get("form")},
                "proposed": t,
            })
    invs = record.get("investigations") or []
    for i, t in proposals["inv"].items():
        if t:
            iv = invs[i] if i < len(invs) else {}
            items.append({
                "key": f"inv:{i}",
                "record_fact": {
                    "name": iv.get("name"), "value": iv.get("value"), "unit": iv.get("unit"),
                    "reference_range": iv.get("reference_range"), "flag": iv.get("flag"),
                },
                "proposed": t,
            })
    if not items:
        return {}

    user = (
        "Check each proposed explanation against the record facts.\n\n"
        + json.dumps({"items": items}, indent=2, ensure_ascii=False)
        + '\n\nReturn ONLY: {"verdicts": [{"key": <string>, "verdict": "pass" | "fail", '
        '"reason": <string>}]} with one entry per item key.'
    )
    raw = model.complete_json(system=JUDGE_SYSTEM, user=user, tier=tier)

    fails: dict[str, str] = {}
    seen = set()
    verdicts = raw.get("verdicts") if isinstance(raw, dict) else None
    if isinstance(verdicts, list):
        for vd in verdicts:
            if isinstance(vd, dict):
                k = vd.get("key")
                seen.add(k)
                if str(vd.get("verdict", "")).lower() != "pass":
                    fails[k] = "judge:" + str(vd.get("reason", "failed"))
    for it in items:  # fail safe on any missing verdict
        if it["key"] not in seen:
            fails[it["key"]] = "judge:no_verdict"
    return fails


def _adopt_failing(old: dict, new: dict, keys) -> dict:
    out = {"diagnosis": old["diagnosis"], "med": dict(old["med"]), "inv": dict(old["inv"])}
    for k in keys:
        if k == "diagnosis":
            out["diagnosis"] = new["diagnosis"]
        elif k.startswith("med:"):
            i = int(k.split(":")[1])
            out["med"][i] = new["med"].get(i)
        elif k.startswith("inv:"):
            i = int(k.split(":")[1])
            out["inv"][i] = new["inv"].get(i)
    return out


def _feedback(fails: dict) -> str:
    return "\n".join(f"- {k}: {r}" for k, r in fails.items())


# --- apply ------------------------------------------------------------------

def _add_flag(flags: list, flag: str) -> None:
    if flag not in flags:
        flags.append(flag)


def _apply(record: dict, proposals: dict, entities: dict, withheld: set, reasons: dict, retries_used: int) -> ExplainReport:
    outcomes = []
    flags = record.setdefault("flags", [])

    def _write(container_ok, set_value, key, label, value):
        if not container_ok:
            return
        if key in withheld:
            set_value(None)
            _add_flag(flags, f"explanation_withheld:{label}")
            outcomes.append(FieldOutcome(label, "withheld", reasons.get(key)))
        else:
            set_value(value)
            outcomes.append(FieldOutcome(label, "authored" if value else "empty"))

    if entities["diagnosis_stated_text"] is not None:
        _write(True, lambda v: record["diagnosis"].__setitem__("plain_language", v),
               "diagnosis", "diagnosis", proposals["diagnosis"])

    meds = record.get("medications") or []
    for m in entities["medications"]:
        i = m["index"]
        _write(i < len(meds), lambda v, i=i: meds[i].__setitem__("purpose_plain", v),
               f"med:{i}", f"medications[{i}]", proposals["med"].get(i))

    invs = record.get("investigations") or []
    for inv in entities["investigations"]:
        i = inv["index"]
        _write(i < len(invs), lambda v, i=i: invs[i].__setitem__("plain_note", v),
               f"inv:{i}", f"investigations[{i}]", proposals["inv"].get(i))

    return ExplainReport(outcomes=outcomes, retries_used=retries_used, withheld=sorted(withheld))


# --- orchestration ----------------------------------------------------------

def explain_record(record: dict, tier: str = "judgment", max_retries: int = 1):
    """Author the plain-language fields under the fidelity guard.

    Returns (updated_record_copy, ExplainReport). Fields that cannot pass the
    guard are left null and flagged explanation_withheld:<field>.
    """
    rec = copy.deepcopy(record)
    entities = _entities(rec)

    proposals = _author(entities, tier)
    retries_used = 0
    fails: dict[str, str] = {}
    for attempt in range(max_retries + 1):
        det = _deterministic_fails(proposals, entities)
        jud = _judge(rec, _mask(proposals, set(det.keys())), entities, tier)
        fails = {**det, **jud}
        if not fails or attempt == max_retries:
            break
        retries_used += 1
        revised = _author(entities, tier, feedback=_feedback(fails))
        proposals = _adopt_failing(proposals, revised, fails.keys())

    report = _apply(rec, proposals, entities, set(fails.keys()), fails, retries_used)
    return rec, report
