# db/backend_ingest.py
"""
Forward scraped records to the AdmissionTimes backend.

The backend's internal endpoint (POST /api/v1/internal/scraper/ingest-batch)
owns how scraped data becomes admissions: university matching, the stable
program identity, verification rules, deadline sync, watcher notifications and
the run log shown in the admin scraper monitor. Scrapers therefore never write
the app's `admissions` table directly.

Flow:
  * runner.py sets SCRAPER_SPOOL_FILE before starting the scrapers.
  * insert_admission() calls queue_record() for every normalized record, which
    appends one JSON line to the spool file (scrapers run as subprocesses).
  * After all scrapers finish, runner.py calls push_spool() once, so one
    pipeline run is one ingestion run in the backend.

Configuration (environment or the repo .env):
  BACKEND_BASE_URL                e.g. https://api.example.com
  SCRAPER_INTERNAL_SERVICE_TOKEN  must match the backend's token
  SCRAPER_INGEST_ENDPOINT         optional, default /api/v1/internal/scraper/ingest-batch
"""

import json
import os
import urllib.error
import urllib.request

from dateutil import parser as date_parser

DEFAULT_ENDPOINT = "/api/v1/internal/scraper/ingest-batch"
# Must not exceed MAX_INGEST_RECORDS in the backend validator.
BATCH_SIZE = 200
REQUEST_TIMEOUT_SECONDS = 120


def load_root_env():
    """Load the repo .env without overriding variables already set (e.g. CI secrets)."""
    root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(root_env):
        return
    with open(root_env, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def backend_configured():
    return bool(os.getenv("BACKEND_BASE_URL", "").strip() and os.getenv("SCRAPER_INTERNAL_SERVICE_TOKEN", "").strip())


def _iso_day(value):
    """Send calendar days as YYYY-MM-DD; the backend closes them at end of day (app timezone)."""
    if not value:
        return None
    # Exact parse (no year guessing): a stale deadline must stay stale.
    try:
        return date_parser.parse(str(value), fuzzy=True).date().isoformat()
    except (ValueError, OverflowError):
        return None


def to_ingest_record(record):
    """Map a normalized scraper record to the backend ingest DTO, or None if it cannot be ingested."""
    university = (record.get("university") or "").strip()
    title = (record.get("program_title") or "").strip()
    last_date = _iso_day(record.get("last_date"))
    if not university or not title or not last_date:
        return None

    return {
        "source_university_name": university,
        "source_program_title": title,
        "source_last_date": last_date,
        "source_details_link": (record.get("details_link") or "").strip(),
        "source_publish_date": _iso_day(record.get("publish_date")),
        "programs_offered": list(record.get("programs_offered") or []),
    }


def queue_record(record):
    """Append a normalized record to the spool file for the end-of-run push (no-op without a spool)."""
    spool = os.getenv("SCRAPER_SPOOL_FILE", "").strip()
    if not spool:
        return False

    payload = to_ingest_record(record)
    if payload is None:
        print(f"[WARN] Not forwarded to backend (missing university/title/deadline): {record.get('university')} - {record.get('program_title')}")
        return False

    with open(spool, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return True


def read_spool(path):
    """Read spooled records, keeping the last occurrence of each university+title."""
    records = {}
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (item["source_university_name"].lower(), " ".join(item["source_program_title"].lower().split()))
            records[key] = item
    return list(records.values())


def _post(url, token, body):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "x-internal-service-token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Backend ingest failed with HTTP {error.code}: {detail}") from error


def push_records(records, requested_by="scraper-pipeline"):
    """POST records to the backend in batches. Returns the list of run summaries; raises on failure."""
    base_url = os.getenv("BACKEND_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("SCRAPER_INTERNAL_SERVICE_TOKEN", "").strip()
    endpoint = os.getenv("SCRAPER_INGEST_ENDPOINT", "").strip() or DEFAULT_ENDPOINT
    if not base_url or not token:
        raise RuntimeError("BACKEND_BASE_URL and SCRAPER_INTERNAL_SERVICE_TOKEN must be set to push to the backend")

    url = base_url + (endpoint if endpoint.startswith("/") else "/" + endpoint)
    summaries = []
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start:start + BATCH_SIZE]
        result = _post(url, token, {"records": batch, "requested_by": requested_by, "university_scope": "all"})
        summary = result.get("data") or {}
        summaries.append(summary)
        print(
            "[BACKEND] run {run} ({mode}): fetched={fetched} published={published} updated={updated} "
            "skipped={skipped} failed={failed} status={status}".format(
                run=summary.get("run_id"),
                mode=summary.get("mode"),
                fetched=summary.get("fetched_count"),
                published=summary.get("published_count"),
                updated=summary.get("updated_count"),
                skipped=summary.get("skipped_count"),
                failed=summary.get("failed_count"),
                status=summary.get("status"),
            )
        )
    return summaries


def push_spool(path, requested_by="scraper-pipeline"):
    records = read_spool(path)
    if not records:
        print("[BACKEND] No records to push.")
        return []
    print(f"[BACKEND] Pushing {len(records)} record(s) to the backend...")
    return push_records(records, requested_by=requested_by)
