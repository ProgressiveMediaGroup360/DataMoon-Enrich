"""Per-contact enrichment: cache rule, lookup order, match verification, merge."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .mapper import map_identity, merge
from .match import CONFLICT, OVERWRITE_ALLOWED, STRONG, MEDIUM, WEAK, verify_match
from .normalise import contact_key, normalise_email, normalise_linkedin, normalise_phone

MIN_AGE_DAYS = 90  # contacts enriched less than ~3 months ago are not re-enriched
_RANK = {STRONG: 3, MEDIUM: 2, WEAK: 1, CONFLICT: 0}


@dataclass
class EnrichResult:
    contact_key: str
    status: str  # enriched | skipped_recent | no_match | no_lookup_key | error
    merged: dict
    endpoint: str = ""
    confidence: str = ""
    reasons: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    mapped: dict = field(default_factory=dict)
    identity: Optional[dict] = None
    api_status: int = 0
    error: str = ""
    remaining: Optional[int] = None


def is_recent(last_enriched_at: Optional[datetime], now: datetime, min_age_days: int = MIN_AGE_DAYS) -> bool:
    return bool(last_enriched_at) and (now - last_enriched_at) < timedelta(days=min_age_days)


class LinkedInLookup:
    """Lookup by LinkedIn URL. NOT in Datamoon's published API, so it is tried on a best-effort basis:
    the endpoint and the parameter name are configurable, a 422 makes us try the next parameter name,
    and a 404/405 (or every name rejected) switches the lookup off for the rest of the job."""

    def __init__(self, endpoint="GetDataByLinkedin", params=("linkedin_url", "linkedin", "linkedinUrl", "url")):
        self.endpoint = endpoint
        self.params = list(params)
        self.disabled_reason = ""
        self.working_param = ""

    def call(self, client, url: str) -> dict:
        if self.disabled_reason:
            return {"status": 404, "ok": False, "identities": [], "remaining": None, "limit": None,
                    "error": self.disabled_reason}
        candidates = [self.working_param] if self.working_param else self.params
        res = None
        for param in candidates:
            res = client.call(self.endpoint, {param: url})
            if res["status"] in (404, 405):
                self.disabled_reason = f"Datamoon has no LinkedIn lookup at '{self.endpoint}' (HTTP {res['status']})"
                res["error"] = self.disabled_reason
                return res
            if res["status"] == 422:
                continue                      # wrong parameter name: try the next one
            self.working_param = param
            return res
        self.disabled_reason = f"Datamoon rejected every LinkedIn parameter name tried ({', '.join(candidates)}): {res['error']}"
        res["error"] = self.disabled_reason
        return res


def lookup_plan(rec: dict, linkedin=None):
    """Lookups in priority order. Name+address (GetDataByPii) is intentionally not
    used yet: its parameters are unverified and it needs a street address + zip."""
    plan = []
    email = normalise_email(rec.get("email"))
    if email:
        plan.append(("GetDataByEmail", {"email": email}))
    phone = normalise_phone(rec.get("phone"))
    if phone:
        plan.append(("GetDataByPhone", {"phone": phone}))  # parameter name confirmed in Datamoon's docs
    li = normalise_linkedin(rec.get("linkedin_url"))
    if li and linkedin is not None:
        plan.append((linkedin.endpoint, {"__linkedin__": str(rec.get("linkedin_url")).strip()}))
    return plan


def best_identity(rec: dict, identities: list):
    """Pick the identity that verifies best against the row."""
    best = None
    for ident in identities:
        conf, reasons = verify_match(rec, ident)
        if best is None or _RANK[conf] > _RANK[best[1]]:
            best = (ident, conf, reasons)
    return best


def enrich_record(client, rec: dict, now: datetime, last_enriched_at: Optional[datetime] = None,
                  cached: Optional[dict] = None, min_age_days: int = MIN_AGE_DAYS,
                  linkedin: Optional[LinkedInLookup] = None) -> EnrichResult:
    key = contact_key(rec)

    # 1. recent enough: reuse what we stored last time, no API call
    if is_recent(last_enriched_at, now, min_age_days) and cached:
        conf = cached.get("confidence", "")
        mapped = cached.get("mapped") or {}
        merged, changed = merge(rec, mapped, conf in OVERWRITE_ALLOWED)
        return EnrichResult(key, "skipped_recent", merged, endpoint="cache", confidence=conf,
                            reasons=["enriched within %d days" % min_age_days], changed=changed, mapped=mapped)

    plan = lookup_plan(rec, linkedin)
    if not plan:
        return EnrichResult(key, "no_lookup_key", dict(rec), reasons=["no email, phone or LinkedIn URL"])

    last_error, last_status = "", 0
    for endpoint, payload in plan:
        res = linkedin.call(client, payload["__linkedin__"]) if "__linkedin__" in payload else client.call(endpoint, payload)
        last_status, last_error = res["status"], res["error"]
        if res["status"] in (401, 403):  # bad key: stop immediately
            return EnrichResult(key, "error", dict(rec), endpoint=endpoint, api_status=res["status"],
                                error=res["error"] or "unauthorised", remaining=res["remaining"])
        if not res["ok"] or not res["identities"]:
            continue
        ident, conf, reasons = best_identity(rec, res["identities"])
        mapped = map_identity(ident)
        merged, changed = merge(rec, mapped, conf in OVERWRITE_ALLOWED)
        return EnrichResult(key, "enriched", merged, endpoint=endpoint, confidence=conf, reasons=reasons,
                            changed=changed, mapped=mapped, identity=ident,
                            api_status=res["status"], remaining=res["remaining"])

    if last_error:
        return EnrichResult(key, "error", dict(rec), api_status=last_status, error=last_error)
    return EnrichResult(key, "no_match", dict(rec), endpoint=plan[-1][0], api_status=last_status)
