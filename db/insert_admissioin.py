# db/insert_admissioin.py

import os
import psycopg2
from psycopg2.extras import Json
from datetime import date, datetime
from dateutil import parser as date_parser
import hashlib

from db.backend_ingest import backend_configured, queue_record

# Load environment variables from root .env
def _load_root_env():
    root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(root_env):
        return

    try:
        with open(root_env, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
    except Exception:
        # Keep scraper flow resilient; missing env will be handled later when accessed.
        pass


_load_root_env()


def _to_readable_date(value):
    """Normalize date values to a consistent readable format."""
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = date_parser.parse(text, fuzzy=True)
        except Exception:
            return text

    return dt.strftime("%A, %B %d, %Y")


def _parse_date_to_dateobj(value):
    """Parse various date representations to a date object (no time)."""
    if value is None:
        return None

    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None
    try:
        dt = date_parser.parse(text, fuzzy=True)
        return dt.date()
    except Exception:
        return None


def normalize_admission_record(record):
    """Ensure every scraper record follows the exact standard schema and types."""
    programs = record.get("programs_offered", [])
    if programs is None:
        programs = []
    elif not isinstance(programs, list):
        programs = [programs]

    normalized_programs = []
    for program in programs:
        text = str(program).strip()
        if text:
            normalized_programs.append(text)

    # Deduplicate while preserving order
    normalized_programs = list(dict.fromkeys(normalized_programs))

    return {
        "university": str(record.get("university", "")).strip(),
        "program_title": str(record.get("program_title", "")).strip(),
        "publish_date": _to_readable_date(record.get("publish_date")),
        "last_date": _to_readable_date(record.get("last_date")),
        "details_link": str(record.get("details_link", "")).strip(),
        "programs_offered": normalized_programs,
    }


def normalize_admission_payload(data):
    """Normalize either a single record or list of records to standard list payload."""
    if isinstance(data, list):
        return [normalize_admission_record(item) for item in data]
    return [normalize_admission_record(data)]


def compute_source_hash(record):
    # Fallback hash used to compare important fields (university, program_title, details_link, last_date)
    key = (
        (record.get("university") or "").strip().lower() + "|" +
        (record.get("program_title") or "").strip().lower() + "|" +
        (record.get("details_link") or "").strip() + "|" +
        (record.get("last_date") or "")
    )
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def insert_admission(record):
    """
    Inserts or updates ONE admission record into PostgreSQL using UPSERT logic.
    If a record with the same university exists, it will be updated with new data.
    Otherwise, a new record will be inserted.
    
    This function is meant to be IMPORTED and used by scrapers.
    
    Args:
        record: Dictionary containing admission data with keys:
                - university
                - program_title
                - publish_date
                - last_date
                - details_link
                - programs_offered (array)
    """

    record = normalize_admission_record(record)

    # The backend decides what becomes an admission; this queues the record for
    # the end-of-run push (see db/backend_ingest.py and runner.py).
    queued = queue_record(record)

    # scraped_admissions is the scrapers' own mirror/history table. It is
    # optional when the pipeline pushes to the backend.
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        if queued or backend_configured():
            return
        raise RuntimeError("Set DATABASE_URL (scraper mirror) or BACKEND_BASE_URL + SCRAPER_INTERNAL_SERVICE_TOKEN")

    conn = None

    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        # Convert programs_offered to JSON for storage
        programs_json = Json(record.get("programs_offered", []))

        # Check if a record already exists for same university + program title.
        cursor.execute(
            """
            SELECT id, program_title, last_date, details_link, programs_offered
            FROM scraped_admissions
            WHERE university = %s AND program_title = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (record["university"], record["program_title"]),
        )
        existing = cursor.fetchone()

        # Backward-compatible fallback for legacy schema where university is unique.
        if not existing:
            cursor.execute(
                """
                SELECT id, program_title, last_date
                FROM scraped_admissions
                WHERE university = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (record["university"],),
            )
            existing_uni = cursor.fetchone()
            if existing_uni:
                existing = (existing_uni[0], existing_uni[2])

        if existing:
            existing_id, existing_program_title, existing_last_date, existing_details_link, existing_programs = existing
            existing_last_date = _to_readable_date(existing_last_date)
            new_last_date = record.get("last_date")

            # Parse to date objects for stale checks
            existing_date_obj = _parse_date_to_dateobj(existing_last_date)
            incoming_date_obj = _parse_date_to_dateobj(new_last_date)
            today = datetime.now().date()

            # If incoming last_date is far in the past (e.g., > 365 days), treat as stale and skip
            try:
                if incoming_date_obj and (today - incoming_date_obj).days > 365:
                    print(f"[INFO] Incoming deadline appears old/stale ({incoming_date_obj}); skipping: {record['university']} ({record['program_title']})")
                    conn.rollback()
                    return
            except Exception:
                pass

            # If the database already has a newer deadline for this university/program, skip the update
            try:
                if existing_date_obj and incoming_date_obj and incoming_date_obj < existing_date_obj and existing_date_obj >= today:
                    print(f"[INFO] Incoming deadline {incoming_date_obj} is older than existing {existing_date_obj}; skipping update for {record['university']} ({record['program_title']})")
                    conn.rollback()
                    return
            except Exception:
                pass
            existing_details_link = existing_details_link or ""
            existing_programs = existing_programs or []

            # Build a simple comparison: if any of the crucial fields changed, update.
            changed = False
            if (existing_program_title or "") != (record.get("program_title") or ""):
                changed = True
            if (existing_details_link or "") != (record.get("details_link") or ""):
                changed = True
            if (existing_last_date or "") != (new_last_date or ""):
                changed = True
            # Compare programs_offered lengths or content
            try:
                if list(existing_programs) != list(record.get("programs_offered", [])):
                    changed = True
            except Exception:
                changed = True

            if not changed:
                print(f"[INFO] No changes for: {record['university']} ({record['program_title']})")
                conn.rollback()
                return

            cursor.execute(
                """
                UPDATE scraped_admissions
                SET program_title = %s,
                    publish_date = %s,
                    last_date = %s,
                    details_link = %s,
                    programs_offered = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    record.get("program_title"),
                    record.get("publish_date"),
                    record.get("last_date"),
                    record.get("details_link"),
                    programs_json,
                    existing_id,
                ),
            )
            conn.commit()
            print(f"[OK] Updated: {record['university']}")
            print(f"   Program: {record['program_title']}")
            print(f"   Last Date: {record.get('last_date')}")

            # Deduplicate: remove any other rows with same university+program_title keeping the updated row
            try:
                cursor.execute(
                    """
                    DELETE FROM scraped_admissions
                    WHERE university = %s AND program_title = %s AND id != %s
                    """,
                    (record.get("university"), record.get("program_title"), existing_id),
                )
                deleted = cursor.rowcount
                if deleted:
                    conn.commit()
                    print(f"[INFO] Removed {deleted} duplicate(s) for {record['university']} - {record['program_title']}")
                else:
                    conn.rollback()
            except Exception as e:
                conn.rollback()
                print("[WARN] Dedupe failed after update:", e)

            # If this record is an aggregated record (multiple programs) and the deadline changed,
            # propagate the deadline to other rows for the same university that have a different last_date.
            try:
                is_aggregated = len(record.get("programs_offered", [])) > 1
            except Exception:
                is_aggregated = False

            if is_aggregated and (existing_last_date or "") != (new_last_date or ""):
                try:
                    cursor.execute(
                        """
                        UPDATE scraped_admissions
                        SET last_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE university = %s AND id != %s AND (last_date IS DISTINCT FROM %s OR last_date IS NULL)
                        """,
                        (
                            record.get("last_date"),
                            record.get("university"),
                            existing_id,
                            record.get("last_date"),
                        ),
                    )
                    conn.commit()
                    print(f"[INFO] Propagated last_date to other programs for university: {record['university']}")
                except Exception as e:
                    conn.rollback()
                    print("[WARN] Failed to propagate last_date to sibling records:", e)

            return

        # Before inserting, check if another row for this university already has a newer deadline.
        incoming_date_obj = _parse_date_to_dateobj(record.get("last_date"))
        try:
            cursor.execute(
                """
                SELECT MAX(last_date) FROM scraped_admissions WHERE university = %s
                """,
                (record.get("university"),),
            )
            max_row = cursor.fetchone()
            max_last_date = None
            if max_row:
                max_last_date = max_row[0]
            max_date_obj = _parse_date_to_dateobj(max_last_date)
            today = datetime.now().date()
            if incoming_date_obj and max_date_obj and incoming_date_obj < max_date_obj and max_date_obj >= today:
                print(f"[INFO] Incoming insert skipped because a newer deadline exists for university {record.get('university')}")
                conn.rollback()
                return
        except Exception:
            # If the check fails, continue with insert (fail-safe)
            pass

        cursor.execute(
            """
            INSERT INTO scraped_admissions (
                university,
                program_title,
                publish_date,
                last_date,
                details_link,
                programs_offered
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                record["university"],
                record["program_title"],
                record.get("publish_date"),
                record.get("last_date"),
                record.get("details_link"),
                programs_json,
            ),
        )

        inserted_row = cursor.fetchone()
        inserted_id = inserted_row[0] if inserted_row else None

        conn.commit()
        print(f"[OK] Inserted: {record['university']}")
        print(f"   Program: {record['program_title']}")
        print(f"   Last Date: {record.get('last_date')}")

        # Deduplicate: remove any other rows with same university+program_title keeping the inserted row
        if inserted_id is not None:
            try:
                cursor.execute(
                    """
                    DELETE FROM scraped_admissions
                    WHERE university = %s AND program_title = %s AND id != %s
                    """,
                    (record.get("university"), record.get("program_title"), inserted_id),
                )
                deleted = cursor.rowcount
                if deleted:
                    conn.commit()
                    print(f"[INFO] Removed {deleted} duplicate(s) for {record['university']} - {record['program_title']}")
                else:
                    conn.rollback()
            except Exception as e:
                conn.rollback()
                print("[WARN] Dedupe failed after insert:", e)

    except Exception as e:
        if conn:
            conn.rollback()
        print("[ERROR] DB upsert failed:", e)
        raise  # Re-raise to let caller handle the error

    finally:
        if conn:
            conn.close()
