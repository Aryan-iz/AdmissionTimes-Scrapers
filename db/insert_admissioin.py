# db/insert_admissioin.py

import os
import psycopg2
from psycopg2.extras import Json
from datetime import date, datetime
from dateutil import parser as date_parser

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

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")

    conn = None

    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        # Convert programs_offered to JSON for storage
        programs_json = Json(record.get("programs_offered", []))

        # Check if a record already exists for same university + program title.
        cursor.execute(
            """
            SELECT id, last_date
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
            existing_id, existing_last_date = existing
            existing_last_date = _to_readable_date(existing_last_date)
            new_last_date = record.get("last_date")

            # Skip DB write when last_date has not changed.
            if (existing_last_date or "") == (new_last_date or ""):
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
            return

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

        conn.commit()
        print(f"[OK] Inserted: {record['university']}")
        print(f"   Program: {record['program_title']}")
        print(f"   Last Date: {record.get('last_date')}")

    except Exception as e:
        if conn:
            conn.rollback()
        print("[ERROR] DB upsert failed:", e)
        raise  # Re-raise to let caller handle the error

    finally:
        if conn:
            conn.close()
