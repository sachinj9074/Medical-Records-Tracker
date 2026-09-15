# Deploying the hosted app

The app runs on Streamlit Community Cloud (free) with Cloudflare R2 (free tier)
as durable, encrypted storage for real accounts. This is a personal-scale setup
for you and a few trusted people, not a compliance-grade medical service.

There are two shapes you can deploy:

- **Zero-cost showcase (no API key).** The public demo works with no key at all:
  visitors browse the seeded profiles and run the pre-baked sample extractions.
  Nothing calls the API. Real accounts can be created but uploads are disabled.
- **Full app (API key + R2).** Adds two things: real users get durable, encrypted
  archives, and both demo visitors and real users can read live documents (each
  capped). You pay the Anthropic API for every live read.

The steps below set up the full app. For the zero-cost showcase, skip Part A and
set only `ANTHROPIC_API_KEY` (optional) in Part C.

Everything the app reads is listed in `.env.example`.

---

## Part A: create the Cloudflare R2 bucket

R2 is Cloudflare's S3-compatible object storage. The free tier (about 10 GB and
generous monthly operations) comfortably covers this project; the real cost is
the Anthropic API, not storage.

1. Sign in at [dash.cloudflare.com](https://dash.cloudflare.com) (a free account
   is fine). In the left sidebar, open **R2**. Enabling R2 may ask you to add a
   payment card even though the free tier is free; that is Cloudflare's gate, not
   a charge for this usage.
2. **Create a bucket.** Give it a name, for example `medical-records`, pick a
   location near you, and create it. This name is your `R2_BUCKET`.
3. **Find your S3 API endpoint.** On the R2 overview (or the bucket's Settings)
   you will see an S3 API address of the form
   `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. That whole URL is your
   `R2_ENDPOINT_URL`.
4. **Create an API token.** Go to **R2 > Manage R2 API Tokens > Create API
   token**. Choose **Object Read & Write**, and scope it to the single bucket you
   just made (least privilege). Create it. Cloudflare shows an **Access Key ID**
   and a **Secret Access Key** once: copy both now.
   - Access Key ID  -> `R2_ACCESS_KEY_ID`
   - Secret Access Key -> `R2_SECRET_ACCESS_KEY`

You now have four values: bucket, endpoint URL, access key id, secret access key.

---

## Part B: get an Anthropic API key

1. In the [Anthropic console](https://console.anthropic.com), create an API key.
2. Set a **hard monthly spend limit** on the key. The app's per-user and demo
   caps bound usage, but a spend limit is the backstop.

This is your `ANTHROPIC_API_KEY`. Skip this part only for the zero-cost showcase.

---

## Part C: point the deployed app at them (Streamlit secrets)

1. Push the repo to GitHub (already done for `main`).
2. At [share.streamlit.io](https://share.streamlit.io), choose **New app**, pick
   this repo, the `main` branch, and `src/app.py`. (If the app already exists,
   skip to its **Settings**.)
3. Open the app's **Settings > Secrets** and paste this TOML, filling in your
   values:

   ```toml
   ANTHROPIC_API_KEY   = "sk-ant-..."
   R2_BUCKET           = "medical-records"
   R2_ENDPOINT_URL     = "https://<ACCOUNT_ID>.r2.cloudflarestorage.com"
   R2_ACCESS_KEY_ID    = "..."
   R2_SECRET_ACCESS_KEY = "..."

   # invite code for "Use it for real": only people you give it to can reach
   # sign-in or account creation. Strongly recommended on a public deploy.
   REAL_ACCESS_CODE    = "pick-a-shared-code"

   # optional, these are the defaults
   DEMO_LIVE_UPLOADS   = "2"    # live "try your own file" trials per demo session
   REAL_UPLOADS_PER_DAY = "25"  # documents a real account may read per day
   ```

   Leave `REAL_ACCESS_CODE` blank (or unset) only if you want anyone to be able
   to create a real account. With it set, share the code with your invitees; each
   of them still creates their own password-protected account. Rotate it any time
   by changing this value (everyone re-enters the new code once).

4. Save. Streamlit reboots the app with the new secrets.

Secrets live only in Streamlit's secret store (and, for local runs, in a
git-ignored `.env`). Never commit them: the repo is public.

---

## Part D: verify it works

1. Open the app. You should land on the **Explore the demo / Use it for real**
   screen.
2. **Demo:** enter a profile, open **Add a document**, and run a pre-baked
   sample. It should render a real reading with no error.
3. **Real:** choose **Use it for real > Create an account**, make one, and upload
   a document. It should be read and filed. You should **not** see the "durable
   cloud storage is not configured" notice (that notice means R2 is not wired up).
4. **Durability check:** from the app's menu, **Reboot** the app (or just come
   back later), sign in again, and confirm your record is still there. That
   proves records are in R2, not on the ephemeral Streamlit disk.
5. In the Cloudflare R2 bucket you will see keys under `accounts/` and
   `users/<your-id>/`. The record and image objects are ciphertext: they cannot
   be read without the account password.

---

## What to tell the people you invite

- Their records are encrypted with **their** password. If they forget it, the
  records **cannot be recovered** (there is no reset in this version).
- Every document they upload runs on your API key, within the per-day cap.
- This is a personal tool shared with people you trust, not a regulated medical
  service. It never gives medical advice; it organises and explains records.

---

## Running it yourself instead (no cloud)

You do not need R2 to use the app privately. Run it locally (see the README):
with no `R2_*` set, real accounts are stored in a git-ignored, still-encrypted
`local_records/cloud/` folder on your own machine. That is durable and private,
but only reachable from that machine.
