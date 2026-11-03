"""Client for the Pre-QC service (the existing n8n workflow, once it has a webhook entry).

Contract (defined here; the n8n side still has to implement it - see qc/README.md):

  POST <QC_WEBHOOK_URL>            header: X-QC-Token: <QC_WEBHOOK_TOKEN> (optional)
  {"job_id": "...", "spec_text": "...", "columns": [...], "rows": [{<template column>: value}, ...]}

  200 {"rows": [{"overall": "PASS" | "HUMAN REVIEW" | "FLAG",
                 "checks": {"<label>": {"status": "OK|REVIEW|MISMATCH", "comment": "..."}}}, ...]}
  (same length and order as the request rows)

If no URL is configured the QC step is reported as NOT_RUN - never as PASS.
"""
import requests

PASS, REVIEW, FLAG, NOT_RUN = "PASS", "HUMAN REVIEW", "FLAG", "NOT_RUN"
CHUNK = 200


def run_qc(url, token, job_id, spec_text, columns, rows, session=None, timeout=600):
    """Return a list of {"overall", "checks"} dicts, one per row (NOT_RUN if QC is unavailable)."""
    if not url or not (spec_text or "").strip():
        why = "QC_WEBHOOK_URL not set" if not url else "no spec text supplied"
        return [{"overall": NOT_RUN, "checks": {}, "note": why} for _ in rows]

    http = session or requests
    headers = {"X-QC-Token": token} if token else {}
    out = []
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        r = http.post(url, json={"job_id": job_id, "spec_text": spec_text, "columns": columns, "rows": chunk},
                      headers=headers, timeout=timeout)
        r.raise_for_status()
        results = r.json().get("rows")
        if not isinstance(results, list) or len(results) != len(chunk):
            raise ValueError("QC response does not match the request rows")
        for item in results:
            overall = item.get("overall")
            if overall not in (PASS, REVIEW, FLAG):
                raise ValueError(f"unexpected QC verdict: {overall!r}")
            out.append({"overall": overall, "checks": item.get("checks") or {}})
    return out
