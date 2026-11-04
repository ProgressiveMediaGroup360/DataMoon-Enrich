"""Company and website from the work-email domain.

Datamoon's email lookup returns no company or website, so these two columns are derived from the
domain after the @: the website is the domain itself, the company comes from core/data/company_domains.csv
(add a line there to teach the app a new domain). Unknown domains leave Company empty - nothing is guessed.
Personal-mail and ISP domains are skipped.
"""
import csv
from pathlib import Path

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com", "live.com",
             "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me", "gmx.com",
             "mail.com", "comcast.net", "att.net", "sbcglobal.net", "verizon.net", "bellsouth.net", "cox.net",
             "cox-internet.com", "telus.net", "shaw.ca", "rogers.com", "sympatico.ca", "rr.com", "cs.com",
             "netscape.com", "earthlink.net", "charter.net", "optonline.net", "mindspring.com", "epix.net"}


def _load() -> dict:
    out = {}
    with open(Path(__file__).parent / "data" / "company_domains.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[r["domain"].strip().lower()] = r["company"].strip()
    return out


COMPANY_BY_DOMAIN = _load()


def domain_of(email) -> str:
    e = str(email or "").strip().lower()
    return e.rsplit("@", 1)[-1] if "@" in e else ""


def is_free_domain(d: str) -> bool:
    return d in FREE_MAIL or d.endswith(".rr.com")


def fill_from_domain(rec: dict):
    """Return (record with empty company/website filled from the email domain, list of fields filled)."""
    d = domain_of(rec.get("email"))
    if not d or is_free_domain(d):
        return rec, []
    out, filled = dict(rec), []
    if not str(out.get("website", "")).strip():
        out["website"] = d
        filled.append("website")
    company = COMPANY_BY_DOMAIN.get(d)
    if company and not str(out.get("company", "")).strip():
        out["company"] = company
        filled.append("company")
    return out, filled
