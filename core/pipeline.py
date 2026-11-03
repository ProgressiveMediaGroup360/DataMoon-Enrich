"""The enrich -> QC -> outputs pipeline as plain functions (no Spark, no web framework).

Used by the Railway app now; the Fabric notebooks can call the same functions later.
`cache` is anything with get(contact_key) -> {"at", "confidence", "mapped"} | None and
put(result, now, job_id). `qc_fn(rows) -> list of {"overall", "checks"}`.
"""
import json
from typing import Callable, Optional

import pandas as pd

from .enrich import enrich_record
from .normalise import contact_key
from .outputs import qc_report_frame, split_by_qc, template_candidates, template_frame
from .qc import FLAG, NOT_RUN, PASS, REVIEW
from .template import TEMPLATE_COLUMNS, to_template_row


class StopJob(Exception):
    """Raised when continuing would only waste Datamoon calls (e.g. the key is rejected)."""


def enrich_all(records: list, client, cache, now, job_id: str = "", on_progress: Optional[Callable] = None,
               min_age_days: int = 90, max_auth_failures: int = 3):
    results, counts = [], {"enriched": 0, "skipped_recent": 0, "no_match": 0, "errors": 0, "no_lookup_key": 0}
    auth_failures = 0
    n = len(records)
    for i, rec in enumerate(records):
        cached = cache.get(contact_key(rec)) if cache else None
        res = enrich_record(client, rec, now, last_enriched_at=cached["at"] if cached else None,
                            cached=cached, min_age_days=min_age_days)
        results.append(res)
        counts[{"error": "errors"}.get(res.status, res.status)] += 1
        auth_failures = auth_failures + 1 if res.api_status in (401, 403) else 0
        if auth_failures >= max_auth_failures:
            raise StopJob("Datamoon rejected the API key several times in a row, so the job was stopped.")
        if cache and res.status in ("enriched", "no_match"):
            cache.put(res, now, job_id)
        if on_progress:
            on_progress(i + 1, n, counts)
    return results, counts


def build_outputs(originals: list, results: list, qc_fn: Callable, store_raw_json: bool = True) -> dict:
    """Return {"frames": {name: DataFrame}, "counts": {...}, "qc_note": str}."""
    merged = [r.merged for r in results]
    candidates, dropped_no_email, dropped_dups = template_candidates(merged)
    rows = [to_template_row(c) for c in candidates]

    qc_note = ""
    try:
        qc = qc_fn(rows) if rows else []
    except Exception as e:  # QC service down / bad reply: deliver the enriched data, say QC did not run
        qc = [{"overall": NOT_RUN, "checks": {}} for _ in rows]
        qc_note = f"The quality check service could not be reached ({type(e).__name__})."
    if qc and all(q["overall"] == NOT_RUN for q in qc) and not qc_note:
        qc_note = "The quality check was not run (no QC service configured or no specs)."

    ok, rejects, not_run = split_by_qc(candidates, qc)
    verdict = {contact_key(rec): q["overall"] for rec, q in ok + rejects + not_run}

    everything = []
    for orig, r in zip(originals, results):
        row = dict(orig)
        m = r.merged
        has_email = bool(str(m.get("email", "")).strip())
        overall = verdict.get(contact_key(m), "") if has_email else ""
        row.update({
            "dm_status": r.status, "dm_endpoint": r.endpoint, "dm_match_confidence": r.confidence,
            "dm_match_reasons": "; ".join(r.reasons), "dm_fields_changed": ", ".join(r.changed),
            "dm_error": r.error, "qc_overall": overall,
            "template_status": ("no email - excluded" if not has_email else
                                {PASS: "in template", REVIEW: "in template (review)", FLAG: "rejected by QC",
                                 NOT_RUN: "not checked (QC did not run)"}.get(overall, "duplicate - merged into another row")),
        })
        row.update({f"enriched_{k}": v for k, v in m.items() if k in orig and v != orig.get(k)})
        if store_raw_json:
            row["dm_raw_json"] = json.dumps(r.identity) if r.identity else ""
        everything.append(row)

    def n_by(v):
        return sum(1 for _, q in ok + rejects + not_run if q["overall"] == v)

    frames = {
        "template": template_frame(ok),
        "rejects": template_frame(rejects),
        "qc_report": qc_report_frame(ok + rejects + not_run),
        "everything": pd.DataFrame(everything),
    }
    if not_run:
        frames["template_unchecked"] = template_frame(not_run)
    counts = {"template_rows": len(ok), "qc_pass": n_by(PASS), "qc_review": n_by(REVIEW), "qc_flag": n_by(FLAG),
              "qc_not_run": len(not_run), "dropped_no_email": dropped_no_email, "dropped_duplicates": dropped_dups}
    return {"frames": frames, "counts": counts, "qc_note": qc_note}
