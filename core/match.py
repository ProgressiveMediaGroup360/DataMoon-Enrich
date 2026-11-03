"""Verify that a Datamoon identity is the same person as the input row.

Datamoon has no LinkedIn-URL lookup, so LinkedIn is used here as a *verification*
key: look up by email/phone, then check the returned identity against the row.
"""
import re
import unicodedata

from .normalise import normalise_email, normalise_linkedin

# confidence levels
STRONG, MEDIUM, WEAK, CONFLICT = "strong", "medium", "weak", "conflict"
OVERWRITE_ALLOWED = {STRONG, MEDIUM}

_COMPANY_NOISE = {"inc", "llc", "ltd", "corp", "co", "gmbh", "sa", "plc", "company", "corporation", "limited", "zoo", "sp"}


def flatten(obj, _out=None, _prefix="") -> dict:
    """Flatten nested dicts into {lower_case_key: value}. Leaf names are also registered
    (first one wins) so lookups work whether the API nests fields or not."""
    out = {} if _out is None else _out
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            path = f"{_prefix}.{lk}" if _prefix else lk
            if isinstance(v, dict):
                flatten(v, out, path)
            else:
                out.setdefault(path, v)
                out.setdefault(lk, v)
    return out


def first_scalar(v):
    """Return a single string from a scalar or list value ('' if empty)."""
    if isinstance(v, (list, tuple)):
        for x in v:
            s = first_scalar(x)
            if s:
                return s
        return ""
    if v is None:
        return ""
    return str(v).strip()


def pick(flat: dict, names) -> str:
    for n in names:
        s = first_scalar(flat.get(n.lower()))
        if s:
            return s
    return ""


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _company_core(s: str) -> str:
    return " ".join(w for w in _fold(s).split() if w not in _COMPANY_NOISE)


def company_similar(a: str, b: str) -> bool:
    a, b = _company_core(a), _company_core(b)
    if not a or not b:
        return False
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))


def verify_match(rec: dict, ident: dict):
    """Return (confidence, reasons) for an identity vs. an input record."""
    flat = flatten(ident)
    reasons = []

    li_rec = normalise_linkedin(rec.get("linkedin_url"))
    li_id = normalise_linkedin(pick(flat, ["linkedin_url", "linkedin"]))
    if li_rec and li_id:
        if li_rec == li_id:
            return STRONG, ["linkedin_url matches"]
        return CONFLICT, ["linkedin_url differs"]

    fn_r, ln_r = _fold(rec.get("first_name")), _fold(rec.get("last_name"))
    fn_i, ln_i = _fold(pick(flat, ["first_name"])), _fold(pick(flat, ["last_name"]))
    name_known = all([fn_r, ln_r, fn_i, ln_i])
    name_ok = name_known and fn_r == fn_i and ln_r == ln_i
    if name_known and not name_ok:
        return CONFLICT, ["name differs"]

    email_r = normalise_email(rec.get("email"))
    email_exact = bool(email_r) and email_r in {
        normalise_email(first_scalar(flat.get(k))) for k in ("work_email", "personal_email", "email")}

    co_ok = company_similar(rec.get("company"), pick(flat, ["company_name"]))
    if name_ok:
        reasons.append("name matches")
        if co_ok:
            reasons.append("company matches")
            return MEDIUM, reasons
        if email_exact:
            reasons.append("email matches")
            return MEDIUM, reasons
        return WEAK, reasons + ["company not confirmed"]
    if email_exact:
        return MEDIUM, ["email matches (name not available to compare)"]
    return WEAK, ["no verifiable match"]
