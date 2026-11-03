import os
from pathlib import Path


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _data_dir() -> str:
    d = os.environ.get("DATA_DIR")
    if d:
        return d
    return "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else str(Path.cwd() / "data")


class Settings:
    """All configuration comes from environment variables (set them in Railway)."""

    def __init__(self):
        # The page lives under /s/<ACCESS_TOKEN>/ - anyone with the link can use it, nobody else can find it.
        self.access_token = os.environ.get("ACCESS_TOKEN", "")
        self.datamoon_api_key = os.environ.get("DATAMOON_API_KEY", "")
        self.qc_url = os.environ.get("QC_WEBHOOK_URL", "")      # the n8n "Pre-QC (API)" webhook
        self.qc_token = os.environ.get("QC_WEBHOOK_TOKEN", "")
        self.data_dir = _data_dir()                              # mount a Railway volume here to keep results across deploys
        self.retention_days = _int("RETENTION_DAYS", 7)
        self.rate_cooldown_sec = _int("RATE_LIMIT_COOLDOWN_SEC", 60)  # pause when the Datamoon quota is nearly used
        self.max_rows = _int("MAX_ROWS_PER_JOB", 100)
        self.max_upload_mb = _int("MAX_UPLOAD_MB", 20)
        self.max_jobs_per_hour = _int("MAX_JOBS_PER_HOUR", 20)   # whole service
        self.max_jobs_per_ip_hour = _int("MAX_JOBS_PER_IP_HOUR", 5)
        self.workers = _int("WORKERS", 2)

    def problems(self):
        out = []
        if len(self.access_token) < 24:
            out.append("ACCESS_TOKEN must be set and at least 24 characters")
        if not self.datamoon_api_key:
            out.append("DATAMOON_API_KEY is not set")
        return out


settings = Settings()
