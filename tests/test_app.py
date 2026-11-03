"""Web app + worker, end to end, with a fake Datamoon client and a fake QC service."""
import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main, worker
from app.config import settings
from core.template import TEMPLATE_COLUMNS

TOKEN = "t" * 32


class InlinePool:
    """Runs submitted jobs immediately so tests are deterministic."""
    def submit(self, fn, *a, **k):
        fn(*a, **k)


class FakeDatamoon:
    calls = []

    def __init__(self, api_key, cooldown_sec=0):
        assert api_key == "DM-KEY"

    def call(self, endpoint, payload):
        FakeDatamoon.calls.append((endpoint, payload))
        if payload.get("email") == "jane@acme.com":
            ident = {"FIRST_NAME": "Jane", "LAST_NAME": "Doe", "WORK_EMAIL": "jane@acme.com", "COMPANY_NAME": "Acme",
                     "LINKEDIN_URL": ["https://linkedin.com/in/jane-doe"], "JOB_TITLE": "VP Sales",
                     "SENIORITY_LEVEL": "VP", "WORK_EMAIL_STATUS": "VALID"}
            return {"status": 200, "ok": True, "identities": [ident], "remaining": 90, "limit": 100, "error": ""}
        return {"status": 200, "ok": True, "identities": [], "remaining": 89, "limit": 100, "error": ""}


def fake_qc(url, token, job_id, spec_text, columns, rows):
    assert columns == TEMPLATE_COLUMNS and "Geography: USA" in spec_text
    return [{"overall": "FLAG" if r["Email"].startswith("bad") else "PASS",
             "checks": {"Geography": {"status": "OK", "comment": ""}}} for r in rows]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    settings.access_token = TOKEN
    settings.datamoon_api_key = "DM-KEY"
    settings.qc_url = "http://qc.example/webhook"
    settings.data_dir = str(tmp_path)
    settings.max_rows = 5
    settings.max_jobs_per_hour = 100
    settings.max_jobs_per_ip_hour = 100
    main._state.clear(); main._hits_global.clear(); main._hits_ip.clear()
    FakeDatamoon.calls = []
    monkeypatch.setattr(worker, "DatamoonClient", FakeDatamoon)
    monkeypatch.setattr(worker, "run_qc", fake_qc)
    st = main.state()
    st["pool"] = InlinePool()
    return TestClient(main.app)


def url(p=""):
    return f"/s/{TOKEN}/{p}"


CSV = (b"First Name,Last Name,Work Email,Company,LinkedIn,Asset\n"
       b"jane,DOE,Jane@Acme.com,Acme Inc,https://www.linkedin.com/in/jane-doe/,SHOULD-BE-IGNORED\n"
       b"Bad,Row,bad@x.com,X,\n"
       b"No,Email,,Y,https://linkedin.com/in/no-email\n"
       b"\xc5\x81ukasz,\xc5\xbbak,lukasz@x.pl,Z,\n")
SPEC = {"geography": "USA", "job_title": "VP"}


def post(client, data=CSV, name="list.csv", **form):
    return client.post(url("api/jobs"), files={"file": (name, data)}, data={**SPEC, **form})


def read_csv(client, job, name):
    r = client.get(url(f"api/jobs/{job}/files/{name}"))
    assert r.status_code == 200
    return pd.read_csv(io.BytesIO(r.content), dtype=str, keep_default_na=False)


def test_wrong_or_missing_token_is_404(client):
    assert client.get("/").status_code == 404
    assert client.get("/s/wrong/").status_code == 404
    assert client.post("/s/wrong/api/jobs", files={"file": ("a.csv", CSV)}).status_code == 404
    page = client.get(url())
    assert page.status_code == 200 and "noindex" in page.headers["x-robots-tag"]


def test_full_job_produces_checked_template_and_everything(client):
    r = post(client, client_name="ACME")
    assert r.status_code == 200
    job = r.json()["job_id"]
    s = client.get(url(f"api/jobs/{job}")).json()
    assert s["status"] == "complete" and s["pct"] == 100
    c = s["counts"]
    assert (c["rows_in"], c["enriched"], c["no_match"], c["no_lookup_key"]) == (4, 1, 3, 0)   # the no-email row is looked up by LinkedIn
    assert c["template_rows"] == 2 and c["qc_flag"] == 1 and c["dropped_no_email"] == 1
    assert set(s["files"]) == {"template.csv", "rejects.csv", "qc_report.csv", "everything.csv"}

    t = read_csv(client, job, "template.csv")
    assert list(t.columns) == TEMPLATE_COLUMNS
    assert (t["Asset "] == "").all() and (t["Date Delivered"] == "").all() and (t["Source ID"] == "").all()
    jane = t[t["Email"] == "jane@acme.com"].iloc[0]
    assert jane["Job Level"] == "VP" and jane["Email Status"] == "VALID"
    assert jane["Contact Source Link"] == "https://www.linkedin.com/in/jane-doe/"
    assert "Łukasz" in set(t["First Name"])
    assert len(read_csv(client, job, "rejects.csv")) == 1

    e = read_csv(client, job, "everything.csv")
    assert len(e) == 4 and set(e["template_status"]) >= {"in template", "rejected by QC", "no email - excluded"}


