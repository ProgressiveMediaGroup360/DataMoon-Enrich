"""Thin Datamoon Enrichment API client.

Facts verified with a live call (2 Nov 2026): POST https://app.datamoon.com/api/v2/GetDataByEmail
with the key in the JSON body returns HTTP 200 {"success": true, "customId": ...,
"data": {"identities": []}} - an empty list means no match, not an error. Rate limit
headers x-ratelimit-limit / x-ratelimit-remaining are returned (limit 100; the window
is not known yet).

Not verified: the GetDataByPhone parameter name, and the shape of a non-empty identity.
The API key is never logged.
"""
import time

import requests

BASE_URL = "https://app.datamoon.com/api/v2"


class DatamoonClient:
    def __init__(self, api_key, session=None, base_url=BASE_URL, max_retries=4,
                 cooldown_sec=60, timeout=30, sleep=time.sleep):
        if not api_key:
            raise ValueError("Datamoon API key is required")
        self._key = api_key
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.cooldown_sec = cooldown_sec  # pause when the quota is nearly used up
        self.timeout = timeout
        self._sleep = sleep
        self.last_remaining = None

    def call(self, endpoint: str, payload: dict) -> dict:
        """POST one lookup. Returns {status, ok, identities, remaining, limit, error}."""
        body = {"api_key": self._key, **payload}
        url = f"{self.base_url}/{endpoint}"
        last_err = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.post(url, json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = type(e).__name__
                self._sleep(min(2 ** attempt, 30))
                continue

            remaining = _int(r.headers.get("x-ratelimit-remaining"))
            limit = _int(r.headers.get("x-ratelimit-limit"))
            self.last_remaining = remaining

            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                wait = _int(r.headers.get("retry-after")) or min(2 ** attempt, 30)
                if r.status_code == 429:
                    wait = max(wait, self.cooldown_sec)
                self._sleep(wait)
                continue

            try:
                data = r.json()
            except ValueError:
                return _result(r.status_code, False, [], remaining, limit, "non-JSON response")

            ok = r.status_code == 200 and bool(data.get("success", True))
            identities = ((data.get("data") or {}).get("identities") or []) if ok else []
            err = "" if ok else str(data.get("error") or data.get("message") or f"HTTP {r.status_code}")[:200]
            # Stay inside the quota: pause when almost exhausted.
            if remaining is not None and remaining <= 1:
                self._sleep(self.cooldown_sec)
            return _result(r.status_code, ok, identities, remaining, limit, err)

        return _result(0, False, [], self.last_remaining, None, f"gave up after retries: {last_err}")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _result(status, ok, identities, remaining, limit, error):
    return {"status": status, "ok": ok, "identities": identities,
            "remaining": remaining, "limit": limit, "error": error}
