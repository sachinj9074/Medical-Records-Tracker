"""Schema validation and needs-review flagging (deterministic).

Validates a record against schemas/record.schema.json and sets needs_review from
real signals (low confidence, null critical fields, an unreadable dose), not
noise. Code only, never the model.

See PROJECT_SPEC.md sections 6 and 11.

TODO (Step 5): implement schema validation and the flagging rules.
"""
