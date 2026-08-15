"""Vision extraction: a messy or handwritten document image -> structured record.

Uses a vision model to read fields into the schema in
schemas/record.schema.json, classify the document type, and self-report
confidence. Unreadable fields are returned as null, never guessed. Lab flags are
carried only if printed on the source, never computed.

See PROJECT_SPEC.md sections 6 and 8.

TODO (Step 4): implement extraction against the schema.
"""
