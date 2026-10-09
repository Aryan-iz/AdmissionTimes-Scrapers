# AdmissionTimes Scrapers - Codebase Documentation and Review

## Purpose

This repository scrapes undergraduate admission timelines and program lists from multiple Pakistani universities, normalizes the data into one schema, writes to PostgreSQL, and stores JSON backups per scraper.

## Scope Covered

This documentation is based on a full read-through of the codebase currently present in the workspace, including:

- Master orchestration and CI workflow
- Shared DB normalization/upsert module
- All 6 production scrapers
- Existing README and subfolder docs

## High-Level Architecture

1. `runner.py` executes all scraper entrypoints in a fixed order.
2. Each scraper extracts data from target university websites (requests/Selenium/PDF parsing).
3. Each scraper normalizes output via `normalize_admission_record` from `db/insert_admissioin.py`.
4. Each scraper attempts DB upsert via `insert_admission`.
5. Each scraper writes local JSON backup files in university-specific output folders.
6. GitHub Actions runs `runner.py` on schedule and uploads logs/output artifacts.

## Standard Data Schema

All scrapers eventually map data to this shape:

```json
{
  "university": "string",
  "program_title": "string",
  "publish_date": "readable date string or null",
  "last_date": "readable date string or null",
  "details_link": "url",
  "programs_offered": ["string", "..."]
}
```

Notes:

- Date normalization is centralized in `db/insert_admissioin.py` using `dateutil.parser` and converted to a readable format.
- Program lists are trimmed, deduplicated, and type-normalized to arrays.

## Core Modules

### `runner.py`

Responsibilities:

- Defines execution order of all production scrapers.
- Runs each script with the current Python interpreter.
- Continues after failures and prints summary.
- Returns non-zero exit code if any scraper fails.

CLI options:

- `--list`: print ordered scraper list and exit.
- `--sleep-seconds`: delay between scripts (default 3).

### `db/insert_admissioin.py`

Responsibilities:

- Loads root `.env` (manual parser).
- Normalizes records and payloads.
- Upserts into `scraped_admissions`.
- Skips write when `last_date` has not changed.

Behavior details:

- Primary lookup: `university + program_title`.
- Legacy fallback lookup: `university` only.
- Uses JSONB (`programs_offered`) via `psycopg2.extras.Json`.

### `db/migrate_programs_offered.py`

Responsibilities:

- Adds `programs_offered JSONB` column if missing.
- Prints current table structure after migration.

## Scraper-by-Scraper Behavior

### FAST University

- File: `FAST University/fast-scraper-standalone.py`
- Uses Selenium + BeautifulSoup.
- Extracts undergraduate programs from degree-program links.
- Extracts date range from admissions schedule tables.
- Saves backup to `FAST University/output/fast_admissions.json`.

### GIKI

- File: `GIKI/giki_scraper_standalone.py`
- Uses requests + BeautifulSoup with retry logic.
- Dynamic scraping with fallback program list.
- Admission dates fallback to latest backup file if live fetch fails.
- Writes timestamped and latest backups under `GIKI/output/giki/`.

### IBA Karachi

- File: `IBA Karachi/ibakarachi-scraper-standalone.py`
- Uses Selenium + BeautifulSoup.
- Parses round-based schedule tables.
- Chooses active round from date window (active, then upcoming, then latest deadline fallback).
- Writes backup to `IBA Karachi/output/iba_karachi_admissions.json`.

### IBA Sukkur

- File: `IBASukkur/iba-scraper-standalone.py`
- Uses requests + BeautifulSoup; parses announcement pages and detail page PDFs.
- Uses PyPDF2 text extraction.
- Attempts AI-assisted program extraction via OpenRouter; falls back to deterministic PDF parsing.
- Writes timestamped backup to `IBASukkur/output/` and logs to `IBASukkur/logs/`.

### Muhammad Ali Jinnah University (MAJU)

- File: `Muhammd  Ali Jinnah/muhammadalijinnah-scraper-standalone.py`
- Uses Selenium + BeautifulSoup.
- Scrapes admission date table and undergraduate program cards.
- Uses detected semester in program title.
- Writes timestamped backup to `Muhammd  Ali Jinnah/output/` and logs to `Muhammd  Ali Jinnah/logs/`.

