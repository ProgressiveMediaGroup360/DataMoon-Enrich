"""Tests use synthetic data only. Datamoon identity shapes here are ASSUMED from the data
dictionary, not taken from a real API response."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from core import match, qc
from core.datamoon import DatamoonClient
from core.enrich import enrich_record, is_recent
from core.mapper import map_identity, merge
from core.normalise import (contact_key, dedupe, map_columns, normalise_frame, normalise_linkedin,
                            normalise_name, normalise_phone)
from core.outputs import split_by_qc, template_candidates, template_frame
from core.spec_query import SpecError, build_query, validate_spec
from core.template import BLANK_BY_DESIGN, TEMPLATE_COLUMNS

NOW = datetime(2026, 11, 2, 12, 0, 0)


# ---- template ----------------------------------------------------------------
def test_template_headers_match_file_exactly():
    header = ("First Name, Last Name ,Email,Email Status,Job Title,Job Function,Job Level,Company,"
              "Address 1,Address 2,City,State,Zip,Country,Phone,Industry,Employee Size,Revenue Size,"
              "Website,Asset ,Contact Source Link,Date Delivered,Source ID")
    assert ",".join(TEMPLATE_COLUMNS) == header


def test_blank_by_design_never_filled_even_if_input_has_values():
    df = normalise_frame(pd.DataFrame([{"Email": "a@b.com", "Asset": "X", "Date Delivered": "1/1", "Source ID": "9"}]))
    row = template_frame([(df.iloc[0].to_dict(), None)]).iloc[0]
    for h in BLANK_BY_DESIGN:
        assert row[h] == ""


def test_contact_source_link_is_linkedin():
    rec = {"email": "a@b.com", "linkedin_url": "https://www.linkedin.com/in/jane/"}
    row = template_frame([(rec, None)]).iloc[0]
    assert row["Contact Source Link"] == "https://www.linkedin.com/in/jane/"


# ---- normalisation (bugs in the old notebook) -----------------------------------
def test_accented_names_survive():
    assert normalise_name("ŁUKASZ") == "Łukasz"
    assert normalise_name("josé garcía") == "José García"
    assert normalise_name("McDonald") == "McDonald"


def test_blank_emails_are_not_duplicates():
    df = pd.DataFrame([{"email": "", "linkedin_url": "https://linkedin.com/in/a"},
                       {"email": "", "linkedin_url": "https://linkedin.com/in/b"},
                       {"email": "", "linkedin_url": ""}, {"email": "", "linkedin_url": ""}])
    assert len(dedupe(df)) == 4


def test_real_duplicates_removed_by_linkedin_then_email():
    df = pd.DataFrame([{"email": "a@b.com", "linkedin_url": "https://www.linkedin.com/in/Jane/"},
                       {"email": "x@b.com", "linkedin_url": "http://linkedin.com/in/jane?trk=1"},
                       {"email": "a@b.com", "linkedin_url": ""}])
    assert len(dedupe(df)) == 1


def test_shared_phone_is_not_a_duplicate():
    df = normalise_frame(pd.DataFrame([{"Email": "a@x.com", "Phone": "555-123-4567"},
                                       {"Email": "b@x.com", "Phone": "555-123-4567"}]))
    assert len(df) == 2


def test_phone_formats():
    assert normalise_phone("(555) 123-4567") == "+1-555-123-4567"
    assert normalise_phone("+48 600 100 200") == "+48600100200"


def test_column_mapping():
    m = map_columns(["First Name", " Last Name ", "Work Email", "LinkedIn Profile URL", "Job Title",
                     "Company Name", "Company LinkedIn", "Asset ", "Random"])
    assert m["First Name"] == "first_name" and m[" Last Name "] == "last_name"
    assert m["LinkedIn Profile URL"] == "linkedin_url"
    assert m["Company Name"] == "company" and "Random" not in m
    assert m["Asset "] == "asset"
    assert "Company LinkedIn" not in m  # company page, not the person's profile


def test_normalise_linkedin():
    assert normalise_linkedin("HTTPS://PL.LinkedIn.com/in/Jan-Kowalski/?x=1") == "linkedin.com/in/jan-kowalski"
    assert normalise_linkedin("https://example.com/in/x") == ""


# ---- verification + merge ------------------------------------------------------
REC = {"first_name": "Jane", "last_name": "Doe", "email": "jane@acme.com", "company": "Acme Inc",
       "linkedin_url": "https://www.linkedin.com/in/jane-doe", "job_title": "Analyst", "city": "Austin"}
IDENT = {"FIRST_NAME": "Jane", "LAST_NAME": "Doe", "WORK_EMAIL": "jane@acme.com", "COMPANY_NAME": "Acme",
         "LINKEDIN_URL": ["https://linkedin.com/in/jane-doe/"], "JOB_TITLE": "VP Sales",
         "SENIORITY_LEVEL": "VP", "WORK_EMAIL_STATUS": "VALID", "WORK_CITY": "", "DIRECT_PHONE": "+15551230000"}


def test_strong_match_by_linkedin():
    assert match.verify_match(REC, IDENT)[0] == match.STRONG


def test_linkedin_conflict_blocks_overwrite():
    other = dict(IDENT, LINKEDIN_URL="https://linkedin.com/in/someone-else")
    conf, _ = match.verify_match(REC, other)
    assert conf == match.CONFLICT
    merged, changed = merge(REC, map_identity(other), conf in match.OVERWRITE_ALLOWED)
    assert merged == REC and changed == []


def test_name_conflict_without_linkedin():
    rec = dict(REC, linkedin_url="")
    conf, _ = match.verify_match(rec, dict(IDENT, LINKEDIN_URL="", FIRST_NAME="Bob"))
    assert conf == match.CONFLICT


def test_overwrite_only_non_empty_values():
    merged, changed = merge(REC, map_identity(IDENT), True)
    assert merged["job_title"] == "VP Sales" and merged["job_level"] == "VP"
    assert merged["city"] == "Austin"  # Datamoon returned blank -> keep ours
    assert merged["email_status"] == "VALID"
    assert "city" not in changed
    assert "address_1" not in changed and "address_2" not in changed  # Datamoon has no street address


def test_linkedin_url_is_never_overwritten_only_filled():
    merged, changed = merge(REC, map_identity(IDENT), True)
    assert merged["linkedin_url"] == REC["linkedin_url"] and "linkedin_url" not in changed
    filled, ch = merge(dict(REC, linkedin_url=""), map_identity(IDENT), True)
    assert filled["linkedin_url"] and "linkedin_url" in ch


# ---- enrichment orchestration ----------------------------------------------------
class FakeClient:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def call(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        return self.results.pop(0)


def _ok(identities):
    return {"status": 200, "ok": True, "identities": identities, "remaining": 50, "limit": 100, "error": ""}


def test_three_month_rule_skips_api_and_uses_cache():
    client = FakeClient([])
    cached = {"confidence": "strong", "mapped": {"job_title": "Cached VP"}}
    res = enrich_record(client, REC, NOW, last_enriched_at=NOW - timedelta(days=30), cached=cached)
    assert res.status == "skipped_recent" and client.calls == []
    assert res.merged["job_title"] == "Cached VP"


def test_old_enrichment_is_refreshed():
    assert not is_recent(NOW - timedelta(days=91), NOW)
    client = FakeClient([_ok([IDENT])])
    res = enrich_record(client, REC, NOW, last_enriched_at=NOW - timedelta(days=120), cached={"mapped": {}})
    assert res.status == "enriched" and client.calls[0][0] == "GetDataByEmail"


def test_no_match_is_not_an_error_and_falls_back_to_phone():
    rec = dict(REC, phone="+1-555-123-4567")
    client = FakeClient([_ok([]), _ok([])])
    res = enrich_record(client, rec, NOW)
    assert res.status == "no_match"
    assert [c[0] for c in client.calls] == ["GetDataByEmail", "GetDataByPhone"]


def test_auth_failure_stops_immediately():
    client = FakeClient([{"status": 401, "ok": False, "identities": [], "remaining": None, "limit": None, "error": "bad key"}])
    res = enrich_record(client, dict(REC, phone="555-123-4567"), NOW)
    assert res.status == "error" and len(client.calls) == 1


def test_no_lookup_key():
    res = enrich_record(FakeClient([]), {"first_name": "A"}, NOW)
    assert res.status == "no_lookup_key"


# ---- Datamoon client ---------------------------------------------------------------
class R:
    def __init__(self, status, body, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, responses):
        self.responses, self.sent = list(responses), []

    def post(self, url, json=None, timeout=None):
        self.sent.append(json)
        return self.responses.pop(0)


def test_client_empty_identities_is_success_and_key_in_body():
    s = FakeSession([R(200, {"success": True, "customId": "t", "data": {"identities": []}},
                       {"x-ratelimit-remaining": "99", "x-ratelimit-limit": "100"})])
    out = DatamoonClient("KEY", session=s, sleep=lambda x: None).call("GetDataByEmail", {"email": "a@b.com"})
    assert out["ok"] and out["identities"] == [] and out["remaining"] == 99
    assert s.sent[0]["api_key"] == "KEY"


def test_client_retries_429_then_succeeds_and_pauses_near_quota():
    sleeps = []
    s = FakeSession([R(429, {}, {"retry-after": "1"}),
                     R(200, {"success": True, "data": {"identities": [{"a": 1}]}}, {"x-ratelimit-remaining": "1"})])
    out = DatamoonClient("K", session=s, sleep=sleeps.append, cooldown_sec=7).call("GetDataByEmail", {})
    assert out["ok"] and len(out["identities"]) == 1
    assert sleeps.count(7) >= 2  # after the 429 and again because remaining <= 1


def test_client_gives_up_after_retries():
    s = FakeSession([R(500, {})] * 3)
    out = DatamoonClient("K", session=s, max_retries=2, sleep=lambda x: None).call("X", {})
    assert not out["ok"] and "gave up" in out["error"]


def test_client_requires_key():
    with pytest.raises(ValueError):
        DatamoonClient("")


# ---- spec -> query ---------------------------------------------------------------------
def test_query_uses_allowlisted_columns_and_escapes():
    q = build_query({"filters": {"job_level": ["VP", "Director"], "industry": ["Real Estate"]}, "row_limit": 50}, 100)
    assert "dj.job_title_levels IN ('VP', 'Director')" in q and "TOP (50)" in q
    assert "DISTINCT" not in q and "dc.Company_Address" in q and "JOIN [dbo].[DimEmail]" in q


@pytest.mark.parametrize("bad", ["x'; DROP TABLE DimPrimary;--", "a /* b */", "x\ny;", "100%", "a" * 101])
def test_injection_attempts_rejected(bad):
    with pytest.raises(SpecError):
        validate_spec({"filters": {"title_keywords": [bad]}}, 100)


def test_apostrophe_is_escaped_not_rejected():
    q = build_query({"filters": {"industry": ["Women's Apparel"]}}, 100)
    assert "'Women''s Apparel'" in q


def test_unknown_filter_and_empty_spec_rejected():
    with pytest.raises(SpecError):
        validate_spec({"filters": {"password": ["x"]}}, 100)
    with pytest.raises(SpecError):
        validate_spec({"filters": {}}, 100)


def test_row_limit_clamped_to_cap():
    assert validate_spec({"filters": {"state": ["TX"]}, "row_limit": 99999}, 100)["row_limit"] == 100


# ---- outputs + QC ------------------------------------------------------------------------
def test_template_candidates_drop_no_email_and_pick_most_complete():
    recs = [{"email": "", "first_name": "NoEmail"},
            {"email": "a@x.com", "first_name": "A", "linkedin_url": "https://linkedin.com/in/a"},
            {"email": "a@x.com", "first_name": "A", "linkedin_url": "https://linkedin.com/in/a", "phone": "+1-555-111-2222"}]
    kept, no_email, dups = template_candidates(recs)
    assert len(kept) == 1 and no_email == 1 and dups == 1 and kept[0]["phone"]


def test_qc_split_and_not_run_never_passes():
    recs = [{"email": str(i)} for i in range(4)]
    q = [{"overall": v, "checks": {}} for v in ("PASS", "HUMAN REVIEW", "FLAG", "NOT_RUN")]
    ok, rej, nr = split_by_qc(recs, q)
    assert len(ok) == 2 and len(rej) == 1 and len(nr) == 1


def test_qc_not_run_without_url_or_spec():
    assert qc.run_qc("", "", "j", "spec", [], [{"a": 1}])[0]["overall"] == "NOT_RUN"
    assert qc.run_qc("http://x", "", "j", "  ", [], [{"a": 1}])[0]["overall"] == "NOT_RUN"


def test_qc_rejects_mismatched_response():
    class S:
        def post(self, *a, **k):
            class Resp:
                def raise_for_status(self): pass
                def json(self): return {"rows": []}
            return Resp()
    with pytest.raises(ValueError):
        qc.run_qc("http://x", "", "j", "spec", [], [{"a": 1}], session=S())


def test_consumer_shape_response_is_parsed_and_verified():
    from core.mapper import extra_columns, map_identity
    from core.match import MEDIUM, WEAK, verify_match
    ident = {"firstName": "LEE", "lastName": "Rubin", "address": "1 Home St", "city": "X", "state": "MD", "zip": "1",
             "emails": [{"email": "lrubin@meridianbanker.com"}, {"email": "other@aol.com"}],
             "phones": [{"phone": "111", "type": "mobile", "rankOrder": 1, "dnc": True},
                        {"phone": "222", "type": "landline", "rankOrder": 1, "dnc": None},
                        {"phone": "333", "type": "mobile", "rankOrder": 2, "dnc": None}],
             "bestMobile": "111", "data": {"occupationDetail": "Manager", "creditRange": "B. 750-799", "age": 70}}
    conf, _ = verify_match({"email": "LRubin@meridianbanker.com"}, ident)
    assert conf == MEDIUM
    m = map_identity(ident)
    assert m == {"first_name": "Lee", "last_name": "Rubin", "phone": "333"}   # do-not-call number never used
    x = extra_columns(ident)
    assert x["dm_occupation"] == "Manager" and "DNC" in x["dm_phones"] and x["dm_home_zip"] == "1"
    assert not any("credit" in k or "age" in k or "income" in k for k in x)    # modelled personal data stays out
    # same email, but the identity is a different person than the email suggests -> not trusted
    other = dict(ident, firstName="Ryan", lastName="Moore")
    conf2, why = verify_match({"email": "paula.gillespie@bkfs.com"}, dict(other, emails=[{"email": "paula.gillespie@bkfs.com"}]))
    assert conf2 == WEAK and "resemble" in why[0]


def test_invalid_email_is_not_sent_and_not_delivered():
    from datetime import datetime
    from core.enrich import enrich_record

    class Boom:
        def call(self, *a, **k):
            raise AssertionError("must not be called")
    r = enrich_record(Boom(), {"email": "natalie.von döhren@lehman.com"}, datetime(2026, 1, 1))
    assert r.status == "no_lookup_key"
