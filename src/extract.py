"""Vision extraction: a messy or handwritten document image -> structured record.

Fills only the factual fields of record.schema.json from what is on the page.
Three groups of fields are deliberately not the model's to write:
  - Plain-language fields (diagnosis.plain_language, medications[].purpose_plain,
    investigations[].plain_note) stay null here; explain.py authors them later
    under the fidelity guard.
  - Code-owned fields (record_id, source_filename, date_processed, episode_id,
    needs_review) are set by code here or downstream, never by the model.
So the model-facing schema is record.schema.json minus those fields. The model
returns JSON guided by that schema; this module validates the result against it
with jsonschema (the deterministic gatekeeper) and repairs once on failure. The
full assembled record is validated again by validate.py (Step 5).

Lab flags are carried only if printed on the source, never computed.

See PROJECT_SPEC.md sections 6 and 8.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import uuid

from jsonschema import Draft202012Validator

from src import model

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(_ROOT, "schemas", "record.schema.json")

# Top-level fields set by code, not the model.
_CODE_OWNED = ("record_id", "episode_id", "source_filename", "date_processed", "needs_review")
# Plain-language fields authored by explain.py, not extraction.
_PLAIN_FIELDS = {
    "diagnosis": ("plain_language",),
    "medications": ("purpose_plain",),
    "investigations": ("plain_note",),
}

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

EXTRACTION_SYSTEM = """You read a single medical document (prescription, lab report, discharge summary, or other) and extract what is written into a structured record.

Rules:
- Extract only what is on the page. If a field is unreadable or absent, use null. Never guess, and never write "N/A".
- Normalise every date to YYYY-MM-DD. If a date is unreadable, use null.
- Classify the document type.
- Write out standard dosing shorthand into normal words in the dose, frequency, and duration fields. For example: "1-0-1" or "BD" means one unit twice daily (dose "1 tablet", frequency "twice daily"); "1-1-1" or "TDS" means three times a day; "OD" means once daily; "x5d" means "5 days"; "b/f" means "before food". In X-Y-Z notation each number is the units taken morning, afternoon, and night. This is de-abbreviating standard notation, not interpreting, and it does not change what was prescribed.
- Do not assess or interpret beyond that. Do not add severity, cause, prognosis, plain-language notes, opinions, or advice; those are not part of your output. Keep diagnosis.stated_text and advice_verbatim exactly as written on the page.
- provider.specialty is a medical specialty (for example Cardiology, Dermatology) only if one is stated. Do not put qualifications or degrees such as MBBS, BDS, or MDS there; if no specialty is stated, use null.
- Lab flags: set an investigation's flag only if a flag (for example H or L) is printed on the report. Never compute it from the value and reference range. If no flag is printed, use "unknown".
- Report your confidence in the extraction as high, medium, or low, based on how legible the document is.
- In flags, list short machine-readable reasons the record may need review, for example "handwritten", "low_confidence", or "unreadable_dose". Use an empty list if there are none.
"""

EXTRACTION_USER = "Extract this document into the structured record."


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _drop_fields(obj: dict, fields) -> None:
    """Remove `fields` from an object schema's properties and required list."""
    for f in fields:
        obj["properties"].pop(f, None)
    obj["required"] = [r for r in obj["required"] if r not in fields]


def extraction_schema(full: dict | None = None) -> dict:
    """The model-facing schema: the full record minus code-owned and
    plain-language fields. Constraints (date pattern, enums) are kept so the
    validator enforces them."""
    schema = copy.deepcopy(full or load_schema())
    _drop_fields(schema, _CODE_OWNED)
    _drop_fields(schema["properties"]["diagnosis"], _PLAIN_FIELDS["diagnosis"])
    _drop_fields(schema["properties"]["medications"]["items"], _PLAIN_FIELDS["medications"])
    _drop_fields(schema["properties"]["investigations"]["items"], _PLAIN_FIELDS["investigations"])
    return schema


def _base_user_prompt(schema: dict) -> str:
    return (
        EXTRACTION_USER
        + "\n\nReturn a single JSON object that matches this schema exactly. "
        "Output only the JSON, with no markdown fences and no commentary.\n\n"
        + json.dumps(schema, indent=2)
    )


def media_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    try:
        return _MEDIA_TYPES[ext]
    except KeyError:
        raise ValueError(f"unsupported file type: {ext!r}")


def new_record_id() -> str:
    return "rec_" + uuid.uuid4().hex[:12]


def assemble_record(extracted: dict, source_filename: str) -> dict:
    """Merge model-extracted facts with code-owned fields and null
    plain-language placeholders into a full record."""
    record = dict(extracted)
    record["record_id"] = new_record_id()
    record["episode_id"] = None
    record["source_filename"] = source_filename
    record["date_processed"] = datetime.date.today().isoformat()
    record["needs_review"] = "N"  # placeholder; validate.py sets this deterministically

    # Restore the null plain-language fields for the explain step to fill.
    if isinstance(record.get("diagnosis"), dict):
        record["diagnosis"].setdefault("plain_language", None)
    for med in record.get("medications", []):
        med.setdefault("purpose_plain", None)
    for inv in record.get("investigations", []):
        inv.setdefault("plain_note", None)
    return record


def extract_record(image_path: str, tier: str = "fast", max_repairs: int = 1) -> dict:
    """Extract one document into a full record dict conforming to
    record.schema.json. Plain-language fields are null; needs_review is a
    placeholder that validate.py later sets authoritatively.

    The model's JSON is validated against the model-facing schema and repaired
    once on failure. Pass tier="judgment" to read a hard or low-confidence
    document with the stronger vision model.
    """
    media_type = media_type_for(image_path)
    with open(image_path, "rb") as f:
        data = f.read()

    schema = extraction_schema()
    validator = Draft202012Validator(schema)
    base_user = _base_user_prompt(schema)
    user = base_user

    errors = []
    for attempt in range(max_repairs + 1):
        extracted = model.extract_json(
            data=data, media_type=media_type,
            system=EXTRACTION_SYSTEM, user=user, tier=tier,
        )
        errors = sorted(validator.iter_errors(extracted), key=str)
        if not errors:
            break
        reasons = "; ".join(e.message for e in errors[:8])
        user = base_user + f"\n\nYour previous output failed validation: {reasons}. Return corrected JSON only."

    if errors:
        raise model.ModelError(
            "extraction did not satisfy the schema after repair: "
            + "; ".join(e.message for e in errors[:8])
        )
    return assemble_record(extracted, source_filename=os.path.basename(image_path))
