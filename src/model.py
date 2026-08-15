"""Tiered, provider-swappable model access.

A fast model for extraction and a judgment model for explanations and hard
vision legibility work, selected here so the rest of the code never hardcodes a
provider. Degrades gracefully on errors.

See PROJECT_SPEC.md sections 6 and 14.

TODO (Step 4): implement the fast/judgment model interface.
"""
