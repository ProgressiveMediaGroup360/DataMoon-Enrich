"""Re-parse an old 'everything.csv' (made before the parser knew Datamoon's real response shape).

Uses the stored dm_raw_json of each row, so it makes NO Datamoon calls. Same logic as the app.
Usage: python3 tools/reparse_everything.py <everything.csv> <out_template.csv> <out_everything.csv>
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.enrich import enrich_record          # noqa: E402
from core.pipeline import build_outputs        # noqa: E402
from core.template import FIELD_TO_HEADER      # noqa: E402

INPUT_FIELDS = list(FIELD_TO_HEADER) + ["asset", "date_delivered", "source_id"]


class StoredIdentity:
    """Stands in for the Datamoon client: answers with the identity saved in the file."""
    def __init__(self, by_email):
        self.by_email = by_email

    def call(self, endpoint, payload):
        ident = self.by_email.get(payload.get("email", ""))
        return {"status": 200, "ok": True, "identities": [ident] if ident else [], "remaining": None,
                "limit": None, "error": ""}


def main(src, out_template, out_everything):
    df = pd.read_csv(src, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    originals = [{f: r.get(f, "") for f in INPUT_FIELDS if f in df.columns} for r in df.to_dict("records")]
    by_email = {}
    for rec, raw in zip(originals, df.get("dm_raw_json", [""] * len(df))):
        if raw:
            by_email[rec["email"].strip().lower()] = json.loads(raw)
    client, now = StoredIdentity(by_email), datetime.utcnow()
    results = [enrich_record(client, rec, now) for rec in originals]
    out = build_outputs(originals, results, qc_fn=None, qc_enabled=False, store_raw_json=False)
    out["frames"]["template"].to_csv(out_template, index=False, encoding="utf-8-sig")
    out["frames"]["everything"].to_csv(out_everything, index=False, encoding="utf-8-sig")
    print(out["counts"], {s: sum(r.status == s for r in results) for s in {r.status for r in results}})


if __name__ == "__main__":
    main(*sys.argv[1:4])
