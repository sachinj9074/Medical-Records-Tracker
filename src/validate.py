"""Deterministic validation and review-flagging (no model calls).

Two jobs, both deterministic so they never depend on the model's goodwill:

  1. Schema gate: validate a full assembled record against record.schema.json.
     extract.py only checks the model-facing subset; this checks the complete
     record, including the code-owned fields.

  2. needs_review: decide, from real structural signals rather than the model's
     self-reported confidence alone, whether a human should eyeball the record
     before it is filed. Confidence self-report is noisy (a legible-enough hand
     can be read at high confidence, and a hard one can be invented at medium),
     so the flag combines confidence with structural signals, and deliberately
     stays quiet on weak ones to avoid crying wolf (PROJECT_SPEC.md sections 5,
     11: fire for a real reason, not noise).

A separate should_escalate() predicate recommends a judgment-tier re-read for
hard prescriptions before we bother the user: the tool tries harder itself
first. It is advisory only; this module never calls a model. The re-read loop
that acts on it lives at ingestion.

See PROJECT_SPEC.md sections 8, 10, 11, 12.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(_ROOT, "schemas", "record.schema.json")

# Substrings that mark a hard-to-read source. The model's flags are free-form,
# so match loosely rather than against a fixed vocabulary.
_LEGIBILITY_MARKERS = (
    "handwritten", "unreadable", "illegible", "partial", "obscured",
    "smudg", "blur", "cut_off", "cropp", "low_confidence", "low_quality", "faint",
)


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


_VALIDATOR = Draft202012Validator(load_schema())


@dataclass
class ValidationResult:
    schema_ok: bool
    schema_errors: list = field(default_factory=list)
    needs_review: str = "N"
    reasons: list = field(default_factory=list)


def schema_errors(record: dict) -> list[str]:
    """Human-readable schema violations for a full record; empty if valid."""
    return [e.message for e in sorted(_VALIDATOR.iter_errors(record), key=str)]


def is_schema_valid(record: dict) -> bool:
    return not schema_errors(record)


def _has_legibility_flag(flags) -> bool:
    if not isinstance(flags, list):
        return False
    return any(
        isinstance(f, str) and any(m in f.lower() for m in _LEGIBILITY_MARKERS)
        for f in flags
    )


def review_reasons(record: dict) -> list[str]:
    """The concrete reasons this record should be eyeballed, or an empty list.

    Each reason is a real, specific signal. Weak signals (medium confidence on
    its own, a missing patient name, a med missing only its duration) are left
    out on purpose so the flag stays meaningful.
    """
    reasons: list[str] = []
    conf = record.get("confidence")
    flags = record.get("flags") or []
    dtype = record.get("document_type")
    meds = record.get("medications")
    invs = record.get("investigations")
    meds = meds if isinstance(meds, list) else []
    invs = invs if isinstance(invs, list) else []

    if conf == "low":
        reasons.append("low_confidence")
    if record.get("record_date") is None:
        reasons.append("missing_date")
    if dtype == "other":
        reasons.append("unclassified_document")
    if dtype == "prescription" and not meds:
        reasons.append("no_medications_read")
    if dtype == "lab_report" and not invs:
        reasons.append("no_investigations_read")

    # Unreadable dosing: a named medication with neither a dose nor a frequency.
    if dtype == "prescription":
        for m in meds:
            if isinstance(m, dict) and m.get("dose") is None and m.get("frequency") is None:
                reasons.append(f"incomplete_dosing:{m.get('name')}")

    # A hard read that the model only rated medium: the image-1 case. (Low is
    # already caught above; high is trusted and not second-guessed on a flag.)
    if conf == "medium" and _has_legibility_flag(flags):
        reasons.append("medium_confidence_hard_read")

    return reasons


def evaluate(record: dict) -> ValidationResult:
    """Validate the record and decide needs_review, without mutating it."""
    errs = schema_errors(record)
    reasons: list[str] = []
    if errs:
        reasons.append("schema_invalid")
    reasons.extend(review_reasons(record))
    needs = "Y" if reasons else "N"
    return ValidationResult(
        schema_ok=not errs, schema_errors=errs, needs_review=needs, reasons=reasons
    )


def apply(record: dict) -> ValidationResult:
    """Evaluate and write needs_review into the record in place."""
    result = evaluate(record)
    record["needs_review"] = result.needs_review
    return result


def should_escalate(record: dict, tier_used: str) -> bool:
    """Recommend a judgment-tier re-read? Advisory; makes no model call.

    Only fast-tier prescriptions escalate, and only when the read looks hard:
    low or medium confidence, or a legibility flag. This is the generalised
    image-1 fix (fast invented an absent cream duration on a handwritten
    script; judgment left it null). Lab reports and clean reads stay on fast.
    """
    if tier_used != "fast":
        return False
    if record.get("document_type") != "prescription":
        return False
    if record.get("confidence") in ("low", "medium"):
        return True
    return _has_legibility_flag(record.get("flags") or [])
