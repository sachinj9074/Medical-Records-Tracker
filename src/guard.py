"""Deterministic medical-advice refusal guard.

Intercepts questions that ask for medical judgment (what should I do, is this
serious, should I stop this) and returns a fixed plain-language response that
redirects to a doctor. This is code, never the model: the refusal must not
depend on the model's goodwill. It sits on every path that takes a user question
(search now, chat later). Showing a past record is allowed; bridging it to the
present is not.

See PROJECT_SPEC.md section 10.

TODO (Step 5): implement intent detection and the fixed refusal response.
"""
