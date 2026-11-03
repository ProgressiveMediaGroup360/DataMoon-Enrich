# DataMoon-Enrich

Drop a contact file → enrich with Datamoon → quality check (Pre-QC) → download files.
Phase 1 is stand-alone: no Fabric. Fabric storage and the "build from specs" mode are phase 2.

## What it does
1. Reads a CSV/XLSX (max `MAX_ROWS_PER_JOB` rows), maps columns, de-duplicates.
2. Looks each person up in Datamoon by email, then phone. A match counts as verified only if LinkedIn, or name + company/email, agrees. Verified values overwrite; the input LinkedIn URL is never overwritten.
3. A person enriched less than 90 days ago is not sent to Datamoon again (stored result reused).
4. Rows with an email go into the standard 23-column template. `Asset`, `Date Delivered`, `Source ID` stay empty; `Contact Source Link` = LinkedIn URL.
5. The template rows are sent to the n8n **Pre-QC (API)** workflow. PASS and HUMAN REVIEW → `template.csv`; FLAG → `rejects.csv`. If QC is unavailable, rows are never marked PASS: you get `template_unchecked.csv` instead.
6. `qc_report.csv` (per-check results) and `everything.csv` (all rows, all Datamoon fields, status per row) are always produced.

## Deploy on Railway
1. New service from GitHub repo `ProgressiveMediaGroup360/DataMoon-Enrich` (the Dockerfile is used).
2. Add a **volume** mounted at `/data` (keeps results and the 90-day cache across deploys).
3. Variables (see `.env.example`): `ACCESS_TOKEN` (24+ random characters), `DATAMOON_API_KEY`, `QC_WEBHOOK_URL`, `QC_WEBHOOK_TOKEN`.
4. Open `https://<your-domain>/s/<ACCESS_TOKEN>/`. Anyone with that link can use it; keep it private.

## Pre-QC (API) in n8n
`n8n/make_api_copy.py` builds the workflow from the original Pre-QC export (the original is untouched):
`python3 n8n/make_api_copy.py PreQC_export.json PreQC_API.n8n.json`, then import it, set the OpenAI (and optional Postgres log) credentials, create a **Header Auth** credential (name `X-QC-Token`, value = a secret), activate, and put the production webhook URL and that secret into Railway. Contract: `qc/README.md`.

## Limits and protection
Secret URL path, row cap, upload size cap, hourly job limits (global and per IP), random job ids, files deleted after `RETENTION_DAYS`.

## Tests
`python3 -m pytest -q tests`

## Phase 2 (not used yet)
`fabric/` (tables, notebooks, pipeline), `core/spec_query.py`, `core/jobio.py`.

## Known unknowns
The real Datamoon response shape for a match (field mapping in `core/mapper.py` is unverified), the phone endpoint's parameter name, and the rate-limit window.
