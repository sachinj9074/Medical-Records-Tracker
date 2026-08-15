"""Local record and original-image persistence (gitignored).

Persists structured records plus their original uploaded files under
local_records/, which is never committed. The original is the source of truth;
the structured JSON is a fallible convenience layer stored alongside it.

See PROJECT_SPEC.md sections 3 and 7.

TODO (Step 7): implement local persistence and original retention.
"""
