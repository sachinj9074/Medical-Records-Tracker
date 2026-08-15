# Medical Records and Prescription Tracker: Project Spec

Status: living spec. Version 1, 2026-08-15.
This refines the original kickoff brief with the decisions made during the design conversation. Where this file and the kickoff disagree, this file wins.

---

## 1. What we are building

Turn a pile of prescriptions and medical reports, including messy and handwritten ones, into a readable, searchable personal health record. You upload a document soon after a visit, the tool reads it, structures it, explains it in plain language, files it on a timeline, and lets you find it again months later.

Value in one line: make your own old, messy medical records findable and understandable.

This is a smart helper that has read your whole file and can find and read back the right page. It is not a doctor and not a doctor's assistant.

## 2. Who it is for, and the honest wedge

For individuals and families managing their own records.

Storage alone is a crowded space (patient portals, Apple and Google Health, national health lockers). We do not compete there. Our defensible wedge is the part those tools do badly: **the messy paper that never made it into any portal**. The handwritten script from a small clinic, the lab PDF that never synced, the photo of a discharge note. Reading that, structuring it, and explaining it is the hard, valuable, un-solved part. The project stays pointed there. If it drifts toward "general health record app," it gets weaker, not stronger.

## 3. Product principles (non-negotiable)

1. **The tool's job ends at "here is what the document says, and here it is." It never crosses into "here is what that means for you now."** This is the line that keeps the whole project safe.
2. **The original image is the source of truth.** Structured data is a convenience layer that can be wrong. The original is always retained and shown alongside the extraction, and every field can be traced back to it. This is our trust story, our safety net, and our liability shield in one.
3. **Extract, do not assess.** The tool reads what is written. It never computes a clinical judgment (see the lab-flag rule in the schema).
4. **All medical language is authored once, at ingestion, under the fidelity guard.** Search and (later) chat only find and read back stored, already-checked explanations. They never write new medical content.
5. **Ingestion must be nearly effortless.** The entire value is deferred six months and only pays off if the archive actually accumulates, which depends on a habit. Snap, the tool does the rest, and it only pulls you in to review when confidence is low.

## 4. Scope

**v1 (in):**
- Single-document upload, with the original retained.
- Vision extraction into a structured record.
- Plain-language explanation, fidelity-guarded.
- Episode clustering and a longitudinal timeline.
- Simple search over past records (keyword and field based).
- Export a record or a date range as a doctor-ready summary.
- The deterministic medical-advice refusal guard, present on every path.

**v1 (out):**
- Chat-based retrieval (moved to v2).
- Appointment booking, reminders, alerts.
- Any drug-interaction checking (edges into advice).
- Multi-user accounts.
- Any computed clinical assessment.

**v2 and later:**
- **Chat retrieval:** ask "what happened last time I had this?" in natural language and get back the matching past records plus their stored explanations. This is the exciting feature and also the one with the sharpest safety edge (a latent self-medication risk), so it is deliberately deferred until the safe core is proven. When built, the guard sits inside the chat path, and chat only retrieves, never composes new medical statements.
- Medication reminders framed strictly as "as prescribed."
- Family profiles.
- Lab-value trend views, as pure plots with zero interpretive commentary.

## 5. The two core loops

**Ingest-time (the habit):**
```
snap or upload a doc -> extract fields -> explain in plain language ->
review only if low confidence -> cluster into an episode -> file on the timeline
```

**Ask-time (the payoff), v1:**
```
type a keyword or condition -> matching records returned ->
original image + structured record + plain-language explanation shown
```
(v2 replaces the first step with a natural-language chat question, behind the guard.)

## 6. AI versus code split

**AI (vision and judgment):**
- Read fields from messy or handwritten images.
- Classify the document type.
- Write the plain-language explanations.
- Self-report confidence.

**Code (deterministic, never the model):**
- Schema validation.
- Needs-review flagging logic.
- Episode clustering and timeline ordering by date.
- The medical-advice refusal guard.
- Search and export.

