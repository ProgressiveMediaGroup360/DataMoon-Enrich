"""Builds 'Pre-QC (API)' from the original Pre-QC export: same checks, but called by webhook (JSON in, JSON out).
Usage: python3 n8n/make_api_copy.py <export.json> <out.json>"""
import json, sys, uuid

src, dst = sys.argv[1], sys.argv[2]
w = json.load(open(src))
nodes = {n["name"]: n for n in w["nodes"]}

CHECK_INPUT = r"""// Node: "Check your input" -> reads the JSON sent by the enrichment app. Never throws: problems become messages.
const body = ($input.first().json.body) || {};
const fail = msg => [{ json: { ok: false, error: msg } }];
const SKIP = ['asset', 'date delivered', 'source id'];   // left empty on purpose, nothing to check

const lines = [];
for (const l of String(body.spec_text || '').split('\n')) {
  const t = l.trim(); if (!t) continue;
  const i = t.indexOf(':');
  lines.push(i > 0 ? t.slice(0, i).trim() + ' | ' + t.slice(i + 1).trim() : 'Other | ' + t);
}
if (!lines.length) return fail('No specs were sent, so there is nothing to check the rows against.');

if (!Array.isArray(body.columns) || !Array.isArray(body.rows) || !body.rows.length) return fail('The request had no columns or no rows.');
if (body.rows.length > 5000) return fail('Too many rows in one request (limit 5000).');
const keep = body.columns.map(String).filter(h => !SKIP.includes(h.trim().toLowerCase()));
if (keep.length < 2) return fail('The request did not contain usable columns.');
const rows = body.rows.map(r => {
  const o = {};
  keep.forEach(h => { const v = r && r[h]; o[h] = v === undefined || v === null ? '' : String(v); });
  return o;
});
const fileName = String(body.job_id || 'job').replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 64) + '.csv';
return [{ json: { ok: true, specText: 'Prospects criteria (entered by the enrichment app)\nField | Details\n' + lines.join('\n'), fileName, headers: keep, rows } }];
"""

# --- entry: webhook instead of the form
hook = {
    "parameters": {"httpMethod": "POST", "path": "pre-qc-api", "authentication": "headerAuth",
                   "responseMode": "responseNode", "options": {}},
    "id": "apihook", "name": "Pre-QC API", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
    "position": [0, 200], "webhookId": str(uuid.uuid4()),
    "credentials": {"httpHeaderAuth": {"id": "", "name": "Pre-QC API token"}},
}
nodes["Check your input"]["parameters"]["jsCode"] = CHECK_INPUT

# --- Assemble CSV: add JSON rows + summary, fix the band parser
a = nodes["Assemble CSV"]["parameters"]["jsCode"]
old_band_open = "  const open = /(and above|or more|\\+|and over|above|over)/.test(s);\n"
assert old_band_open in a
a = a.replace(old_band_open, old_band_open + "  const upTo = /(under|less than|below|up to|<)/.test(s) && !open;\n")
old_usd = "    return { lo: nums[0], hi: open || nums.length < 2 ? (open ? Infinity : nums[0]) : nums[1] };\n"
assert old_usd in a
a = a.replace(old_usd, "    if (upTo && nums.length === 1) return { lo: 0, hi: nums[0] };\n" + old_usd)
old_cnt = "  return { lo: ns[0], hi: open ? Infinity : (ns[1] !== undefined ? ns[1] : ns[0]) };\n"
assert old_cnt in a
a = a.replace(old_cnt, "  if (upTo && ns.length === 1) return { lo: 0, hi: ns[0] };\n" + old_cnt)
marker = "// ---- checked CSV\n"
assert marker in a
a = a.replace(marker, """// ---- rows for the API response (same order as the request)
const apiRows = out.map(r => ({
  overall: r['PreQC Overall'],
  checks: Object.fromEntries(criteria.map(c => [c.label, { status: r[colName(c)], comment: r[cmtName(c)] }])),
}));
""" + marker)
old_ret = "return [{ json: { file: base + '_PreQC.csv',"
assert old_ret in a
a = a.replace(old_ret, "return [{ json: { apiRows, summary: { rows: N, ...tally }, file: base + '_PreQC.csv',")
nodes["Assemble CSV"]["parameters"]["jsCode"] = a

# --- answers: Respond to Webhook
def respond(name, nid, pos, body_expr, code):
    return {"parameters": {"respondWith": "json", "responseBody": body_expr, "options": {"responseCode": code}},
            "id": nid, "name": name, "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": pos}

old_fail = nodes["Run failed"]["parameters"]["completionMessage"]
msg_expr = old_fail.split("\n\nPlease go back")[0]            # the '={{ ... }}' that maps errors to friendly text
assert msg_expr.startswith("={{")
fail_body = "={{ { error: " + msg_expr[3:-2].strip() + " } }}"
ok = respond("Return result", "returnresult", nodes["Download checked CSV"]["position"],
             "={{ { rows: $json.apiRows, summary: $json.summary } }}", 200)
bad = respond("Input problem", "inputproblem", nodes["Input problem"]["position"],
              "={{ { error: typeof $json.error === 'string' ? $json.error : 'Unknown problem' } }}", 400)
fail = respond("Run failed", "runfailed", nodes["Run failed"]["position"], fail_body, 500)

new_nodes = []
for n in w["nodes"]:
    nm = n["name"]
    if nm == "Upload form": new_nodes.append(hook)
    elif nm == "Download checked CSV": new_nodes.append(ok)
    elif nm == "Input problem": new_nodes.append(bad)
    elif nm == "Run failed": new_nodes.append(fail)
    else: new_nodes.append(n)
w["nodes"] = new_nodes

c = w["connections"]
c["Pre-QC API"] = c.pop("Upload form")
for k, v in c.items():
    for branch in v["main"]:
        for t in branch:
            if t["node"] == "Download checked CSV": t["node"] = "Return result"
w["name"] = "Pre-QC (API)"
w["active"] = False
json.dump(w, open(dst, "w"), indent=2, ensure_ascii=False)
print("ok", len(w["nodes"]), "nodes")
