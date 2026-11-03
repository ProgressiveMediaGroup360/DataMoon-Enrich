"""Input normalisation (fixed version of the old notebook 01).

Fixes versus audiencelab-platform/notebooks/01_normalise_crm.py:
  * names keep accented / non-ASCII letters
  * duplicate removal ignores blank keys (the old code deleted every row after
    the first with a blank email or phone)
  * no de-duplication on phone (shared office numbers are not duplicates)
  * phone numbers outside the US are kept, not mangled
  * column mapping uses exact synonyms before fuzzy rules, LinkedIn first
"""
import re
import unicodedata

import pandas as pd

from .template import CONTENT_FIELDS


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return str(v).strip() == ""


def normalise_name(v) -> str:
    if _blank(v):
        return ""
    s = unicodedata.normalize("NFC", str(v)).strip()
    s = "".join(ch for ch in s if ch.isalpha() or ch in " -'’.")
    s = re.sub(r"\s+", " ", s).strip(" .")
    if s.isupper() or s.islower():
        s = s.title()
    return s


def normalise_email(v) -> str:
    return "" if _blank(v) else str(v).strip().lower()


def is_valid_email(v) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(v or "")))


def normalise_phone(v) -> str:
    if _blank(v):
        return ""
    raw = str(v).strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and not digits.startswith("1"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1-{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return raw


def normalise_linkedin(v) -> str:
    """Canonical form used for comparison: host+path, lower case, no query/trailing slash."""
    if _blank(v):
        return ""
    s = str(v).strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^(www|[a-z]{2})\.", "", s)
    s = s.split("?")[0].split("#")[0].rstrip("/")
    return s if s.startswith("linkedin.com/") else ""


def normalise_text(v) -> str:
    return "" if _blank(v) else re.sub(r"\s+", " ", str(v)).strip()


def contact_key(rec) -> str:
    """Stable key for caching/dedupe: LinkedIn first, then email, then phone digits."""
    li = normalise_linkedin(rec.get("linkedin_url"))
    if li:
        return "li:" + li
    em = normalise_email(rec.get("email"))
    if em:
        return "em:" + em
    ph = re.sub(r"\D", "", str(rec.get("phone") or ""))
    return ("ph:" + ph) if ph else ""


# ---- column mapping -------------------------------------------------------

SYNONYMS = {
    "linkedin_url": {"linkedin", "linkedin url", "linkedin profile", "linkedin profile url",
                     "person linkedin url", "contact source link"},
    "first_name": {"first name", "firstname", "first", "given name"},
    "last_name": {"last name", "lastname", "last", "surname", "family name"},
    "email": {"email", "e mail", "email address", "work email", "business email"},
    "email_status": {"email status", "email validation status"},
    "job_title": {"job title", "title", "position", "role"},
    "job_function": {"job function", "function", "department"},
    "job_level": {"job level", "seniority", "seniority level", "level"},
    "company": {"company", "company name", "organization", "organisation", "employer", "account", "account name"},
    "address_1": {"address 1", "address1", "address", "street", "street address"},
    "address_2": {"address 2", "address2", "suite", "unit"},
    "city": {"city", "town"},
    "state": {"state", "province", "region", "state province"},
    "zip": {"zip", "zip code", "zipcode", "postal code", "postcode"},
    "country": {"country", "country code"},
    "phone": {"phone", "phone number", "mobile", "mobile phone", "direct phone", "telephone"},
    "industry": {"industry", "sector"},
    "employee_size": {"employee size", "employees", "employee count", "company size", "headcount"},
    "revenue_size": {"revenue size", "revenue", "annual revenue", "company revenue"},
    "website": {"website", "web site", "domain", "company website", "company domain"},
    # blank-by-design in the output; recognised so they are not mistaken for something else
    "asset": {"asset"},
    "date_delivered": {"date delivered"},
    "source_id": {"source id"},
}

_FUZZY = [
    ("linkedin_url", lambda h: "linkedin" in h and "company" not in h),
    ("email", lambda h: "email" in h or "e mail" in h),
    ("phone", lambda h: "phone" in h or "mobile" in h or "tel" == h),
    ("first_name", lambda h: "first" in h and "name" in h),
    ("last_name", lambda h: "last" in h and "name" in h),
    ("zip", lambda h: "zip" in h or "postal" in h),
    ("job_title", lambda h: "title" in h),
    ("company", lambda h: "company" in h or "organi" in h),
    ("industry", lambda h: "industry" in h),
    ("employee_size", lambda h: "employee" in h),
    ("revenue_size", lambda h: "revenue" in h),
    ("website", lambda h: "website" in h or "domain" in h),
    ("country", lambda h: "country" in h),
    ("state", lambda h: "state" in h),
    ("city", lambda h: "city" in h),
]


def _header_key(c) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(c).lower()).strip()


def map_columns(columns) -> dict:
    """Return {original column: internal field}. First column wins per field."""
    taken, out = set(), {}
    exact = {s: f for f, syns in SYNONYMS.items() for s in syns}
    for c in columns:
        h = _header_key(c)
        f = exact.get(h)
        if f and f not in taken:
            out[c] = f
            taken.add(f)
    for c in columns:
        if c in out:
            continue
        h = _header_key(c)
        for f, pred in _FUZZY:
            if f not in taken and pred(h):
                out[c] = f
                taken.add(f)
                break
    return out


def normalise_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Rename recognised columns, normalise values, drop duplicates.

    Unrecognised columns are kept unchanged (they go to the "everything" output).
    Every internal content field exists afterwards (empty string if absent).
    """
    df = df.copy().fillna("").astype(str)
    df = df.rename(columns=map_columns(df.columns))
    for f in CONTENT_FIELDS + ["asset", "date_delivered", "source_id"]:
        if f not in df.columns:
            df[f] = ""

    for f in ("first_name", "last_name"):
        df[f] = df[f].map(normalise_name)
    df["email"] = df["email"].map(normalise_email)
    df["phone"] = df["phone"].map(normalise_phone)
    df["linkedin_url"] = df["linkedin_url"].map(lambda v: normalise_text(v))
    for f in ("job_title", "job_function", "job_level", "company", "address_1", "address_2",
              "city", "state", "zip", "country", "industry", "employee_size",
              "revenue_size", "website", "email_status"):
        df[f] = df[f].map(normalise_text)

    return dedupe(df)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Drop later duplicates by LinkedIn URL, then by email. Blank keys are never duplicates."""
    li = df["linkedin_url"].map(normalise_linkedin)
    df = df[~(li.duplicated(keep="first") & (li != ""))]
    em = df["email"]
    df = df[~(em.duplicated(keep="first") & (em != ""))]
    return df.reset_index(drop=True)
