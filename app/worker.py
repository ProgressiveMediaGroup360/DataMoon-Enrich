"""Runs one job in a background thread: read -> normalise -> enrich -> QC -> write outputs."""
import io
import logging
from datetime import datetime, timezone

import pandas as pd

from .textio import decode_csv
from core.datamoon import DatamoonClient
from core.enrich import LinkedInLookup
from core.normalise import normalise_frame
from core.pipeline import StopJob, build_outputs, enrich_all
from core.qc import run_qc
from core.template import TEMPLATE_COLUMNS

log = logging.getLogger("worker")

OUTPUT_FILES = {"template": "template.csv", "rejects": "rejects.csv", "qc_report": "qc_report.csv",
                "everything": "everything.csv", "template_unchecked": "template_unchecked.csv"}


class UserError(Exception):
    """A problem the person can fix; its message is shown on the page."""


def _status(store, job_id, status, stage, pct, message="", counts=None):
    store.write_json(job_id, "status.json", {
        "job_id": job_id, "status": status, "stage": stage, "pct": pct, "message": message,
        "counts": counts or {}, "updated_at": datetime.now(timezone.utc).isoformat()})


def read_input(store, job_id: str) -> pd.DataFrame:
    for ext in ("csv", "xlsx"):
        rel = f"input/raw_upload.{ext}"
        if store.exists(job_id, rel):
            p = store.path(job_id, rel)
            try:
                if ext == "csv":
                    return pd.read_csv(io.StringIO(decode_csv(p.read_bytes())), dtype=str, keep_default_na=False)
                return pd.read_excel(p, dtype=str).fillna("")
            except Exception:
                raise UserError("The file could not be read. Check that it is a valid CSV or Excel file.")
    raise UserError("No uploaded file was found for this job.")


def run_job(job_id: str, settings, store, cache, client_factory=None, qc_fn=None):
    client_factory = client_factory or DatamoonClient   # resolved at call time so tests can swap them
    qc_fn = qc_fn or run_qc
    try:
        spec_text = store.read_bytes(job_id, "input/spec_text.txt").decode("utf-8")
        qc_on = bool(spec_text.strip())     # no specs saved = quality check switched off
        _status(store, job_id, "running", "prepare", 8, "Reading the file")
        raw = read_input(store, job_id)
        if len(raw) > settings.max_rows:
            raise UserError(f"The file has {len(raw)} rows; the limit is {settings.max_rows} per job.")
        df = normalise_frame(raw)
        records = df.to_dict(orient="records")
        n = len(records)
        if n == 0:
            raise UserError("The file has no usable rows.")
        _status(store, job_id, "running", "enrich", 15, f"{n} contacts ready", {"rows_in": n})

        client = client_factory(settings.datamoon_api_key, cooldown_sec=settings.rate_cooldown_sec)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        def progress(done, total, counts):
            if done % 3 == 0 or done == total:
                _status(store, job_id, "running", "enrich", 15 + int(65 * done / total),
                        f"Enriched {done} of {total}", {"rows_in": total, **counts})

        li = None
        if settings.linkedin_endpoint and settings.linkedin_endpoint.lower() != "off":
            li = LinkedInLookup(settings.linkedin_endpoint,
                                [p.strip() for p in settings.linkedin_params.split(",") if p.strip()])
        results, counts = enrich_all(records, client, cache, now, job_id=job_id, on_progress=progress, linkedin=li)

        _status(store, job_id, "running", "publish", 82, "Running the quality check" if qc_on else "Building files", {"rows_in": n, **counts})
        out = build_outputs(
            records, results, qc_enabled=qc_on,
            qc_fn=lambda rows: qc_fn(settings.qc_url, settings.qc_token, job_id, spec_text, TEMPLATE_COLUMNS, rows))
        for name, frame in out["frames"].items():
            store.write_bytes(job_id, f"outputs/{OUTPUT_FILES[name]}",
                              frame.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"))
        note = out["qc_note"]
        if li is not None and li.disabled_reason:
            note = (li.disabled_reason + ". " + note).strip()
        if counts.get("no_lookup_key", 0) == n:
            note = ("None of the %d rows has an email, phone or LinkedIn URL that could be recognised, so nothing was "
                    "sent to Datamoon. Check the column names (for example 'Email', 'Phone', 'LinkedIn'). %s" % (n, note)).strip()
        elif counts.get("no_lookup_key", 0):
            note = ("%d of %d rows had no email or phone and were not looked up. %s" % (counts["no_lookup_key"], n, note)).strip()
        _status(store, job_id, "complete", "publish", 100, note, {"rows_in": n, **counts, **out["counts"]})
    except UserError as e:
        _status(store, job_id, "failed", "prepare", 0, str(e))
    except StopJob as e:
        _status(store, job_id, "failed", "enrich", 0, str(e))
    except Exception:  # never show internals on the page
        log.exception("job %s failed", job_id)
        _status(store, job_id, "failed", "error", 0, "Something went wrong while processing. Please try again.")