Rule of thumb: anything a user could be harmed by if it were inconsistent stays out of the model.

## 7. Data and firewall

- **Public demo:** synthetic, clearly fictional sample prescriptions and reports, committed under `samples/`.
- **Real records:** local only, gitignored, never uploaded to the hosted demo, never committed. Originals live under a gitignored local store.

## 8. Output schema (`schemas/record.schema.json`)

```json
{
  "record_id": "string",
  "episode_id": "string | null",
  "source_filename": "string",
  "date_processed": "YYYY-MM-DD",
  "document_type": "prescription | lab_report | discharge_summary | other",
  "record_date": "YYYY-MM-DD | null",
  "provider": { "name": "string | null", "specialty": "string | null", "clinic": "string | null" },
  "patient": { "name": "string | null", "age": "number | null", "sex": "string | null" },
  "diagnosis": { "stated_text": "string | null", "plain_language": "string | null" },
  "medications": [
    { "name": "string", "strength": "string | null", "form": "string | null",
      "dose": "string | null", "frequency": "string | null", "duration": "string | null",
      "purpose_plain": "string | null" }
  ],
  "investigations": [
    { "name": "string", "value": "string | null", "unit": "string | null",
      "reference_range": "string | null", "flag": "normal | high | low | unknown",
      "plain_note": "string | null" }
  ],
  "advice_verbatim": "string | null",
  "follow_up": "string | null",
  "confidence": "high | medium | low",
  "flags": ["string"],
  "needs_review": "Y | N"
}
```

**Rules for the model:**
- Dates normalised to YYYY-MM-DD.
- Unreadable fields returned as `null`, never guessed and never the string "N/A".
- `plain_language`, `purpose_plain`, and `plain_note` explain only what is written. General facts about a named drug or test (for example, "amoxicillin is an antibiotic") are allowed. Claims about *this patient* (severity, cause, whether treatment is working, what to do) are not.
- **Lab-flag rule:** `investigations[].flag` carries a value only when that flag is printed on the source report (labs usually print H or L themselves). The model must never compute it from the value and reference range. If no flag is printed, use `"unknown"`. This is the one place the schema could slip from extraction into assessment, so it is closed by design.

**Fields added or changed from the kickoff:**
- `episode_id` added, to link documents from the same visit or condition (see section 9).
- Lab-flag semantics changed from "the tool determines high or low" to "printed-only, never computed."
- The original file referenced by `source_filename` is always retained locally by the store and shown next to the record.

## 9. Episode clustering

Records are grouped into episodes so the timeline and (later) chat answers read as coherent visits rather than scattered pages.

- Cluster by `record_date` proximity, provider, and overlapping condition or medications.
- The user can manually link or split episodes.
- Clustering is deterministic (code, not model) and lives with the timeline logic.

## 10. Safety boundary and guard

- The tool explains and organises. It never diagnoses, never assesses severity, and never recommends starting, stopping, or changing any treatment.
- A deterministic guard (`src/guard.py`) intercepts medical-advice questions ("what should I do", "is this serious", "should I stop this") and returns a fixed plain-language response that redirects to a doctor. This never depends on the model's goodwill, and it sits on every path that takes a user question (search now, chat later).
- The boundary between allowed and refused:
  - "Show me what I was given last time for this" is allowed (pure retrieval).
  - "What should I take now?" is refused (advice).
  - Retrieval must never bridge to the present. Showing a past prescription is fine; suggesting the user repeat it is not.
- When search or chat finds nothing, the honest answer is "I have no record matching that." It must not fill the gap with general medical knowledge.
- Every record view shows a short standing line: this is a record aid, not medical advice.

## 11. Success criteria and metrics

