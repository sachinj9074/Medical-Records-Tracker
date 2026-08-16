"""Tiered, provider-swappable model access.

A fast model for extraction and a judgment model for explanations and hard
vision legibility work. All Anthropic-specific request shaping lives here, so
swapping providers means editing only this file. On failure it raises ModelError
with context rather than returning a half-formed result, letting the caller
decide how to degrade.

See PROJECT_SPEC.md sections 6 and 14.
"""

from __future__ import annotations

import base64
import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional; env vars may be set another way
    pass

FAST_MODEL = os.getenv("FAST_MODEL", "claude-sonnet-5")
JUDGMENT_MODEL = os.getenv("JUDGMENT_MODEL", "claude-opus-4-8")

_TIERS = {"fast": FAST_MODEL, "judgment": JUDGMENT_MODEL}

_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_PDF_MEDIA_TYPE = "application/pdf"


class ModelError(RuntimeError):
    """A model call failed or returned an unusable result."""


def model_for(tier: str) -> str:
    """Resolve a tier name ('fast' or 'judgment') to a concrete model id."""
    try:
        return _TIERS[tier]
    except KeyError:
        raise ModelError(f"unknown model tier: {tier!r}")


def _client():
    try:
        import anthropic
    except ImportError as e:
        raise ModelError(
            "the 'anthropic' package is not installed; run pip install -r requirements.txt"
        ) from e
    # The SDK resolves the API key from ANTHROPIC_API_KEY (or an ant profile).
    return anthropic.Anthropic()


def _source_block(data: bytes, media_type: str) -> dict:
    """Build the provider-specific content block for an image or PDF."""
    b64 = base64.standard_b64encode(data).decode("utf-8")
    if media_type in _IMAGE_MEDIA_TYPES:
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    if media_type == _PDF_MEDIA_TYPE:
        return {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    raise ModelError(f"unsupported media type: {media_type!r}")


def _loads_json(text: str) -> dict:
    """Parse a JSON object from model text, tolerating code fences or stray prose."""
    s = text.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Last resort: slice out the outermost braces.
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError as e:
                raise ModelError(f"model returned invalid JSON: {e}") from e
        raise ModelError("model returned no JSON object")


def extract_json(
    *,
    data: bytes,
    media_type: str,
    system: str,
    user: str,
    tier: str = "fast",
    max_tokens: int = 12000,
) -> dict:
    """Send a document image or PDF plus instructions and return the parsed JSON.

    The `user` prompt is expected to carry the required JSON shape; the caller
    validates the result against the schema (structured outputs cannot be used
    here because the record has more nullable fields than that feature allows).
    Raises ModelError on transport failure, refusal, truncation, or bad JSON.
    """
    client = _client()
    content = [_source_block(data, media_type), {"type": "text", "text": user}]
    try:
        response = client.messages.create(
            model=model_for(tier),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:  # boundary: wrap SDK/transport errors for the caller
        raise ModelError(f"model request failed: {e}") from e

    if response.stop_reason == "refusal":
        raise ModelError("model declined the request (stop_reason=refusal)")
    if response.stop_reason == "max_tokens":
        raise ModelError("model output was truncated (stop_reason=max_tokens); raise max_tokens")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ModelError("model returned no text block to parse")
    return _loads_json(text)