def test_second_upload_within_3_months_reuses_cache_without_api_calls(client):
    post(client)
    first_calls = len(FakeDatamoon.calls)
    assert first_calls >= 3
    job2 = post(client).json()["job_id"]
    s = client.get(url(f"api/jobs/{job2}")).json()
    assert len(FakeDatamoon.calls) == first_calls          # nothing sent to Datamoon again
    assert s["counts"]["skipped_recent"] == 4
    t = read_csv(client, job2, "template.csv")
    assert t[t["Email"] == "jane@acme.com"].iloc[0]["Job Level"] == "VP"   # served from the stored enrichment


def test_qc_service_down_still_delivers_unchecked_file(client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(worker, "run_qc", boom)
    job = post(client).json()["job_id"]
    s = client.get(url(f"api/jobs/{job}")).json()
    assert s["status"] == "complete" and "quality check" in s["message"].lower()
    assert s["counts"]["template_rows"] == 0 and s["counts"]["qc_not_run"] == 3
    assert "template_unchecked.csv" in s["files"]
    assert len(read_csv(client, job, "template_unchecked.csv")) == 3


def test_no_qc_url_means_not_run_never_pass(client):
    settings.qc_url = ""
    monkeypatch_run = worker.run_qc
    worker.run_qc = __import__("core.qc", fromlist=["run_qc"]).run_qc   # real client: no URL -> NOT_RUN
    try:
        job = post(client).json()["job_id"]
    finally:
        worker.run_qc = monkeypatch_run
    s = client.get(url(f"api/jobs/{job}")).json()
    assert s["counts"]["qc_pass"] == 0 and s["counts"]["qc_not_run"] == 3 and "template_unchecked.csv" in s["files"]


def test_validation_errors(client):
    assert post(client, data=b"Email\n" + b"\n".join(b"a%d@b.com" % i for i in range(10))).status_code == 400  # row cap
    assert post(client, data=b"MZ", name="x.exe").status_code == 400
    assert client.post(url("api/jobs"), files={"file": ("a.csv", CSV)}, data={}).status_code == 400            # no specs
    assert client.post(url("api/jobs"), files={"file": ("a.csv", b"Email\n")}, data=SPEC).status_code == 400   # no rows
    assert FakeDatamoon.calls == []


def test_unconfigured_service_refuses(client):
    settings.datamoon_api_key = ""
    assert post(client).status_code == 503


def test_bad_key_stops_job_with_friendly_message(client, monkeypatch):
    class Rejecting(FakeDatamoon):
        def call(self, endpoint, payload):
            return {"status": 401, "ok": False, "identities": [], "remaining": None, "limit": None, "error": "bad key"}
    monkeypatch.setattr(worker, "DatamoonClient", Rejecting)
    csv = b"Email\n" + b"\n".join(b"a%d@b.com" % i for i in range(5))
    job = post(client, data=csv).json()["job_id"]
    s = client.get(url(f"api/jobs/{job}")).json()
    assert s["status"] == "failed" and "API key" in s["message"]


def test_job_ids_and_file_names_are_validated(client):
    assert client.get(url("api/jobs/not-a-uuid")).status_code == 404
    assert client.get(url("api/jobs/11111111-1111-4111-8111-111111111111")).status_code == 404
    job = post(client).json()["job_id"]
    assert client.get(url(f"api/jobs/{job}/files/..%2Fmeta.json")).status_code == 404
    assert client.get(url(f"api/jobs/{job}/files/status.json")).status_code == 404


def test_per_ip_rate_limit(client):
    settings.max_jobs_per_ip_hour = 1
    assert post(client).status_code == 200
    assert post(client).status_code == 429


def test_compose_spec():
    s = main.compose_spec({"geography": "USA", "job_title": "CEO,\nCFO", "other_criteria": "Seniority: Director+\n\n"})
    assert s == "Geography: USA\nJob Title: CEO,; CFO\nSeniority: Director+"
    assert main.compose_spec({}) == ""


def test_interrupted_jobs_are_marked_failed_on_start(tmp_path):
    from app.store import JobStore
    st = JobStore(tmp_path)
    worker._status(st, "j1", "running", "enrich", 40)
    main._state.clear()
    settings.data_dir = str(tmp_path)
    main.state()
    assert json.loads(st.read_bytes("j1", "status.json"))["status"] == "failed"


def test_quality_check_switched_off_only_enriches(client, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("QC must not be called")
    monkeypatch.setattr(worker, "run_qc", must_not_run)
    r = client.post(url("api/jobs"), files={"file": ("list.csv", CSV)}, data={"run_qc": "0"})   # no specs needed
    assert r.status_code == 200
    s = client.get(url(f"api/jobs/{r.json()['job_id']}")).json()
    assert s["status"] == "complete" and "switched off" in s["message"]
    assert set(s["files"]) == {"template.csv", "everything.csv"}
    assert s["counts"]["template_rows"] == 3 and s["counts"]["qc_not_run"] == 0
    t = read_csv(client, r.json()["job_id"], "template.csv")
    assert set(t["Email"]) == {"jane@acme.com", "bad@x.com", "lukasz@x.pl"}


def test_quality_check_on_still_requires_specs(client):
    r = client.post(url("api/jobs"), files={"file": ("a.csv", CSV)}, data={"run_qc": "1"})
    assert r.status_code == 400


def test_any_csv_encoding_is_accepted(client):
    raw = "First Name,Last Name,Work Email,Company\nJosé,García,jane@acme.com,Acme\n"
    for enc in ("cp1252", "utf-16", "utf-8"):
        r = client.post(url("api/jobs"), files={"file": ("l.csv", raw.encode(enc))}, data={"run_qc": "0"})
        assert r.status_code == 200, enc
        t = read_csv(client, r.json()["job_id"], "template.csv")
        assert "García" in set(t[" Last Name "]), enc


def test_file_with_nothing_to_look_up_explains_why(client):
    csv = b"Name,Company\nJane Doe,Acme\nJohn Roe,X\n"
    r = client.post(url("api/jobs"), files={"file": ("l.csv", csv)}, data={"run_qc": "0"})
    s = client.get(url(f"api/jobs/{r.json()['job_id']}")).json()
    assert s["status"] == "complete" and s["counts"]["no_lookup_key"] == 2
    assert "email, phone or LinkedIn" in s["message"] and FakeDatamoon.calls == []


LI_CSV = b"LinkedIn\nhttps://www.linkedin.com/in/jane-doe/\nhttps://www.linkedin.com/in/nobody/\n"


def test_linkedin_only_file_is_looked_up_by_url(client, monkeypatch):
    class LI(FakeDatamoon):
        def call(self, endpoint, payload):
            FakeDatamoon.calls.append((endpoint, payload))
            if "linkedin" not in payload:
                return {"status": 422, "ok": False, "identities": [], "remaining": 9, "limit": 10, "error": "param"}
            if payload["linkedin"].endswith("jane-doe/"):
                ident = {"FIRST_NAME": "Jane", "LAST_NAME": "Doe", "WORK_EMAIL": "jane@acme.com", "COMPANY_NAME": "Acme",
                         "LINKEDIN_URL": ["https://linkedin.com/in/jane-doe"], "JOB_TITLE": "VP Sales"}
                return {"status": 200, "ok": True, "identities": [ident], "remaining": 9, "limit": 10, "error": ""}
            return {"status": 200, "ok": True, "identities": [], "remaining": 9, "limit": 10, "error": ""}
    monkeypatch.setattr(worker, "DatamoonClient", LI)
    r = client.post(url("api/jobs"), files={"file": ("l.csv", LI_CSV)}, data={"run_qc": "0"})
    s = client.get(url(f"api/jobs/{r.json()['job_id']}")).json()
    assert s["counts"]["enriched"] == 1 and s["counts"]["no_match"] == 1
    t = read_csv(client, r.json()["job_id"], "template.csv")
    assert list(t["Email"]) == ["jane@acme.com"]          # filled from Datamoon, row with no email is excluded
    # 'linkedin_url' was rejected once (422); the working name 'linkedin' was then reused for both rows
    assert [list(p) for _, p in FakeDatamoon.calls].count(["linkedin"]) == 2


def test_missing_linkedin_endpoint_switches_lookup_off_for_the_job(client, monkeypatch):
    class Gone(FakeDatamoon):
        def call(self, endpoint, payload):
            FakeDatamoon.calls.append((endpoint, payload))
            return {"status": 404, "ok": False, "identities": [], "remaining": None, "limit": None, "error": "Not found"}
    monkeypatch.setattr(worker, "DatamoonClient", Gone)
    r = client.post(url("api/jobs"), files={"file": ("l.csv", LI_CSV)}, data={"run_qc": "0"})
    s = client.get(url(f"api/jobs/{r.json()['job_id']}")).json()
    assert len(FakeDatamoon.calls) == 1                  # asked once, then stopped
    assert s["status"] == "complete" and "no LinkedIn lookup" in s["message"] and s["counts"]["errors"] == 2
