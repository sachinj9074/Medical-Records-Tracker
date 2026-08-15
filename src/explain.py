"""Plain-language explanations, fidelity-guarded.

Turns the extracted, structured record into plain-language notes
(diagnosis.plain_language, medications[].purpose_plain,
investigations[].plain_note). General facts about a named drug or test are
allowed; claims about this patient (severity, cause, whether treatment is
working, what to do) are not. All medical language is authored here, once, at
ingestion.

See PROJECT_SPEC.md sections 3 and 8.

TODO (Step 6): implement explanation with the fidelity constraint.
"""
