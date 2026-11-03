# =============================================================================
# NOTEBOOK 03 - Quality check and publish
# Builds the template file (PASS + HUMAN REVIEW rows), rejects, QC report and the
# "everything" file; logs the job to PMG_DWH.dbo.datamoon_jobs; marks the job complete.
# =============================================================================
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/lakehouse/default/Files/code")

import pandas as pd
import com.microsoft.spark.fabric
from com.microsoft.spark.fabric.Constants import Constants

from core.jobio import job_dir, write_status
from core.normalise import contact_key
from core.outputs import qc_report_frame, split_by_qc, template_candidates, template_frame
from core.qc import run_qc
from core.template import TEMPLATE_COLUMNS, to_template_row

# ---- parameters ------------------------------------------------------------------
job_id = ""
mode = "upload"
WAREHOUSE = "PMG_DWH"
KEYVAULT = "fabric-apis"
QC_URL_SECRET = "PreQC-webhook-url"      # leave the secret out to skip QC (reported as NOT_RUN)
QC_TOKEN_SECRET = "PreQC-webhook-token"
STORE_RAW_JSON_IN_EVERYTHING = True       # see README "Sensitive data" - decision pending
# -----------------------------------------------------------------------------------


def secret(name):
    try:
        return mssparkutils.credentials.getSecret(KEYVAULT, name)
    except Exception:
        return ""


d = job_dir(job_id)
try:
    write_status(job_id, "running", "publish", 82, "Quality check")
    data = json.load(open(f"{d}/work/enriched.json"))
    meta = json.load(open(f"{d}/meta.json"))
    spec_text = open(f"{d}/input/spec_text.txt").read()
    counts = data["counts"]

    merged = [r["merged"] for r in data["rows"]]
    candidates, dropped_no_email, dropped_dups = template_candidates(merged)

    rows = [to_template_row(r) for r in candidates]
    qc = run_qc(secret(QC_URL_SECRET), secret(QC_TOKEN_SECRET), job_id, spec_text, TEMPLATE_COLUMNS, rows)
    ok, rejects, not_run = split_by_qc(candidates, qc)

    os.makedirs(f"{d}/outputs", exist_ok=True)
    template_frame(ok).to_csv(f"{d}/outputs/template.csv", index=False, encoding="utf-8-sig")
    template_frame(rejects).to_csv(f"{d}/outputs/rejects.csv", index=False, encoding="utf-8-sig")
    qc_report_frame(ok + rejects + not_run).to_csv(f"{d}/outputs/qc_report.csv", index=False, encoding="utf-8-sig")

    # ---- "everything": every input row + enrichment details + QC verdict (where it was checked)
    verdict = {contact_key(rec): q["overall"] for rec, q in ok + rejects + not_run}
    rows_all = []
    for orig, r in zip(data["original"], data["rows"]):
        row = dict(orig)
        row.update({f"merged_{k}": v for k, v in r["merged"].items() if k in orig and v != orig.get(k)})
        m = r["merged"]
        row["qc_overall"] = verdict.get(contact_key(m), "") if str(m.get("email", "")).strip() else ""
        row["template_status"] = ("no email - excluded" if not str(m.get("email", "")).strip()
                                  else {"PASS": "in template", "HUMAN REVIEW": "in template (review)", "FLAG": "rejected by QC",
                                        "NOT_RUN": "not delivered (QC not run)"}.get(row["qc_overall"], "duplicate - merged into another row"))
        row.update({"dm_status": r["status"], "dm_endpoint": r["endpoint"], "dm_match_confidence": r["confidence"],
                    "dm_match_reasons": "; ".join(r["reasons"]), "dm_fields_changed": ", ".join(r["changed"]),
                    "dm_error": r["error"]})
        if STORE_RAW_JSON_IN_EVERYTHING:
            row["dm_raw_json"] = json.dumps(r["datamoon_json"]) if r["datamoon_json"] else ""
        rows_all.append(row)
    pd.DataFrame(rows_all).to_csv(f"{d}/outputs/everything.csv", index=False, encoding="utf-8-sig")

    n_by = lambda v: sum(1 for _, q in ok + rejects + not_run if q["overall"] == v)
    final = {"rows_in": len(data["rows"]), **counts, "template_rows": len(ok),
             "qc_pass": n_by("PASS"), "qc_review": n_by("HUMAN REVIEW"), "qc_flag": n_by("FLAG"),
             "qc_not_run": len(not_run), "dropped_no_email": dropped_no_email, "dropped_duplicates": dropped_dups}

    log = pd.DataFrame([{
        "job_id": job_id, "mode": mode, "client_name": meta.get("client_name") or None, "status": "complete",
        "rows_in": final["rows_in"], "enriched": counts.get("enriched", 0), "skipped_recent": counts.get("skipped_recent", 0),
        "no_match": counts.get("no_match", 0), "errors": counts.get("errors", 0), "template_rows": final["template_rows"],
        "qc_pass": final["qc_pass"], "qc_review": final["qc_review"], "qc_flag": final["qc_flag"], "qc_not_run": final["qc_not_run"],
        "spec_json": open(f"{d}/input/spec.json").read() if mode == "specs" else None,
        "finished_at": datetime.now(timezone.utc).replace(tzinfo=None)}])
    spark.createDataFrame(log).write.mode("append").synapsesql(f"{WAREHOUSE}.dbo.datamoon_jobs")

    note = "Quality check was not run (no QC service or no specs), so the template file is empty." if not_run and not ok else ""
    write_status(job_id, "complete", "publish", 100, note, final)
except Exception as e:
    write_status(job_id, "failed", "publish", 0, str(e)[:300])
    raise
