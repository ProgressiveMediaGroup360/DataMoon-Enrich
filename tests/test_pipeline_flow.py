"""End-to-end run of the notebook logic (01 -> 02 -> 03) without Spark, using synthetic data,
a fake Datamoon client and a fake QC service."""
import py_compile
from datetime import datetime
from pathlib import Path

import pandas as pd

from core.enrich import enrich_record
from core.normalise import normalise_frame
from core.outputs import qc_report_frame, split_by_qc, template_candidates, template_frame
from core.qc import run_qc
from core.template import TEMPLATE_COLUMNS, to_template_row

NOW = datetime(2026, 11, 2)


class FakeDatamoon:
    def call(self, endpoint, payload):
        e = payload.get("email", "")
        if e == "jane@acme.com":
            ident = {"FIRST_NAME": "Jane", "LAST_NAME": "Doe", "WORK_EMAIL": e, "COMPANY_NAME": "Acme",
                     "LINKEDIN_URL": ["https://linkedin.com/in/jane-doe"], "JOB_TITLE": "VP Sales",
                     "SENIORITY_LEVEL": "VP", "WORK_EMAIL_STATUS": "VALID"}
            return {"status": 200, "ok": True, "identities": [ident], "remaining": 90, "limit": 100, "error": ""}
        return {"status": 200, "ok": True, "identities": [], "remaining": 89, "limit": 100, "error": ""}


class FakeQC:
    def post(self, url, json=None, headers=None, timeout=None):
        class R:
            def raise_for_status(self): pass
            def json(_):
                out = []
                for row in json["rows"]:
                    verdict = "FLAG" if row["Email"].startswith("bad") else "PASS"
                    out.append({"overall": verdict, "checks": {"Job Level": {"status": "OK", "comment": ""}}})
                return {"rows": out}
        return R()


def test_full_flow():
    raw = pd.DataFrame([
        {"First Name": "jane", "Last Name": "DOE", "Work Email": "Jane@Acme.com", "Company": "Acme Inc",
         "LinkedIn": "https://www.linkedin.com/in/jane-doe/", "Asset": "SHOULD-BE-IGNORED"},
        {"First Name": "Bad", "Last Name": "Row", "Work Email": "bad@x.com", "Company": "X", "LinkedIn": ""},
        {"First Name": "No", "Last Name": "Email", "Work Email": "", "Company": "Y", "LinkedIn": "https://linkedin.com/in/no-email"},
        {"First Name": "Łukasz", "Last Name": "Żak", "Work Email": "lukasz@x.pl", "Company": "Z", "LinkedIn": ""},
    ])
    df = normalise_frame(raw)
    assert len(df) == 4 and df.loc[3, "first_name"] == "Łukasz"

    client = FakeDatamoon()
    results = [enrich_record(client, r, NOW) for r in df.to_dict(orient="records")]
    assert [r.status for r in results] == ["enriched", "no_match", "no_lookup_key", "no_match"]
    jane = results[0]
    assert jane.confidence == "strong" and jane.merged["job_level"] == "VP" and jane.merged["email_status"] == "VALID"

    candidates, no_email, dups = template_candidates([r.merged for r in results])
    assert len(candidates) == 3 and no_email == 1
    rows = [to_template_row(c) for c in candidates]
    qc = run_qc("http://qc", "", "job1", "VP and above", TEMPLATE_COLUMNS, rows, session=FakeQC())
    ok, rejects, not_run = split_by_qc(candidates, qc)
    assert len(ok) == 2 and len(rejects) == 1 and not not_run

    out = template_frame(ok)
    assert list(out.columns) == TEMPLATE_COLUMNS
    assert (out["Asset "] == "").all() and (out["Date Delivered"] == "").all() and (out["Source ID"] == "").all()
    assert out.iloc[0]["Contact Source Link"] == "https://www.linkedin.com/in/jane-doe/"
    assert "QC Overall" in qc_report_frame(ok + rejects).columns


def test_notebooks_compile():
    for nb in sorted(Path(__file__).parent.parent.glob("fabric/notebooks/*.py")):
        py_compile.compile(str(nb), doraise=True)
