# =============================================================================
# NOTEBOOK 02 - Enrich with Datamoon
# Reads work/contacts.csv, applies the 3-month cache rule against
# PMG_DWH.dbo.datamoon_enrichment_latest, calls Datamoon for the rest, appends new
# results to PMG_DWH.dbo.datamoon_enrichment. Output: work/enriched.json
# =============================================================================
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/lakehouse/default/Files/code")

import pandas as pd
import com.microsoft.spark.fabric
from com.microsoft.spark.fabric.Constants import Constants

from core.datamoon import DatamoonClient
from core.enrich import enrich_record
from core.jobio import job_dir, write_status
from core.normalise import contact_key

# ---- parameters ------------------------------------------------------------------
job_id = ""
WAREHOUSE = "PMG_DWH"
KEYVAULT = "fabric-apis"                  # same convention as the old audiencelab notebooks
SECRET_NAME = "Datamoon-api-key"          # create this secret in Key Vault
COOLDOWN_SEC = 60                         # pause when the quota is nearly used; window not known yet
# -----------------------------------------------------------------------------------

d = job_dir(job_id)
try:
    df = pd.read_csv(f"{d}/work/contacts.csv", dtype=str, keep_default_na=False)
    records = df.to_dict(orient="records")
    n = len(records)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # ---- cache: latest enrichment per contact ---------------------------------------
    keys = [contact_key(r) for r in records]
    cache = {}
    try:
        latest = spark.read.synapsesql(f"{WAREHOUSE}.dbo.datamoon_enrichment_latest").toPandas()
        latest = latest[latest["contact_key"].isin(set(k for k in keys if k))]
        for row in latest.itertuples():
            cache[row.contact_key] = {
                "at": pd.to_datetime(row.enriched_at).to_pydatetime().replace(tzinfo=None),
                "confidence": row.match_confidence or "",
                "mapped": json.loads(row.mapped_json) if row.mapped_json else {},
            }
    except Exception as e:                       # first run: table is empty / view missing -> no cache
        print(f"[02] cache unavailable ({type(e).__name__}); enriching everything")

    client = DatamoonClient(mssparkutils.credentials.getSecret(KEYVAULT, SECRET_NAME), cooldown_sec=COOLDOWN_SEC)

    results, new_rows, counts, auth_failures = [], [], {"enriched": 0, "skipped_recent": 0, "no_match": 0, "errors": 0, "no_lookup_key": 0}, 0
    for i, rec in enumerate(records):
        c = cache.get(keys[i])
        res = enrich_record(client, rec, now, last_enriched_at=c["at"] if c else None, cached=c)
        results.append(res)

        k = {"error": "errors"}.get(res.status, res.status)
        counts[k] = counts.get(k, 0) + 1
        auth_failures = auth_failures + 1 if res.api_status in (401, 403) else 0
        if auth_failures >= 3:
            raise RuntimeError("Datamoon rejected the API key 3 times in a row; stopping to avoid wasted calls.")

        if res.status in ("enriched", "no_match", "error"):
            new_rows.append({
                "contact_key": res.contact_key or "unknown", "person_id": str(rec.get("person_id", "")) or None,
                "job_id": job_id, "enriched_at": now, "lookup_endpoint": res.endpoint or None,
                "status": res.status, "match_confidence": res.confidence or None,
                "match_reasons": "; ".join(res.reasons)[:1000] or None,
                "mapped_json": json.dumps(res.mapped) if res.mapped else None,
                "datamoon_json": json.dumps(res.identity) if res.identity else None,
            })
        if i % 5 == 0 or i == n - 1:
            write_status(job_id, "running", "enrich", 20 + int(60 * (i + 1) / max(n, 1)),
                         f"Enriched {i + 1} of {n}", {"rows_in": n, **counts})

    # errors are logged for audit but excluded from the cache view (status filter in the view)
    if new_rows:
        (spark.createDataFrame(pd.DataFrame(new_rows)).write.mode("append")
              .synapsesql(f"{WAREHOUSE}.dbo.datamoon_enrichment"))

    out = [{"merged": r.merged, "status": r.status, "endpoint": r.endpoint, "confidence": r.confidence,
            "reasons": r.reasons, "changed": r.changed, "error": r.error,
            "datamoon_json": r.identity} for r in results]
    json.dump({"counts": counts, "rows": out, "original": records}, open(f"{d}/work/enriched.json", "w"))
    write_status(job_id, "running", "enrich", 80, "", {"rows_in": n, **counts})
    mssparkutils.notebook.exit(json.dumps(counts))
except Exception as e:
    write_status(job_id, "failed", "enrich", 0, str(e)[:300])
    raise
