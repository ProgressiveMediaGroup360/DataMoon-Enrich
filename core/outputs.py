"""Build the two deliverables: the template file and the "everything" file."""
import pandas as pd

from .normalise import contact_key, is_valid_email
from .qc import FLAG, NOT_RUN, PASS, REVIEW
from .template import CONTENT_FIELDS, TEMPLATE_COLUMNS, to_template_row


def _completeness(rec: dict) -> int:
    return sum(1 for f in CONTENT_FIELDS if str(rec.get(f, "")).strip())


def template_candidates(records: list):
    """Rows that can go in the template file: must have an email, distinct per person.

    Returns (kept records, dropped_no_email, dropped_duplicates). Of several rows for the same
    person the most complete one is kept.
    """
    best, order, no_email = {}, [], 0
    for rec in records:
        if not is_valid_email(str(rec.get("email", "")).strip()):
            no_email += 1
            continue
        key = contact_key(rec)
        if key not in best:
            best[key] = rec
            order.append(key)
        elif _completeness(rec) > _completeness(best[key]):
            best[key] = rec
    kept = [best[k] for k in order]
    return kept, no_email, len(records) - no_email - len(kept)


def split_by_qc(records: list, qc: list):
    """PASS and HUMAN REVIEW rows go in the template file; FLAG rows go to rejects.
    NOT_RUN rows are kept out of the template file (they have not passed QC)."""
    ok, rejects, not_run = [], [], []
    for rec, q in zip(records, qc):
        v = q["overall"]
        if v in (PASS, REVIEW):
            ok.append((rec, q))
        elif v == FLAG:
            rejects.append((rec, q))
        else:
            not_run.append((rec, q))
    return ok, rejects, not_run


def template_frame(pairs) -> pd.DataFrame:
    return pd.DataFrame([to_template_row(rec) for rec, _ in pairs], columns=TEMPLATE_COLUMNS)


def qc_report_frame(pairs) -> pd.DataFrame:
    rows = []
    for rec, q in pairs:
        row = {"Email": rec.get("email", ""), "Contact Source Link": rec.get("linkedin_url", ""),
               "QC Overall": q["overall"]}
        for label, c in (q.get("checks") or {}).items():
            row[f"QC - {label}"] = c.get("status", "")
            row[f"Comment - {label}"] = c.get("comment", "")
        rows.append(row)
    return pd.DataFrame(rows)
