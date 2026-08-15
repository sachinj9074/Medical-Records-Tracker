# Medical Records and Prescription Tracker

Make your own old, messy medical records findable and understandable.

Upload a prescription or report soon after a visit. The tool reads it (including
handwritten and low-quality images), structures it, explains it in plain
language, files it on a timeline, and lets you find it again months later. It can
also export a clean summary to hand to a new doctor.

## What this is not

This is a record aid, not medical advice. It explains and organises what a
document says. It never diagnoses, never assesses severity, and never recommends
starting, stopping, or changing any treatment. A deterministic guard refuses
medical-advice questions and redirects to a doctor. See
[PROJECT_SPEC.md](PROJECT_SPEC.md).

## Privacy firewall

- The public demo runs on synthetic, clearly fictional sample records only
  (`samples/`).
- Your real records stay local under `local_records/`, are gitignored, and are
  never committed or uploaded to the hosted demo.
- The original uploaded image is always retained and shown alongside the
  extraction. The original is the source of truth; the structured data is a
  convenience layer that can be wrong.

## Status

Early scaffold. See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full spec and build
order, and [FRAMEWORK.md](FRAMEWORK.md) for the design decisions behind it.

## Setup (once implemented)

```
pip install -r requirements.txt
cp .env.example .env
streamlit run src/app.py
```

You paste your own API key into `.env`. Never commit `.env`.