### NUTECH

- File: `NUTECH/nutech-scraper-standalone.py`
- Uses Selenium + BeautifulSoup + webdriver-manager.
- Extracts programs from list/text patterns.
- Extracts active admission windows from schedule tables; computes deadline from windows or text fallback.
- Writes timestamped backup to `NUTECH/output/` and logs to `NUTECH/logs/`.

## Environment Variables

Primary variables used:

- `DATABASE_URL` for PostgreSQL writes.
- `scraperapikey` for AI-enhanced flows (especially IBA Sukkur).
- `Geminiapikey` is passed in CI for compatibility.

Observed behavior:

- Some modules use manual `.env` parsing.
- Some modules use `python-dotenv`.
- Several scripts continue successfully even when AI key is missing/invalid.

## CI/CD Workflow

Workflow file: `.github/workflows/scraper.yml`

Pipeline summary:

1. Checkout repository.
2. Setup Python 3.11 and pip cache.
3. Setup Chrome.
4. Install root dependencies.
5. Run `python runner.py` with one retry loop.
6. On failure, print failed lines from `runner.log`.
7. Always upload artifacts (`logs`, `output`, admissions JSON, `runner.log`).

## Code Review Findings

Severity: High

1. `db/insert_admissioin.py`: update-skip logic only compares `last_date`. If `programs_offered`, `publish_date`, or `details_link` changes while `last_date` stays the same, DB update is skipped and stale data can persist.

2. Multiple scripts manually parse `.env` with naive `key=value` splitting and no guard for malformed lines, causing runtime warnings/errors like "not enough values to unpack" when `.env` contains unexpected lines.

Severity: Medium

3. `GIKI/giki_scraper_standalone.py` disables SSL verification (`VERIFY_SSL = False`). This increases MITM/security risk and can hide certificate problems.

4. `NUTECH/nutech-scraper-standalone.py` sets both `--headless=new` and `--headless`. Redundant/possibly conflicting browser flags reduce clarity and can cause environment-specific behavior.

5. Inconsistent env loading strategy across scrapers (`dotenv` vs manual parser) increases maintenance risk and inconsistent behavior between local and CI runs.

Severity: Low

6. Filename typo in shared DB module (`insert_admissioin.py`) is harmless functionally but can reduce discoverability and increase confusion.

7. `Muhammd  Ali Jinnah/README.md` appears outdated versus current file layout and script names.

## Execution Report (Run Date: 2026-05-19)

Command flow executed locally:

1. `python -m pip install -r requirements.txt`
2. `python runner.py`

Final run summary:

- Total scrapers: 6
- Succeeded: 5
- Failed: 1

Per-scraper outcome:

1. FAST University: Failed (Selenium request timeout while navigating).
2. GIKI: Success.
3. IBA Karachi: Success.
4. IBA Sukkur: Success (AI API returned 401, scraper continued with fallback parsing).
5. MAJU: Success (warning: malformed `.env` line handling, missing deadline in current source page).
6. NUTECH: Success (warning: malformed `.env` line handling; publish date not detected).

Output artifacts observed from this run:

- `GIKI/output/giki/giki_admissions_20260519_181351.json`
- `IBASukkur/output/iba_sukkur_admissions_20260519_181414.json`
- `Muhammd  Ali Jinnah/output/maju_admissions_20260519_181426.json`
- `NUTECH/output/nutech_admissions_20260519_181457.json`
- `IBA Karachi/output/iba_karachi_admissions.json` (updated)

## Recommendations

1. Replace all manual `.env` parsing with `python-dotenv` to prevent malformed-line crashes/warnings and unify behavior.
2. Improve DB skip logic to compare a stable content fingerprint (or compare all mutable fields) instead of `last_date` only.
3. Enable SSL verification for GIKI and address any root certificate issues explicitly.
4. Add per-scraper integration tests with frozen HTML/PDF fixtures for parser stability.
5. Add retry/error handling in FAST Selenium navigation similar to other robust scrapers.
