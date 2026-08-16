"""Tests for the deterministic medical-advice refusal guard.

Allowed is pure retrieval; refused is any question asking the tool to exercise
medical judgment. Mixed questions are refused (retrieval never bridges to the
present). See PROJECT_SPEC.md section 10.
"""

import pytest

from src import guard

ALLOWED = [
    "",
    "Show me what I was given last time for this",
    "What did Dr Tupe prescribe in March?",
    "What medicines did the doctor give me?",
    "When was my last HbA1c?",
    "List my medications",
    "What is my latest creatinine reading?",
    "Show my diabetes records",
    "Find the dental prescription from June",
    "Export a summary for my appointment",
]

REFUSED = [
    "What should I take now?",
    "Is my HbA1c high?",
    "Is this serious?",
    "Should I stop taking metformin?",
    "Why is my creatinine high?",
    "What does this result mean?",
    "Will it get worse?",
    "What do you recommend?",
    "Do I have diabetes?",
    "How much should I take?",
    "Show me my results and tell me if they are dangerous",  # mixed -> refuse
]


@pytest.mark.parametrize("q", ALLOWED)
def test_allowed_questions(q):
    res = guard.classify_question(q)
    assert res.allowed, f"should allow: {q!r} (got {res.category})"
    assert res.message is None


@pytest.mark.parametrize("q", REFUSED)
def test_refused_questions(q):
    res = guard.classify_question(q)
    assert not res.allowed, f"should refuse: {q!r}"
    assert res.message == guard.REFUSAL_MESSAGE


def test_reported_categories():
    assert guard.classify_question("Is this serious?").category == "severity"
    assert guard.classify_question("What should I do?").category == "general_advice"
    assert guard.classify_question("What does this result mean?").category == "interpretation"
    assert guard.classify_question("Should I stop taking metformin?").category == "treatment_change"
    assert guard.classify_question("Do I have diabetes?").category == "diagnosis"


def test_helpers():
    assert guard.is_allowed("List my medications")
    assert not guard.is_allowed("Is my sugar dangerous?")
    assert guard.guard_response("List my medications") is None
    assert guard.guard_response("What should I take?") == guard.REFUSAL_MESSAGE


def test_standing_constants_present():
    assert guard.STANDING_NOTICE
    assert guard.NO_MATCH_MESSAGE
