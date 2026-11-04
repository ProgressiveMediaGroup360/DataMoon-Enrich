"""Map a Datamoon identity onto the internal/template fields.

VERIFIED against real responses (Nov 2026): GetDataByEmail returns a CONSUMER-style identity
{firstName, lastName, address, city, state, zip, emails[], phones[], bestMobile, occupation under data{}, ...}.
It has no job title, company, industry, size, revenue, website or LinkedIn URL, so those template columns
cannot be filled from it. Home address, age, income, credit, devices, vehicles and browsing data are
deliberately NOT copied into the template.

FIELD_CANDIDATES below is the older guess based on the Business Person data dictionary; it is only used
when an identity does not look like the consumer shape.
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


def is_consumer(ident: dict) -> bool:
    return isinstance(ident, dict) and ("firstName" in ident or isinstance(ident.get("emails"), list))


def _name(v) -> str:
    s = str(v or "").strip()
    return s.title() if s and (s.isupper() or s.islower()) else s


def usable_phones(ident: dict) -> list:
    """Phones that are not flagged do-not-call, mobile first, then by Datamoon's own rank."""
    ph = [p for p in (ident.get("phones") or []) if isinstance(p, dict) and p.get("phone") and not p.get("dnc")]
    return sorted(ph, key=lambda p: (p.get("type") != "mobile", p.get("rankOrder") or 99))


def best_phone(ident: dict) -> str:
    ok = usable_phones(ident)
    best = str(ident.get("bestMobile") or "")
    if best and any(p["phone"] == best for p in ok):
        return best
    return str(ok[0]["phone"]) if ok else ""


def extra_columns(ident: dict) -> dict:
    """Parsed, non-sensitive columns for the 'everything' file (empty dict for a non-consumer identity)."""
    if not is_consumer(ident):
        return {}
    d = ident.get("data") or {}
    phones = "; ".join(" ".join(x for x in (str(p.get("phone")), p.get("type") or "", p.get("carrier") or "",
                                            "DNC" if p.get("dnc") else "") if x)
                       for p in (ident.get("phones") or []) if isinstance(p, dict) and p.get("phone"))
    return {
        "dm_first_name": _name(ident.get("firstName")), "dm_last_name": _name(ident.get("lastName")),
        "dm_home_address": ident.get("address") or "", "dm_home_city": ident.get("city") or "",
        "dm_home_state": ident.get("state") or "", "dm_home_zip": ident.get("zip") or "",
        "dm_phone_used": best_phone(ident), "dm_phones": phones,
        "dm_email_count": len(ident.get("emails") or []),
        "dm_occupation": d.get("occupationDetail") or "", "dm_occupation_type": d.get("occupationType") or "",
        "dm_occupation_category": d.get("occupationCategory") or "",
    }


def map_identity(ident: dict) -> dict:
    """Return {internal field: value} for fields the identity actually provides."""
    if is_consumer(ident):
        out = {"first_name": _name(ident.get("firstName")), "last_name": _name(ident.get("lastName")),
               "phone": best_phone(ident)}
        return {k: v for k, v in out.items() if v}
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
