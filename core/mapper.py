"""Map a Datamoon identity onto the internal/template fields.

UNVERIFIED: the candidate keys below come from the Datamoon data dictionary
(Business Person / Company Firmographics), not from a real Enrichment API
response. Check them against a real response (see docs/FIRST_API_RESPONSE.md)
and adjust FIELD_CANDIDATES - nothing else needs to change.

Datamoon's Business Person record has no street address, so address_1 and
address_2 are never overwritten.
"""
from .match import flatten, pick

FIELD_CANDIDATES = {
    "first_name": ["first_name"],
    "last_name": ["last_name"],
    "email": ["work_email"],
    "email_status": ["work_email_status"],
    "job_title": ["job_title"],
    "job_function": ["department"],
    "job_level": ["seniority_level"],
    "company": ["company_name"],
    "city": ["work_city"],
    "state": ["work_state"],
    "zip": ["work_zip"],
    "country": ["work_country"],
    "phone": ["direct_phone", "mobile_phone"],
    "industry": ["company_industry"],
    "employee_size": ["company_employee_range"],
    "revenue_size": ["company_revenue_range"],
    "website": ["company_domain", "website"],
    "linkedin_url": ["linkedin_url"],
}


def map_identity(ident: dict) -> dict:
    """Return {internal field: value} for fields the identity actually provides."""
    flat = flatten(ident)
    out = {}
    for field, names in FIELD_CANDIDATES.items():
        v = pick(flat, names)
        if v:
            out[field] = v
    return out


# The LinkedIn URL is the verification key and the template's "Contact Source Link": we keep the
# one we were given and only fill it in when it was empty.
FILL_ONLY = {"linkedin_url"}


def merge(rec: dict, mapped: dict, allow_overwrite: bool):
    """Overwrite rule: only non-empty Datamoon values, and only for verified matches.

    Returns (merged record, list of changed field names).
    """
    merged, changed = dict(rec), []
    if not allow_overwrite:
        return merged, changed
    for f, v in mapped.items():
        if f in FILL_ONLY and str(rec.get(f, "")).strip():
            continue
        if v and str(rec.get(f, "")).strip() != v:
            merged[f] = v
            changed.append(f)
    return merged, changed