- **Extraction accuracy:** field-level correctness on a labelled synthetic set, weighted toward handwritten and low-quality images.
- **Explanation fidelity:** the plain-language output adds no claim absent from the source. This is the metric that protects the safety story.
- **Correct-refusal rate:** medical-advice questions the guard must decline cleanly.
- **Needs-review correctness:** flags fire for a real reason, not noise.
- **Time-to-file (product metric):** how little friction a new upload takes. The archive only accumulates if this stays low. Success is as much a habit-formation problem as an AI problem.

## 12. Risks and guardrails

- **Handwriting** is the hard part. Expect low-confidence extractions and route them to review. The original is always kept, so a bad read is a convenience failure, not a data-loss failure.
- **Explanation drift** is the subtle risk: the model adding a plausible claim the document never made. The fidelity check exists to catch exactly this.
- **Self-medication** is the product-ethics risk that lives mostly in v2 retrieval: the user acting on a past prescription. The words stay clean; the framing ("never bridge to the present") and deferring chat until the core is proven are the mitigations.
- **Adoption** is the business risk: if ingestion is a chore, the archive never accumulates. Mitigated by the low-friction ingestion principle.

## 13. Eval design

Build the eval set before the pipeline. Four things to measure, mapped to the success criteria:

1. Extraction accuracy on a labelled set weighted toward handwritten and low-quality images.
2. Explanation fidelity: a judged check that the plain-language output contains no claim absent from the source.
3. Correct refusal: a set of medical-advice prompts the guard must decline.
4. Needs-review correctness: flags fire for a real reason.

Record baseline numbers, then improve, re-running the eval and regenerating the demo cache on every change.

## 14. Build order

1. Scaffold files, `.gitignore`, `.env.example`, `requirements.txt`; first commit.
2. Write `record.schema.json` and validate it.
3. Create three or four synthetic sample documents (including one handwritten-style) and label them for the eval set.
4. Build `extract.py` against the schema, vision model reading, confidence self-reported.
5. Add `validate.py` (schema plus needs-review) and `guard.py` (the refusal guard).
6. Add `explain.py` with the fidelity constraint.
7. Add `store.py` (local persistence, original retained), `timeline.py` (episode clustering plus timeline), `search.py` (simple search), and `export.py` (doctor-ready summary).
8. Wire `app.py`: upload, review-if-needed, record view, timeline, search, export.
9. Commit every working step.

Models: a fast model for extraction, a judgment model for explanations and any vision legibility work, swappable through `model.py`, degrading gracefully on errors.

## 15. Folder structure

```
FRAMEWORK.md
PROJECT_SPEC.md
README.md
requirements.txt
.gitignore            # .env, /local_records/, any real PII output
.env.example
schemas/
  record.schema.json
src/
  app.py              # Streamlit: upload -> review -> record + timeline + search + export
  extract.py          # vision extraction -> structured record
  explain.py          # plain-language explanations, fidelity-guarded
  validate.py         # schema + needs_review flagging (deterministic)
  store.py            # local record + original-image persistence (gitignored)
  timeline.py         # episode clustering + longitudinal view (deterministic)
  search.py           # simple keyword/field search over stored records (v1)
  guard.py            # medical-advice refusal guard (deterministic)
  export.py           # export a record or date range as a doctor-ready summary
  model.py            # tiered fast/judgment models, provider-swappable
eval/
  eval_set/           # synthetic labelled samples
  run_eval.py
  eval_log_template.csv
samples/              # fictional prescriptions and reports (committed)
demo_cache/           # precomputed demo outputs for zero-cost demo mode
local_records/        # real records + originals, gitignored, never committed
test_extract.py
test_pipeline.py
```

## 16. Later phases (cost and ship)

Cost-safety before any public deploy: cached demo mode over the synthetic samples so a visitor costs nothing, live uploads gated behind a password and a per-session cap, and a spend cap in the provider console. You paste your own API key. Then GitHub, the Streamlit demo on synthetic data, a README with the eval table, and a short write-up built around the one number that proves it works.
