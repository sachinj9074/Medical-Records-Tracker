"""Ingestion pipeline: one uploaded document -> a stored, explained record.

Chains the pieces the earlier steps built, in order, and wires the two threads
that were deferred: the fast->judgment escalation re-read for hard prescriptions,
and the full extract -> explain -> validate -> store flow.

    extract (fast)
      -> if should_escalate: re-extract (judgment)
      -> explain (fills plain-language or withholds)
      -> validate (final needs_review)
      -> store.save (record + retained original)

Degrades gracefully (PROJECT_SPEC.md section 14): a failed judgment re-read falls
back to the fast read, and a failed explanation stores the record with
plain-language left null and an explanation_failed flag, so a record always
lands and is flagged for review rather than lost.

store.py stays pure persistence; this module owns the model-driven orchestration.

See PROJECT_SPEC.md sections 6, 7, 14.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field

from src import explain, extract, model, validate
from src.store import Store


@dataclass
class IngestReport:
    record_id: str
    tier_used: str
    escalated: bool
    needs_review: str
    reasons: list = field(default_factory=list)
    explanations_authored: int = 0
    explanations_withheld: int = 0
    errors: list = field(default_factory=list)


def ingest_record(
    image_path: str,
    store: Store,
    *,
    allow_escalation: bool = True,
    fast_tier: str = "fast",
    judgment_tier: str = "judgment",
):
    """Run one document through the full pipeline and store the result.

    Returns (record, IngestReport). Model failures in the escalation and
    explanation stages degrade gracefully and are noted in the report; only a
    failure of the initial extraction (no record at all) propagates.
    """
    errors: list[str] = []

    # 1. Extract on the fast tier.
    record = extract.extract_record(image_path, tier=fast_tier)
    tier_used = fast_tier
    escalated = False

    # 2. Escalate a hard prescription to the judgment tier (the deferred loop).
    if allow_escalation and validate.should_escalate(record, fast_tier):
        try:
            record = extract.extract_record(image_path, tier=judgment_tier)
            tier_used = judgment_tier
            escalated = True
        except model.ModelError as e:
            errors.append(f"escalation_failed: {e}")  # keep the fast read

    # 3. Explain under the fidelity guard.
    authored = withheld = 0
    try:
        record, ereport = explain.explain_record(record, tier=judgment_tier)
        authored = sum(1 for o in ereport.outcomes if o.status == "authored")
        withheld = len(ereport.withheld)
    except model.ModelError as e:
        errors.append(f"explanation_failed: {e}")
        flags = record.setdefault("flags", [])
        if "explanation_failed" not in flags:
            flags.append("explanation_failed")

    # 4. Final deterministic validation + needs_review.
    result = validate.apply(record)

    # 5. Persist the record and retain the original.
    store.save(record, original_path=image_path)

    return record, IngestReport(
        record_id=record.get("record_id"),
        tier_used=tier_used,
        escalated=escalated,
        needs_review=result.needs_review,
        reasons=result.reasons,
        explanations_authored=authored,
        explanations_withheld=withheld,
        errors=errors,
    )


def ingest_ephemeral(image_path: str, **kwargs):
    """Run the full pipeline but persist nothing: ingest into a throwaway store
    that is deleted immediately, returning the in-memory (record, IngestReport).

    Used by the hosted demo, where an uploaded document is processed live and
    kept only in the visitor's session, never written to the shared store.
    """
    tmp = tempfile.mkdtemp()
    try:
        return ingest_record(image_path, Store(tmp), **kwargs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
