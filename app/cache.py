"""Enrichment cache (SQLite) for the 3-month rule: a contact enriched less than 90 days ago is not
sent to Datamoon again. Lives next to the job files, so it survives deploys only if DATA_DIR is a volume.
When Fabric is added this is replaced by the PMG_DWH datamoon_enrichment table."""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class EnrichmentCache:
    def __init__(self, root):
        Path(root).mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(Path(root) / "enrichment_cache.sqlite"), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS enrichment (
                contact_key TEXT PRIMARY KEY, enriched_at TEXT NOT NULL, status TEXT NOT NULL,
                confidence TEXT, mapped_json TEXT, job_id TEXT)""")
            self._db.commit()

    def get(self, key: str):
        if not key:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT enriched_at, confidence, mapped_json FROM enrichment WHERE contact_key=?", (key,)).fetchone()
        if not row:
            return None
        return {"at": datetime.fromisoformat(row[0]), "confidence": row[1] or "",
                "mapped": json.loads(row[2]) if row[2] else {}}

    def put(self, result, now: datetime, job_id: str = "") -> None:
        if not result.contact_key:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO enrichment VALUES (?,?,?,?,?,?)",
                (result.contact_key, now.isoformat(), result.status, result.confidence,
                 json.dumps(result.mapped) if result.mapped else None, job_id))
            self._db.commit()
