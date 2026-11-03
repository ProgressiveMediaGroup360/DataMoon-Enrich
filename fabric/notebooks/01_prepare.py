# =============================================================================
# NOTEBOOK 01 - Prepare contacts
# Fabric notebook (PySpark, attach the PMG_LH Lakehouse, Runtime 2.0)
# mode "upload": read + normalise the uploaded file
# mode "specs" : build a read-only query from the spec form and pull contacts from PMG_DWH
# Output: Files/jobs/<job_id>/work/contacts.csv
# =============================================================================
import json
import os
import sys

sys.path.insert(0, "/lakehouse/default/Files/code")  # upload the repo's core/ folder here

import pandas as pd
import com.microsoft.spark.fabric
from com.microsoft.spark.fabric.Constants import Constants

from core.jobio import job_dir, write_status
from core.normalise import normalise_frame
from core.spec_query import build_query

# ---- parameters (mark this cell as the "parameters cell" in Fabric) -------------
job_id = ""
mode = "upload"          # upload | specs
max_rows = 100
WAREHOUSE = "PMG_DWH"
# ---------------------------------------------------------------------------------

try:
    d = job_dir(job_id)
    os.makedirs(f"{d}/work", exist_ok=True)
    write_status(job_id, "running", "prepare", 8, "Reading contacts")

    if mode == "upload":
        src_csv, src_xlsx = f"{d}/input/raw_upload.csv", f"{d}/input/raw_upload.xlsx"
        if os.path.exists(src_csv):
            raw = pd.read_csv(src_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        else:
            raw = pd.read_excel(src_xlsx, dtype=str).fillna("")
        if len(raw) > max_rows:
            raise ValueError(f"The file has {len(raw)} rows; the limit is {max_rows} per job.")
    elif mode == "specs":
        spec = json.load(open(f"{d}/input/spec.json"))
        query = build_query(spec, max_rows)      # validated + escaped; read-only SELECT
        raw = (spark.read.option(Constants.DatabaseName, WAREHOUSE).synapsesql(query)).toPandas()
    else:
        raise ValueError(f"unknown mode {mode!r}")

    df = normalise_frame(raw)
    df.to_csv(f"{d}/work/contacts.csv", index=False)
    write_status(job_id, "running", "prepare", 20, "", {"rows_in": len(df)})
    print(f"[01] {len(df)} contacts ready (from {len(raw)} input rows)")
    mssparkutils.notebook.exit(json.dumps({"rows": len(df)}))
except Exception as e:
    write_status(job_id, "failed", "prepare", 0, str(e)[:300])
    raise
