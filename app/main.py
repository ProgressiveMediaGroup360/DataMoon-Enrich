"""Railway-hosted front end: drop a contact file, get it enriched (Datamoon) and quality-checked (Pre-QC).

Access: no login. The whole app lives under /s/<ACCESS_TOKEN>/ ; every other path is 404.
Protection against misuse (every job spends Datamoon credits): hard row cap, upload size cap, hourly
job limits (global and per IP), unguessable job ids, results deleted after RETENTION_DAYS.
"""
import csv
import io
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .cache import EnrichmentCache
from .config import settings
from .store import JobStore
from . import worker

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
router = APIRouter(prefix="/s/{token}")

PAGE = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
OUTPUTS = {"template.csv", "rejects.csv", "qc_report.csv", "everything.csv", "template_unchecked.csv"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SPEC_FIELDS = [("geography", "Geography"), ("industry", "Industry"), ("job_title", "Job Title"),
               ("job_function", "Job Function"), ("company_revenue", "Company Revenue"),
               ("employee_size", "Employee Size")]

_hits_global = deque()
_hits_ip = defaultdict(deque)
_state = {}


def state():
    """Lazily created so tests can point DATA_DIR elsewhere."""
    if not _state:
        store = JobStore(settings.data_dir)
        _state.update(store=store, cache=EnrichmentCache(settings.data_dir),
                      pool=ThreadPoolExecutor(max_workers=max(1, settings.workers)))
        for job_id in list(store.running_jobs()):   # threads die with a restart: don't leave jobs spinning
            worker._status(store, job_id, "failed", "error", 0, "The service restarted during this job. Please upload the file again.")
        store.cleanup(settings.retention_days)
    return _state


def _guard(token: str):
    if not secrets.compare_digest(token.encode(), settings.access_token.encode()):
        raise HTTPException(404)


def _rate_limit(ip: str):
    now = time.time()
    for dq in (_hits_global, _hits_ip[ip]):
        while dq and now - dq[0] > 3600:
            dq.popleft()
    if len(_hits_global) >= settings.max_jobs_per_hour or len(_hits_ip[ip]) >= settings.max_jobs_per_ip_hour:
        raise HTTPException(429, "Too many jobs started recently. Try again later.")
    _hits_global.append(now)
    _hits_ip[ip].append(now)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def _job_id(job_id: str) -> str:
    if not UUID_RE.match(job_id):
        raise HTTPException(404)
    return job_id


def compose_spec(values: dict) -> str:
    """Spec form -> 'Label: details' lines, the shape the Pre-QC workflow expects."""
    lines = []
    for key, label in SPEC_FIELDS:
        v = re.sub(r"\s*\n\s*", "; ", str(values.get(key) or "").strip())
        if v:
            lines.append(f"{label}: {v}")
    for l in str(values.get("other_criteria") or "").splitlines():
        l = l.strip()
        if l:
            lines.append(l)
    return "\n".join(lines)[:5000]


@router.get("/", response_class=HTMLResponse)
def page(token: str):
    _guard(token)
    return HTMLResponse(PAGE.replace("__MAX_ROWS__", str(settings.max_rows)),
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
                                 "Referrer-Policy": "no-referrer"})


@router.post("/api/jobs")
async def create_job(token: str, request: Request, file: UploadFile = File(...), client_name: str = Form(""),
                     geography: str = Form(""), industry: str = Form(""), job_title: str = Form(""),
                     job_function: str = Form(""), company_revenue: str = Form(""), employee_size: str = Form(""),
                     other_criteria: str = Form(""), run_qc: str = Form("1")):
    _guard(token)
    if settings.problems():
        raise HTTPException(503, "The service is not fully configured yet.")
    spec_text = compose_spec(dict(geography=geography, industry=industry, job_title=job_title,
                                  job_function=job_function, company_revenue=company_revenue,
                                  employee_size=employee_size, other_criteria=other_criteria))
    qc_on = run_qc.strip().lower() not in ("0", "false", "off", "no")
    if not qc_on:
        spec_text = ""
    elif not spec_text:
        raise HTTPException(400, "Add at least one spec (for example Geography or Job Title): the quality check needs something to check against.")
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ("csv", "xlsx"):
        raise HTTPException(400, "Upload a .csv or .xlsx file.")
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")
    if ext == "csv":
        try:
            rows = sum(1 for _ in csv.reader(io.StringIO(data.decode("utf-8-sig")))) - 1
        except UnicodeDecodeError:
            raise HTTPException(400, "CSV must be UTF-8 encoded.")
        if rows < 1:
            raise HTTPException(400, "The file has no data rows.")
        if rows > settings.max_rows:
            raise HTTPException(400, f"The file has {rows} rows; the limit is {settings.max_rows} per job.")
    _rate_limit(_client_ip(request))

    st = state()
    job_id = str(uuid.uuid4())
    store = st["store"]
    store.write_bytes(job_id, f"input/raw_upload.{ext}", data)
    store.write_bytes(job_id, "input/spec_text.txt", spec_text.encode("utf-8"))
    store.write_json(job_id, "meta.json", {"job_id": job_id, "client_name": client_name.strip()[:100],
                                           "filename": (file.filename or "")[:200],
                                           "qc_enabled": qc_on, "created_at": datetime.now(timezone.utc).isoformat()})
    worker._status(store, job_id, "queued", "queued", 2)
    st["pool"].submit(worker.run_job, job_id, settings, store, st["cache"])
    return {"job_id": job_id}


@router.get("/api/jobs/{job_id}")
def job_status(token: str, job_id: str):
    _guard(token)
    job_id = _job_id(job_id)
    store = state()["store"]
    try:
        status = store.read_json(job_id, "status.json")
    except Exception:
        raise HTTPException(404)
    status["files"] = [f for f in sorted(OUTPUTS) if store.exists(job_id, f"outputs/{f}")] \
        if status.get("status") == "complete" else []
    return JSONResponse(status, headers={"Cache-Control": "no-store"})


@router.get("/api/jobs/{job_id}/files/{name}")
def download(token: str, job_id: str, name: str):
    _guard(token)
    job_id = _job_id(job_id)
    if name not in OUTPUTS:
        raise HTTPException(404)
    try:
        data = state()["store"].read_bytes(job_id, f"outputs/{name}")
    except Exception:
        raise HTTPException(404, "File not available.")
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{job_id[:8]}_{name}"',
                             "Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"status": "ok" if not settings.problems() else "misconfigured"}


app.include_router(router)
