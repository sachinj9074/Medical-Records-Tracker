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

- The public demo runs on synthetic, clearly fictional records only
  (`demo_cache/`, generated from the `samples/` documents). It is read-only and
  needs no API key.
- Your real records stay local under `local_records/`, are gitignored, and are
  never committed or uploaded to the hosted demo.
- The original uploaded image is always retained and shown alongside the
  extraction. The original is the source of truth; the structured data is a
  convenience layer that can be wrong.

## Status

Complete. Extraction, the fidelity-guarded explanations, needs-review flagging,
the refusal guard, episode clustering, search, export, and the Streamlit UI are
all built and tested. See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full spec
and [FRAMEWORK.md](FRAMEWORK.md) for the design decisions behind it.

## Run locally

```
pip install -r requirements.txt
cp .env.example .env      # then paste your own Anthropic API key into .env
streamlit run src/app.py
```

With a key present, the app starts in **real mode** and uses a private, gitignored
store under `local_records/`. Never commit `.env`.

## Modes

- **Real mode** (a key is present): upload your own documents; they are stored
  locally under `local_records/` and never leave your machine.
- **Demo mode** (no key present, or `APP_MODE=demo`): read-only browsing of the
  synthetic records in `demo_cache/`, with uploads disabled. This is what the
  hosted public demo runs, so it needs no API key and touches no real data.

## Deploy the public demo (Streamlit Community Cloud)

The hosted app carries no API key, so it falls back to demo mode automatically.

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), sign in with GitHub and
   choose **New app**.
3. Pick this repo, the `main` branch, and `src/app.py` as the main file.
4. Deploy. (No secrets required for the demo. To force the mode explicitly, add
   `APP_MODE = "demo"` under the app's **Secrets**.)

Do not put a real API key in the hosted app's secrets: real mode is for local
use only, so the public URL can never spend your key or see real records.
