# Pre-QC integration

The existing n8n workflow ("Pre-QC: CSV vs manual specs", gpt-4.1, OK/REVIEW/MISMATCH per criterion,
PASS / HUMAN REVIEW / FLAG overall) is reused. It currently has only a form trigger, so it needs a
second entry point that takes rows and a spec as JSON and returns verdicts as JSON.

## Contract (implemented by `core/qc.py`)

```
POST <webhook>          header X-QC-Token: <shared secret>
{"job_id": "...", "spec_text": "...", "columns": [<template columns>], "rows": [{<column>: <value>}, ...]}

200 {"rows": [{"overall": "PASS" | "HUMAN REVIEW" | "FLAG",
               "checks": {"<criterion label>": {"status": "OK|REVIEW|MISMATCH", "comment": "..."}}}, ...]}
```
Same number of rows, same order. Rows are sent in chunks of 200. A missing webhook or empty spec text
is reported as `NOT_RUN`, never as PASS, and NOT_RUN rows are kept out of the template file.

## Changes needed in the n8n workflow

1. Add a **Webhook** node (POST, header auth) next to "Upload form". Map the JSON rows into the same
   shape "Check your input" produces, run the existing "Build spec request" -> "OpenAI: parse spec" ->
   "Plan checks" -> "OpenAI: judge values" -> "Assemble CSV" chain, then return the per-row result with
   a "Respond to Webhook" node instead of "Download checked CSV".
2. **Skip the three blank-by-design columns** (`Asset `, `Date Delivered`, `Source ID`) in "Plan checks",
   so empty values are not flagged. Note the template headers contain trailing/leading spaces.
3. **Fix band parsing** in "Assemble CSV" `parseBand`: it only recognises "and above / or more / + / over",
   so "UNDER 1 MILLION" and "<$1M" are read as the single value 1,000,000. Datamoon's revenue and employee
   ranges use both styles (`<$1M`, `UNDER 1 MILLION`, `5000+`, `1 BILLION AND OVER`), so add: leading
   `under`, `less than`, `<` -> `{lo: 0, hi: N}`.
4. Credentials in the exported JSON ("OpenAI API key", "Pre-QC log database") have blank ids; re-select
   them after import.

Everything else (two-pass judging in batches of 50, `preqc_runs` logging) stays as is.
