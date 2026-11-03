"""Job files on local disk: DATA_DIR/jobs/<job_id>/{meta.json,status.json,input/,outputs/}.

Railway containers have ephemeral disks: attach a volume at DATA_DIR (default /data) to keep results
and the enrichment cache across deploys. Old jobs are deleted after RETENTION_DAYS.
"""
import json
import os
import shutil
import time
from pathlib import Path


class JobStore:
    def __init__(self, root):
        self.root = Path(root) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str, rel: str = "") -> Path:
        p = (self.root / job_id / rel).resolve()
        if self.root.resolve() not in p.parents and p != (self.root / job_id).resolve():
            raise ValueError("path escapes the job folder")
        return p

    def write_bytes(self, job_id: str, rel: str, data: bytes) -> None:
        p = self.path(job_id, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def read_bytes(self, job_id: str, rel: str) -> bytes:
        return self.path(job_id, rel).read_bytes()

    def write_json(self, job_id: str, rel: str, obj) -> None:
        self.write_bytes(job_id, rel, json.dumps(obj).encode("utf-8"))

    def read_json(self, job_id: str, rel: str):
        return json.loads(self.read_bytes(job_id, rel).decode("utf-8"))

    def exists(self, job_id: str, rel: str) -> bool:
        return self.path(job_id, rel).exists()

    def cleanup(self, retention_days: int) -> int:
        cutoff, removed = time.time() - retention_days * 86400, 0
        for d in self.root.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed

    def running_jobs(self):
        for d in self.root.iterdir():
            try:
                if json.loads((d / "status.json").read_text()).get("status") in ("queued", "running"):
                    yield d.name
            except Exception:
                continue
