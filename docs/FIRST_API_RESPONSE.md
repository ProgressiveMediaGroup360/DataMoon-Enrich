# What we know about the Datamoon response

Verified (n8n test run, 2 Nov 2026, `POST /api/v2/GetDataByEmail`, key in the JSON body):

- HTTP 200, `{"success": true, "customId": "test-001", "data": {"identities": []}}`
- A no-match is an empty `identities` array, not an error.
- Headers: `x-ratelimit-limit: 100`, `x-ratelimit-remaining: 99`. The window is unknown.
- Responses set cookies (`XSRF-TOKEN`, `datamoon_session`); they are not needed.

Not yet seen: a response with a real identity. Until it is, `core/mapper.py` maps fields by the names in
the Business Person / Company Firmographics dictionary and is **unverified**.

To check it: run the n8n test workflow with a real work email and compare the masked output with
`FIELD_CANDIDATES` in `core/mapper.py`. Things to confirm:

1. Where the fields sit (flat per identity, or nested) and their casing. `core/match.flatten` handles both.
2. Whether `LINKEDIN_URL` is returned, and as a string or a list.
3. The `WORK_EMAIL_STATUS` values you actually get (`C`, `D`, `V` are undefined in the dictionary).
4. Whether Employee/Revenue ranges come back in the `<$1M` or the `UNDER 1 MILLION` style.
5. The parameter name for `GetDataByPhone` (the code sends `phone`).
