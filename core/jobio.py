"""Job folder helpers shared by the notebooks (Lakehouse Files/jobs/<job_id>/)."""
import json
import os
from datetime import datetime, timezone

BASE = "/lakehouse/default/Files/jobs"


def job_dir(job_id: str, base: str = BASE) -> str:
    return os.path.join(base, job_id)


def write_status(job_id: str, status: str, stage: str, pct: int, message: str = "",
                 counts: dict = None, base: str = BASE) -> None:
    d = job_dir(job_id, base)
    os.makedirs(d, exist_ok=True)
    payload = {"job_id": job_id, "status": status, "stage": stage, "pct": pct, "message": message,
               "counts": counts or {}, "updated_at": datetime.now(timezone.utc).isoformat()}
    tmp = os.path.join(d, "status.json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, os.path.join(d, "status.json"))
