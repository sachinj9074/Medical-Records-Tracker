"""Deterministic medical-advice refusal guard (no model calls).

The tool explains and organises; it never diagnoses, never assesses severity,
and never recommends starting, stopping, or changing any treatment
(PROJECT_SPEC.md section 10). This guard sits on every path that takes a user
question (search now, chat later). It classifies intent by pattern and refuses
medical-judgment questions with a fixed response that redirects to a doctor, so
the boundary never depends on the model's goodwill.

Allowed is pure retrieval ("show me what I was given last time"); refused is any
question that asks the tool to act as a clinician ("what should I take now?",
"is this serious?", "why is my creatinine high?"). Retrieval must never bridge
to the present, so a question that mixes retrieval with judgment is refused.

This is a first-line floor, not a ceiling: a keyword classifier can be evaded
and can over-trigger. It is layered with the extraction and explanation prompts
and the explain-time fidelity guard, never relied on alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Shown under every record view; a standing reminder of what the tool is.
STANDING_NOTICE = "This is a record aid, not medical advice."

# When search (or later, chat) finds nothing, this is the honest answer. It must
# not be replaced by general medical knowledge that fills the gap.
NO_MATCH_MESSAGE = "I have no record matching that."

REFUSAL_MESSAGE = (
    "I can't tell you what this means for you, whether it's serious, or what to "
    "do about it. I'm a record keeper, not a clinician, so those questions are "
    "for your doctor. What I can do is show you exactly what your documents say "
    "and organise them: pull up a past reading and how the report itself flagged "
    "it, list what a doctor prescribed on a given visit, or put together a "
    "summary you can take to your appointment. Want me to do any of that?"
)

# Each category is a way of asking the tool to exercise medical judgment. If any
# pattern matches, the question is refused. Patterns are matched case-insensitively
# against the lowercased question. Kept deliberately targeted at first-person
# "what does this mean for me / what should I do" phrasing so that third-person
# retrieval ("what did the doctor prescribe") stays allowed.
_JUDGMENT_PATTERNS: dict[str, list[str]] = {
    "diagnosis": [
        r"\bdo i have\b",
        r"\bwhat('?s| is) wrong with me\b",
        r"\bam i (\w+\s+)?(diabetic|sick|ill|dying|anaemic|anemic|infected)\b",
        r"\bwhat (condition|disease|illness) (do|have) i\b",
        r"\bdiagnos(e|is|ing)\b",
    ],
    "severity": [
        r"\bis (it|this|that|my \w+) (serious|dangerous|bad|normal|okay|ok|fine|"
        r"high|low|concerning|worrying|alarming|healthy)\b",
        r"\bhow (bad|serious|dangerous|high|low) (is|are)\b",
        r"\bshould i (be )?(worry|worried|concerned)\b",
        r"\b(too high|too low|dangerous|dangerously|life[- ]threatening)\b",
    ],
    "treatment_change": [
        r"\bshould i (start|stop|continue|change|switch|take|keep taking|quit)\b",
        r"\b(can|should) i stop\b",
        r"\bdo i (still )?need (to take|this|these|it)\b",
        r"\bis it (safe|ok|okay) to (take|stop|combine|mix|use)\b",
    ],
    "dosing_advice": [
        r"\b(what|which)( dose| dosage)?\s+(should|do) i take\b",
        r"\bhow (much|many|often) (should|do) i (take|use)\b",
        r"\bcan i take \w+ (with|and|alongside)\b",
        r"\bwhat (medicine|medication|drug|pills?|tablets?) (should|can) i\b",
    ],
    "prognosis": [
        r"\bwill i (get|be|recover|die|need|have to)\b",
        r"\bwill (it|this|that) (get worse|improve|go away|heal|come back|spread)\b",
        r"\bhow long (until|till|before|will)\b",
        r"\bwhat('?s| is) (my|the) (prognosis|outlook|chance)\b",
        r"\bam i going to\b",
    ],
    "interpretation": [
        r"\bwhat does .*\bmean\b",
        r"\bwhy (is|are|do|does) (my|i)\b",
        r"\bwhat (caused|causes|is causing)\b",
        r"\bwhat('?s| is) causing\b",
    ],
    "general_advice": [
        r"\bwhat should i do\b",
        r"\bwhat do you (recommend|suggest|advise)\b",
        r"\bwhat would you do\b",
        r"\bhelp me (decide|choose|figure)\b",
        r"\b(any|some) advice\b",
        r"\bis there anything i (should|can|need to) (do|take)\b",
    ],
}

_COMPILED: dict[str, list[re.Pattern]] = {
    cat: [re.compile(p, re.IGNORECASE) for p in pats]
    for cat, pats in _JUDGMENT_PATTERNS.items()
}


@dataclass
class GuardResult:
    allowed: bool
    category: str
    message: str | None  # the fixed refusal when not allowed, else None


def classify_question(text: str) -> GuardResult:
    """Classify a user question as allowed retrieval or a refused judgment ask.

    Returns the first matching judgment category (refused) or 'retrieval'
    (allowed). Empty input is allowed as a no-op.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return GuardResult(allowed=True, category="empty", message=None)
    for category, patterns in _COMPILED.items():
        if any(p.search(lowered) for p in patterns):
            return GuardResult(allowed=False, category=category, message=REFUSAL_MESSAGE)
    return GuardResult(allowed=True, category="retrieval", message=None)


def is_allowed(text: str) -> bool:
    return classify_question(text).allowed


def guard_response(text: str) -> str | None:
    """The refusal message if the question must be declined, else None."""
    return classify_question(text).message
