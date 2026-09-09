# Medical Records and Prescription Tracker: Complete Project Brief

> **How to use this file.** This is a single self-contained knowledge base for the project. Attach it to a Claude chat (or add it to a Claude Project as knowledge) and ask anything: what the product is, why a design decision was made, how a specific module works, what the evals measure, what the output looks like, or how to run and deploy it. Everything needed to answer those questions is in here. It reflects the codebase as built (all 8 build steps complete and committed).

**Repository:** https://github.com/sachinj9074/Medical-Records-Tracker
**Live demo:** https://medical-records-tracker-jwcur2ngo2aixetfugvtti.streamlit.app/
**Language / stack:** Python 3.12, Streamlit, Anthropic Claude (vision + text), jsonschema.

---

## 1. One-paragraph summary

The tool turns a pile of messy, handwritten, or badly scanned prescriptions and medical reports into a readable, searchable personal health record. You upload a document soon after a visit; it reads the image (including a doctor's scrawl), structures it into a consistent record, explains the medical terms in plain language, flags anything it read with low confidence, files it on a timeline grouped into episodes of care, lets you search it months later, and exports a clean facts-only summary you can hand to a new doctor. The single most important property: **it is a record aid, not medical advice.** It transcribes, organises, and explains what a document says. It never diagnoses, never rates severity, and never tells you to start, stop, or change a treatment.

---

## 2. The problem and the wedge

A person's medical history ends up scattered across a paper drawer, a phone camera roll, and a few email attachments. Months later none of it is findable: you cannot remember which visit a blood test belonged to, the prescription is in shorthand you cannot read, and at a new doctor you arrive with a pile of photos instead of a history.

**The honest wedge.** Storage of clean digital records is a crowded space (hospital portals, Apple Health, Google Health, national health lockers). This project does not compete there. Its defensible edge is the part those tools do badly: **the messy paper that never made it into any portal.** The handwritten script from a small clinic, the lab PDF that never synced, the photo of a discharge note. Reading, structuring, and explaining that is the hard, valuable, unsolved part. If the project drifts toward being a general "health record app," it gets weaker, not stronger.

**Who it is for:** individuals and families managing their own records. Not a clinic tool, not a multi-user product.

---

## 3. Product principles (non-negotiable)

1. **The job ends at "here is what the document says, and here it is."** It never crosses into "here is what that means for you now." This one line keeps the whole project safe.
2. **The original image is the source of truth.** The structured data is a convenience layer that can be wrong. The original is always retained and shown next to the extraction. This is the trust story, the safety net, and the liability shield in one.
3. **Extract, do not assess.** The tool reads what is written. It never computes a clinical judgment (see the printed-lab-flag rule).
4. **All medical language is authored once, at ingestion, under a fidelity guard.** Search (and later, chat) only find and read back stored, already-checked explanations. They never write new medical content.
5. **Ingestion must be nearly effortless.** The value is deferred months and only pays off if the archive actually accumulates, which depends on a habit. Snap, the tool does the rest, and it only pulls you in to review when confidence is low.

---

## 4. What it does (the user-facing loop)

**Ingest-time (the habit):**
`upload a document → extract fields → explain in plain language → review only if low confidence → cluster into an episode → file on the timeline`

**Ask-time (the payoff), v1:**
`type a keyword or condition → matching records returned → original image + structured record + plain-language explanation shown`

Six capabilities:

- **Reads the document.** Prescriptions, lab reports, discharge summaries; typed or handwritten; images or PDFs; into one structured record.
- **Explains it in plain language.** A short, general, third-person gloss of what a diagnosis or medicine or test is, sitting next to (never replacing) the verbatim text.
- **Flags what needs a second look.** Every record carries a `needs_review` verdict computed from real structural signals.
- **Files it on a timeline.** Records cluster into episodes of care, newest first.
- **Lets you find it again.** A guarded, literal keyword search over what is actually written.
- **Exports a doctor-ready summary.** Facts-only Markdown, grouped by episode, downloadable.

---

## 5. Scope

**In (v1, all built):** single-document upload with the original retained; vision extraction into a structured record; fidelity-guarded plain-language explanations; episode clustering and a longitudinal timeline; keyword/field search; export a record or date range as a doctor-ready summary; the deterministic medical-advice refusal guard on every path that takes a question.

**Out (v1):** chat-based retrieval (v2); appointment booking, reminders, alerts; any drug-interaction checking (edges into advice); multi-user accounts; any computed clinical assessment.

**v2 and later:** natural-language chat retrieval ("what happened last time I had this?") with the guard inside the chat path and chat only retrieving, never composing new medical statements; medication reminders framed strictly as "as prescribed"; family profiles; lab-value trend plots with zero interpretive commentary.

---

## 6. Architecture and pipeline

```
        upload (image or PDF)
              |
      extract.py  (FAST tier: claude-sonnet-5, vision)  -> structured JSON, validated against schema
              |
      should_escalate?  (validate.py: fast + prescription + hard read)
         |yes|                                   |no|
      re-extract (JUDGMENT tier: claude-opus-4-8) |
              |__________________________________|
              |
      explain.py  (JUDGMENT tier)  author -> guard (deterministic + judge) -> gate
              |
      validate.py  (schema gate + needs_review from real signals)
              |
      store.py  (record JSON + retained original, atomic write)
              |
     _________|_________________________
     |             |                    |
 timeline.py    search.py            export.py
 (episodes)   (guarded keyword)   (doctor-ready Markdown)

  guard.py sits in front of every question (search now, chat later)
```

**The AI-versus-code split (the governing rule):** anything a user could be harmed by if it were inconsistent stays out of the model.

- **AI (vision and judgment):** read fields from messy images, classify the document type, write the plain-language explanations, self-report confidence.
- **Code (deterministic, never the model):** schema validation, needs-review flagging, episode clustering and timeline ordering, the medical-advice refusal guard, search, and export.

---

## 7. The data model (`schemas/record.schema.json`)

One digitised document becomes one record. JSON Schema Draft 2020-12, `additionalProperties: false`, all 16 top-level fields required (nullable where a field may be absent).

```json
{
  "record_id": "string",                     // code-owned, stable unique id "rec_...."
  "episode_id": "string | null",             // code-owned, set by clustering
  "source_filename": "string",               // code-owned
  "date_processed": "YYYY-MM-DD",            // code-owned
  "document_type": "prescription | lab_report | discharge_summary | other",  // model
  "record_date": "YYYY-MM-DD | null",        // model; null if unreadable, never guessed
  "provider": { "name": "string|null", "specialty": "string|null", "clinic": "string|null" },
  "patient":  { "name": "string|null", "age": "number|null", "sex": "string|null" },
  "diagnosis": { "stated_text": "string|null", "plain_language": "string|null" },  // plain_language by explain.py
  "medications": [
    { "name": "string", "strength": "string|null", "form": "string|null",
      "dose": "string|null", "frequency": "string|null", "duration": "string|null",
      "purpose_plain": "string|null" }        // purpose_plain by explain.py
  ],
  "investigations": [
    { "name": "string", "value": "string|null", "unit": "string|null",
      "reference_range": "string|null", "flag": "normal | high | low | unknown",
      "plain_note": "string|null" }           // plain_note by explain.py
  ],
  "advice_verbatim": "string | null",         // exactly as written, not paraphrased
  "follow_up": "string | null",
  "confidence": "high | medium | low",        // model self-report
  "flags": ["string"],                        // machine-readable review reasons
  "needs_review": "Y | N"                     // code-owned, set by validate.py
}
```

**Rules the model must follow when extracting:**
- Dates normalised to `YYYY-MM-DD`. Unreadable fields returned as `null`, never guessed, never the string "N/A".
- Standard dosing shorthand is expanded into words ("1-0-1" or "BD" → dose "1 tablet", frequency "twice daily"; "OD" → once daily; "x5d" → "5 days"; "b/f" → "before food"). This is de-abbreviating notation that is present, not interpreting.
- **Only transcribe dosing that is actually written.** If a dose, frequency, or duration is not on the page, that field is `null`. The model must never supply a typical or assumed course (for example, never a "7 days" duration for a cream that has no duration printed).
- `provider.specialty` is a medical specialty only if one is stated; never a degree like MBBS/BDS/MDS.
- **The printed-lab-flag rule (the one place the schema could slip from extraction into assessment, closed by design):** `investigations[].flag` carries `high`/`low`/`normal` only when that flag is printed on the report itself (labs usually print H or L). The model must never compute it from the value and the reference range. If no flag is printed, the value is `unknown`.

**Why some fields are code-owned:** `record_id`, `episode_id`, `source_filename`, `date_processed`, and `needs_review` are set by code, never by the model, so they are consistent and trustworthy. The three plain-language fields are left null by extraction and authored separately by `explain.py` under the fidelity guard.

---

## 8. Module-by-module technical reference (`src/`)

### `model.py` (tiered, provider-swappable model access)
- Two tiers resolved from env: `FAST_MODEL` (default `claude-sonnet-5`) and `JUDGMENT_MODEL` (default `claude-opus-4-8`). All Anthropic-specific request shaping lives here, so swapping providers means editing only this file.
- `extract_json(...)`: sends a document image or PDF plus instructions, returns parsed JSON (`max_tokens=12000`, default `tier="fast"`). Builds the right content block per media type (image vs PDF document).
- `complete_json(...)`: text-only structured call, no image attached (default `tier="judgment"`, `max_tokens=4000`), used by `explain.py`.
- Robust JSON parsing tolerates code fences and stray prose. On refusal (`stop_reason == "refusal"`), truncation (`max_tokens`), or bad JSON it raises `ModelError` with context instead of returning a half-formed result, letting the caller decide how to degrade.
- **Why guided-JSON and not structured outputs:** the record has more nullable fields than the structured-outputs feature allows, so the schema is passed in the prompt and the result is validated with jsonschema instead.

### `extract.py` (vision extraction → structured record)
- Holds `EXTRACTION_SYSTEM`, the prompt that encodes every extraction rule in section 7.
- Builds a **model-facing schema** = the full record schema minus the code-owned fields and the three plain-language fields, while keeping constraints (date patterns, enums) so the validator still enforces them.
- Validates the model's JSON against that schema with `Draft202012Validator`; on failure it repairs once (`max_repairs=1`) by sending the validation errors back and asking for corrected JSON; if it still fails it raises `ModelError`.
- `assemble_record(...)` merges the extracted facts with the code-owned fields and restores the null plain-language placeholders. `record_id` is `"rec_" + uuid hex[:12]`.

### `validate.py` (deterministic validation and review flagging, no model)
- **Schema gate:** validates the full assembled record (including code-owned fields) against the schema.
- **`needs_review`** is computed from real structural signals, not the model's self-reported confidence alone (self-report is noisy). A record is flagged `Y` if any of these reasons fire:
  - `low_confidence` (confidence == low)
  - `missing_date` (record_date is null)
  - `unclassified_document` (document_type == other)
  - `no_medications_read` (a prescription with an empty medications list)
  - `no_investigations_read` (a lab_report with an empty investigations list)
  - `incomplete_dosing:<name>` (a prescription medication with neither a dose nor a frequency)
  - `medium_confidence_hard_read` (confidence medium AND a legibility flag present)
  - `explanation_withheld` (explain.py withheld a field that failed its guard)
  - `explanation_failed` (explanations could not be authored at all)
  - `schema_invalid` (the record failed the schema)
- **Deliberately NOT flagged** (to avoid crying wolf): medium confidence on its own, a missing patient name, or a medication missing only its duration.
- **`should_escalate(record, tier_used)`** (advisory only, makes no model call): returns True only for a **fast-tier prescription** that looks hard: confidence low or medium, or a legibility flag present. Lab reports and clean reads stay on the fast tier. This is the generalised fix for the invented-dosing finding (see section 9).

### `guard.py` (deterministic medical-advice refusal guard, no model)
- `classify_question(text)` returns a `GuardResult(allowed, category, message)`. It matches the lowercased question against regex patterns in seven refuse categories; the first match wins. Empty input is allowed as a no-op; anything with no match is allowed as `retrieval`.
- **Refuse categories:** `diagnosis`, `severity`, `treatment_change`, `dosing_advice`, `prognosis`, `interpretation`, `general_advice`. Patterns are targeted at first-person "what does this mean for me / what should I do" phrasing, so third-person retrieval ("what did the doctor prescribe") stays allowed.
- **Constants:** `STANDING_NOTICE = "This is a record aid, not medical advice."`; `NO_MATCH_MESSAGE = "I have no record matching that."`; `REFUSAL_MESSAGE` (a fixed message that declines and offers to pull up records, list a past prescription, or build a summary instead).
- It is a **first-line floor, not a ceiling:** a keyword classifier can be evaded and can over-trigger. It is layered with the extraction and explanation prompts and the explain-time fidelity guard, never relied on alone.

### `explain.py` (plain-language, authored once under the fidelity guard)
Fills the three null plain-language fields: `diagnosis.plain_language`, each `medications[].purpose_plain`, each `investigations[].plain_note`. The safety property: these add **no claim absent from the source.** General facts about a named condition/drug/test are allowed; any claim about *this patient* (severity, cause, prognosis, what to do, or interpretation of a value) is not.

Design is **author → guard → gate**:
- **Author (judgment tier)** is given only the **names** of things (the diagnosis text, medicine names + form, test names), never the patient's values, flags, or dates. It therefore *structurally cannot* interpret them: it can explain what HbA1c is, but it never sees the number, so it cannot say "your sugar is high." It writes one short third-person sentence per term, or `null` when nothing safe can be said.
- **Guard has two layers.** A deterministic backstop catches mechanical leaks: no second person ("you/your"), no directive language, a length cap (≤ 300 chars, ≤ 2 sentences), and no number that was not already in the term. An independent adversarial **judge (judgment tier)** that *does* see the full record catches semantic leaks: a hallucinated entity not in the record, a patient-specific claim, or an interpreted value. The judge **fails safe**: any item without an explicit "pass" is treated as a fail.
- **Gate:** a field that fails is re-authored once with the objection as feedback (`max_retries=1`); if it still fails it is **withheld** (left null) and flagged `explanation_withheld:<field>`, which `validate.py` turns into a `needs_review` reason. **The worst case is a missing explanation, never a wrong one.**

### `ingest.py` (the orchestrator)
- `ingest_record(image_path, store, ...)` chains the pipeline: extract (fast) → escalate to judgment re-read if `should_escalate` → explain (judgment) → validate → `store.save` with the original retained. Returns `(record, IngestReport)`.
- **Graceful degradation:** a failed judgment re-read keeps the fast read and notes `escalation_failed`; a failed explanation stores the record with plain-language null and an `explanation_failed` flag (so a record always lands and is flagged, never lost). Only a failure of the initial extraction propagates.
- `ingest_ephemeral(image_path, ...)` runs the same pipeline into a throwaway store that is deleted immediately, persisting nothing. Used by the hosted demo so an uploaded document is processed live and kept only in the visitor's session.

### `store.py` (local persistence, no model)
- A `Store` is bound to a root directory. Layout: `<root>/records/<record_id>.json` and `<root>/originals/<record_id>.<ext>`. The same code serves the real store (`local_records/store/`, gitignored) and the demo store (`demo_cache/store/`, synthetic) by pointing at different roots.
- `save()` is an upsert with **atomic writes** (temp file + `os.replace`), so `timeline.py` can re-save a record after assigning an episode id. The original is located by `record_id` convention (no extra field on the record). `list()` skips a corrupt file rather than failing.

### `timeline.py` (episode clustering + longitudinal view, deterministic)
- `GAP_DAYS = 120`. Two records join the same episode when they are both **related** and **proximate**:
  - **related** = same provider (normalised name or clinic overlap), OR a shared medication key (the med name with form/unit/number tokens stripped), OR a shared diagnosis keyword (tokens ≥ 4 chars, minus stopwords). All **literal** string matches: there is no medical concept map, so a comorbidity mentioned on an unrelated visit (for example "Diabetic type-1" noted on a skin prescription) never drags that visit into a diabetes episode.
  - **proximate** = record dates within 120 days (if a date is missing, relatedness alone links).
- Grouping uses **union-find**. Episode ids are **stable**: a component that already contains an id keeps it (seeded by the earliest member), so adding records does not reshuffle ids.
- `build_timeline(records)` returns `Episode` objects: records ordered oldest-first within an episode, episodes ordered newest-first by their most recent date. Episode label = `primary provider · N records · month range`.
- Cross-condition recall ("show all my diabetes records" across different providers) is **search's** job, not clustering's; episodes stay tight.

### `search.py` (guarded keyword/field search, v1, deterministic)
- Every query passes the **guard first**, so a judgment question is refused before any search runs. Statuses: `refused`, `empty` (no content tokens), `no_match` (returns `NO_MATCH_MESSAGE`, never general medical knowledge), or `ok`.
- Matching is **literal**: strip a small stopword list, then **every** remaining query token must appear somewhere in a record's clinical fields (AND across tokens). `_token_matches` allows an exact token match or a substring only when both parts are ≥ 4 characters (the length floor stops a one-letter record token from matching inside a longer query word).
- Primary fields (provider name, diagnosis stated_text, medication name, investigation name) weigh 2.0; secondary fields weigh 1.0. Results sort by score then recency. Internal fields (ids, flags, filenames) are excluded from search.
- **No synonyms or stemming:** "hba1c" finds the lab, but "diabetes" does not unless that literal word is present. Search can only find what extraction captured. Stemming and synonyms are a later enhancement.

### `export.py` (doctor-ready summary)
- `render_summary(records, ...)` produces facts-only Markdown, grouped by episode (newest first), records oldest-first within. A header carries patient, period, generated date, and the standing notice.
- **Printed lab flags are shown exactly as stored** (for example `[HIGH]`); an `unknown` flag is suppressed. **Plain-language notes are deliberately omitted from the doctor export** (a doctor wants the facts, not the lay glosses). A `needs_review == Y` record is marked "(unverified extraction)". Helpers `filter_by_date_range` and `filter_by_episode` scope the export.

### `auth.py` (identity and per-user isolation, deterministic, no model)
- Decides who a request belongs to. The safety property is **isolation by construction**: a `user_id` picks a store root (see `app.store_root`) and no code path enumerates across users, so one user can never see another's records.
- Passwords are stored only as **PBKDF2-HMAC-SHA256** hashes (`hash_password` / `verify_password`, constant-time compare). Nothing here touches Streamlit, so it is fully unit-tested.
- **Real (local) account:** one account (you). `real_user()` reads `APP_USER`/`APP_USER_NAME` (defaults "me"/"You"); `real_password_required()` is true only if `APP_PASSWORD` is set; frictionless otherwise.
- **Demo accounts:** seeded from `config/demo_users.json` (committed), each with a name, a PBKDF2 hash, and a `password_hint` shown on the login screen (the data is fictional). `authenticate_demo(user_id, password)` verifies the hash.

### `app.py` (Streamlit UI)
- **Identity gate.** Nothing renders until the user signs in (`_login`). Real mode auto-signs-in as the single local account unless `APP_PASSWORD` is set; demo mode shows the seeded profiles so a reviewer can log in as one, then another, and see the isolation. Signing in resets per-user session state (`_reset_session_for`), so one profile's session uploads never leak into another's.
- **Per-user store.** `store_root(mode, user_id)` resolves each user to their own folder: `local_records/store/users/<id>` (real) or `demo_cache/users/<id>/store` (demo). Every data path (`all_records`, `find_record`, timeline, search, export, save) operates only on the signed-in user's store plus that user's session uploads.
- **Modes.** *Real mode* (a key present locally): uploads stored privately under `local_records/`. *Demo mode* (a deploy, or no key, or `APP_MODE=demo`): browsing the signed-in profile's synthetic archive under `demo_cache/`; where a key is configured, that profile can also upload, kept **only in the visitor's browser session** (never written to shared storage). A deploy always defaults to demo even if a key is set, so a hosted app never silently runs real mode.
- Sidebar shows an account card (mode + who is signed in + a log-out control where sign-out is meaningful). A demo profile lands on its populated timeline. Every record view shows the original scan next to the extraction and the standing notice. Editing (correct fields, mark reviewed) is offered only for stored records, never for a session-only demo upload.

---

## 9. The finding that shaped the pipeline (the escalation story)

Testing on real handwritten prescriptions surfaced a specific, repeatable failure: the **fast tier (Sonnet 5) confidently invented a plausible dosing** that was not on the page (a "7 days" duration for a cream that had no duration printed), and it kept doing so even after the extraction prompt was hardened (three runs). The **judgment tier (Opus 4.8) correctly left it null** three out of three. The lesson: for hard handwritten reads, **escalation to the stronger model, not a longer prompt, was the fix.** This is exactly what `should_escalate` encodes (fast-tier hard prescriptions get a judgment re-read), and it is why the two-tier design exists rather than a single model everywhere.

---

## 10. Evals and testing

**Test suite (built, passing): 132 tests via `pytest`.** They cover every part where consistency matters and the model is not involved: schema validation and the `needs_review` signals, the guard's refuse/allow categories, the explanation fidelity guard (that it never introduces a number or a directive), storage round-trips, the full ingest chain (including the escalation branch and graceful degradation), episode clustering, guarded search, the export format, the sign-in and per-user store isolation (password hashing, authenticate success/failure, and that two users resolve to different roots), and the eval scorer's own comparison logic.

**Real-document testing** against actual handwritten prescriptions is what produced the escalation finding in section 9.

**The eval scorer is built (`python eval/run_eval.py`).** It runs the real pipeline (`ingest.ingest_ephemeral`) over a labelled synthetic set (`eval/eval_set/`) and reports the four metrics; `eval/scoring.py` holds the testable comparison logic, `eval/refusal_prompts.json` the labelled guard prompts, and it writes a committed headline table to `eval/RESULTS.md` plus a per-check CSV log. `--strict` turns it into a CI gate on the safety metrics.

**The four eval metrics, and the latest baseline (over 4 synthetic samples):**
1. **Extraction accuracy** (field-level, normalised comparison): **97%** (103/106).
2. **Explanation fidelity** (an independent LLM judge, deliberately stricter than the in-pipeline guard, that the output adds no claim absent from the source): **93%** (13/14). The one flag is a diagnosis note that restated the patient's body site; it points at a place to tighten the author prompt.
3. **Correct refusal** (deterministic, over `refusal_prompts.json`): **100%** (27/27), zero advice prompts let through.
4. **Needs-review correctness:** **75%** (3/4). The single miss is escalation correctly clearing a handwritten read the label assumed would be flagged, not a defect.
   Numbers are honest baselines over a small synthetic set, not a large-scale accuracy claim.

There is also a product metric, **time-to-file** (how little friction a new upload takes), because the archive only accumulates if the upload habit sticks.

---

## 11. Output (what you get back)

**A structured record** (the schema in section 7), always shown next to the original scan.

**A doctor-ready export**, facts-only Markdown, grouped by episode. A real trimmed example from the synthetic sample set:

```markdown
# Medical records summary

**Patient:** Rahul Mehta (M, 32)
**Period:** 2026-01-15 to 2026-04-02
**Records:** 4
**Generated:** 2026-08-18

_This is a record aid, not medical advice. Digitised from the patient's own
documents; the original scans are the source of truth._

## Dr. Anil Rao · 2 records · Jan 2026 to Feb 2026

### 2026-01-15 · lab report
- **Provider:** Dr. Anil Rao · MedLab Diagnostics
- **Investigations:**
    - HbA1c = 7.8 % [HIGH] (ref < 5.7)
    - Fasting Plasma Glucose = 142 mg/dL [HIGH] (ref 70 - 100)
    - Total Cholesterol = 185 mg/dL (ref < 200)
- **Original on file:** sample_03_diabetes_lab.png

### 2026-02-10 · prescription
- **Provider:** Dr. Anil Rao · General Medicine · Sunrise Family Clinic
- **Diagnosis (as stated):** Acute pharyngitis
- **Medications:**
    - Amoxicillin 500 mg (Tablet): 1 tablet, twice daily, 5 days
- **Advice (verbatim):** Warm saline gargles. Rest and plenty of fluids.
- **Original on file:** sample_01_pharyngitis.png
```

Note the printed `[HIGH]` flags are carried through exactly as the report printed them, and the plain-language notes are left out of the doctor handover on purpose.

**Guard behaviour example:** "Is irreversible pulpitis serious?" is declined (severity); "Should I stop the Augmentin early?" is declined (treatment change); "When did I last see a dentist?" is answered by searching the records.

---

## 12. Privacy firewall

- **Committed (public):** synthetic, clearly fictional sample documents under `samples/` and their precomputed outputs under `demo_cache/`. The public demo runs on these only, is read-only, and needs no API key.
- **Local only (never committed, gitignored):** your real records and their originals under `local_records/`. They never leave your machine and are never uploaded to the hosted demo.
- **On the hosted demo:** uploads are processed live and kept only in the visitor's browser session (via `ingest_ephemeral`), never written to shared storage, so one visitor can never see another's document. `.streamlit/secrets.toml` is gitignored.
- **Per-user isolation:** every signed-in user reads and writes only their own store root (`.../users/<id>/...`); no code path enumerates across users. Demo profile passwords live only as PBKDF2 hashes in `config/demo_users.json`.

---

## 13. Modes, running, and deployment

**Run locally**
```bash
git clone https://github.com/sachinj9074/Medical-Records-Tracker.git
cd Medical-Records-Tracker
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
cp .env.example .env              # paste your own ANTHROPIC_API_KEY
streamlit run src/app.py
```
With a key present the app starts in **real mode** and signs you in as the single local account (frictionless unless `APP_PASSWORD` is set), against a private, gitignored store. Never commit `.env`.

**Deploy the public demo (Streamlit Community Cloud):** push the repo, choose New app, pick this repo, `main`, and `src/app.py`.
- *Browse-only (no key, zero cost):* deploy as-is; visitors sign in as a seeded demo profile and browse that profile's isolated synthetic archive.
- *Interactive (a signed-in profile can also upload):* set app **Secrets**: `APP_MODE="demo"`, a **spend-capped** `ANTHROPIC_API_KEY`, `MAX_UPLOADS_PER_SESSION="3"`. Uploads are then session-only. The deploy stays in demo mode regardless, so the public URL never runs persistent real mode.

**Config knobs (`.env` / secrets):** `ANTHROPIC_API_KEY`, `FAST_MODEL` (default `claude-sonnet-5`), `JUDGMENT_MODEL` (default `claude-opus-4-8`), `APP_MODE` (`demo`/`real`), `APP_USER` / `APP_USER_NAME` / `APP_PASSWORD` (the local account), `MAX_UPLOADS_PER_SESSION`.

---

## 14. Tech stack

- **Python 3.12**, **Streamlit** (single-page web app).
- **Anthropic Claude** via the official SDK, tiered: `claude-sonnet-5` (fast, everyday reading) and `claude-opus-4-8` (judgment: hard vision + the plain-language explanations).
- **jsonschema** (Draft 2020-12): the record shape is schema-defined and every extraction is validated.
- **Pillow** and **PyMuPDF**: image handling and rendering PDF pages to images for the vision model.
- **hashlib / hmac** (stdlib): PBKDF2 password hashing for sign-in and per-user isolation.
- **python-dotenv** (load the key locally), **pytest** (the 132-test suite).

---

## 15. Repository layout

```
README.md            # public-facing project readme (GitHub landing page)
PROJECT_SPEC.md      # the authoritative living spec (16 sections)
FRAMEWORK.md         # the build philosophy and deliberate deviations
PROJECT_BRIEF.md     # this file
requirements.txt
.env.example
.gitignore           # .env, .streamlit/secrets.toml, /local_records/
config/demo_users.json  # seeded demo profiles (names + PBKDF2 hashes + hints)
schemas/record.schema.json
src/
  app.py             # Streamlit UI (identity gate, per-user store)
  auth.py            # identity + per-user isolation (PBKDF2, deterministic)
  model.py           # tiered model access (fast/judgment), provider-swappable
  extract.py         # vision extraction -> structured record
  explain.py         # plain-language, fidelity-guarded (author -> guard -> gate)
  validate.py        # schema + needs_review + should_escalate (deterministic)
  guard.py           # medical-advice refusal guard (deterministic)
  ingest.py          # the orchestrator (chains the pipeline; ephemeral variant)
  store.py           # local record + original persistence (gitignored root)
  timeline.py        # episode clustering + timeline (union-find, deterministic)
  search.py          # guarded keyword/field search (deterministic)
  export.py          # doctor-ready Markdown summary
eval/
  eval_set/          # labelled synthetic samples (ground truth)
  run_eval.py        # the eval scorer (CLI: --metric / --samples / --strict)
  scoring.py         # comparison + fidelity-judge logic (unit-tested)
  refusal_prompts.json  # labelled advice/retrieval prompts for the guard metric
  RESULTS.md         # committed headline table (regenerated by run_eval)
  eval_log_template.csv
samples/             # fictional prescriptions and reports (committed)
demo_cache/users/<id>/store/   # per-profile synthetic records + originals (committed)
local_records/store/users/<id>/  # real records + originals (gitignored, never committed)
tests/               # 132 tests (pytest)
conftest.py          # makes `import src` work under pytest
.claude/launch.json  # dev-server config (streamlit, port 8520)
```

---

## 16. Honest limitations

- **It advises nothing.** Every output is a transcription, an organisation, or a neutral explanation, never a clinical judgment. This is a design choice, not a gap.
- **The eval set is small and synthetic.** The scorer is built and runs the real pipeline over four labelled samples; the numbers are honest baselines, not a large-scale accuracy claim. Growing the labelled set is the next step.
- **Episode clustering is literal and automatic.** It groups on exact provider/medicine/diagnosis-keyword matches within 120 days; it will not spot that two differently-named conditions are related, and manual episode merge/split is not implemented yet.
- **Search is literal.** No synonyms or stemming; it finds only what extraction captured.
- **Access control is demonstrable, not a hosted multi-tenant service.** There is a real sign-in and per-user store isolation (proven in the demo with seeded profiles), but no durable multi-tenant database, no federated login, and no encryption-at-rest of third-party PHI. Real personal use is single-user and local; those production pieces are on the roadmap.
- **The guard is a keyword floor.** It can be evaded or can over-trigger; it is one layer among several, not the whole safety story.

---

## 17. Roadmap

- A larger, more varied labelled eval set (the scorer exists; grow the data and re-baseline).
- Manual episode merge and split in the UI.
- Federated login (OIDC) and durable per-user storage for true multi-user hosting; backup/restore and record delete for real personal use.
- v2 chat retrieval, with the guard inside the chat path (retrieve, never compose).
- A short screen-recording walkthrough and screenshots in the README.
- PDF multi-document handling; broader document types (imaging reports, vaccination records).
- Search stemming/synonyms; lab-value trend plots with zero interpretive commentary.

**Done since the initial build:** (1) multi-user access control (sign-in + per-user store isolation, with seeded demo profiles); (2) the one-command eval scorer over the four metrics, with committed baselines in `eval/RESULTS.md`; (3) the value-forward UI overhaul (timeline home with overview tiles and episode cards, a "messy original vs clean reading" record detail, one-tap review, and a value-led doctor-summary export). All built and tested.

---

## 18. Glossary

- **Episode (of care):** a cluster of records that belong to one course of treatment and its follow-ups, grouped deterministically by related-and-proximate signals.
- **Fidelity guard:** the author → guard → gate machinery in `explain.py` that ensures a plain-language note states only general facts about a named term and never a claim about the patient.
- **Escalation:** re-reading a hard fast-tier prescription with the stronger judgment model.
- **needs_review:** a `Y`/`N` verdict set by code from real structural signals, telling the user to eyeball a record against its original.
- **Printed-flag rule:** a lab flag (H/L) is carried only if the report printed it; the tool never computes it.
- **Real mode vs demo mode:** a single local account with private storage of your own records, vs seeded demo profiles browsing isolated synthetic archives with optional session-only uploads.
- **Per-user isolation:** the `user_id` picks the store root, and no code path reads across users, so one user never sees another's records.
- **Firewall:** the separation that keeps real records local-and-gitignored while only synthetic data is committed or hosted.

---

## 19. FAQ (quick answers)

- **Does it diagnose or tell me what to do?** No. It reads, organises, and explains what a document says, and it refuses medical-advice questions.
- **What if it misreads a handwritten scan?** The original is always kept and shown, and a low-confidence read is flagged `needs_review`. A bad read is a convenience failure, not data loss.
- **Why two models?** A fast model handles clean reads cheaply; a stronger model is spent only on hard handwritten prescriptions and on writing the explanations. The split came from a real finding: the fast model invented dosing that the stronger model did not (section 9).
- **How does it avoid a friendly explanation quietly becoming medical advice?** The explanation author is never shown the patient's values or flags, only the names of terms, so it cannot interpret them; an adversarial judge that does see the record then checks each note and withholds anything unsafe.
- **Why can't it compute whether a lab value is high?** By design. Deciding a value is abnormal is a clinical judgment; the tool only carries a flag the report itself printed.
- **Why does search miss "diabetes" when I have a diabetes lab?** Search is literal, with no synonyms; it matches words that actually appear in the extracted fields. This is a known limitation with stemming/synonyms on the roadmap.
- **Is my data private on the live demo?** The public demo shows only synthetic records; if you upload, it is processed live and kept only in your browser session, never saved or shared. Real records, when you run it locally, stay on your machine and are never committed.
- **How does multi-user work, and can one user see another's records?** Every user signs in and reads/writes only their own store root (`.../users/<id>/...`); no code path enumerates across users, so no. The demo proves it with seeded profiles (log in as one, then another). It is a demonstrable access-control design, not a durable multi-tenant service: for real use it is single-user and local.
- **What is the safety line in one sentence?** This is a record aid, not medical advice.

---

*Deep dives: `PROJECT_SPEC.md` (the authoritative 16-section spec) and `FRAMEWORK.md` (the build philosophy).*
