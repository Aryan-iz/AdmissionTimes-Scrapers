# AdmissionTimes Scrapers

Production scraper suite for collecting undergraduate admissions information from multiple Pakistani universities, normalizing records, and writing to PostgreSQL.

## What This Repository Does

- Scrapes admission windows and programs from 6 university sources.
- Normalizes output into one standard schema.
- Stores results in PostgreSQL through a shared DB module.
- Saves JSON backups in scraper-specific output folders.
- Runs all scrapers sequentially via a master runner.
- Supports automated daily execution via GitHub Actions.

## Universities Covered

1. FAST University
2. GIKI
3. IBA Karachi
4. IBA Sukkur
5. Muhammad Ali Jinnah University (MAJU)
6. NUTECH

## Repository Structure

- `runner.py`: Master orchestrator that runs all production scrapers in order.
- `requirements.txt`: Root dependency set used for local and CI runs.
- `.github/workflows/scraper.yml`: Scheduled + manual GitHub Actions pipeline.
- `db/insert_admissioin.py`: Shared DB insert/update and normalization logic.
- `FAST University/`: FAST standalone scraper and output.
- `GIKI/`: GIKI standalone scraper and output.
- `IBA Karachi/`: IBA Karachi standalone scraper and output.
- `IBASukkur/`: IBA Sukkur standalone scraper, logs, and output.
- `Muhammd  Ali Jinnah/`: MAJU standalone scraper, logs, and output.
- `NUTECH/`: NUTECH standalone scraper, logs, and output.

## Production Execution Order

Defined in `runner.py`:

1. `FAST University/fast-scraper-standalone.py`
2. `GIKI/giki_scraper_standalone.py`
3. `IBA Karachi/ibakarachi-scraper-standalone.py`
4. `IBASukkur/iba-scraper-standalone.py`
5. `Muhammd  Ali Jinnah/muhammadalijinnah-scraper-standalone.py`
6. `NUTECH/nutech-scraper-standalone.py`

`runner.py` behavior:

- Uses the same Python interpreter for all scripts.
- Prints clear `[START]`, `[SUCCESS]`, and `[FAILED]` logs per scraper.
- Continues to next scraper if one fails.
- Returns non-zero exit code if any scraper fails.
- Sleeps briefly between scrapers (default: 3 seconds).

## Standard Record Schema

Each scraper normalizes records to this shape before DB write:

```json
{
  "university": "string",
  "program_title": "string",
  "publish_date": "readable date string",
  "last_date": "readable date string",
  "details_link": "url",
  "programs_offered": ["string", "..."]
}
```

## Environment Variables

Required environment variables:

- `DATABASE_URL`: PostgreSQL connection string.
- `scraperapikey`: OpenRouter API key used by AI-assisted scraping flows.
- `Geminiapikey`: Gemini key mapped in CI for compatibility.

Notes:

- Local runs can load values from root `.env`.
- CI does not rely on local `.env`; it uses GitHub Secrets.
- `.env` files are ignored by `.gitignore`.

## Local Setup

### 1) Create and activate virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure environment

Create `.env` in repo root:

```dotenv
DATABASE_URL=your_postgres_connection
scraperapikey=your_openrouter_key
Geminiapikey=your_gemini_key
```

### 4) Run all scrapers

```bash
python runner.py
```

Useful commands:

```bash
python runner.py --list
python runner.py --sleep-seconds 5
```

## GitHub Actions Automation

Workflow file: `.github/workflows/scraper.yml`

### Triggers

- Daily schedule: `30 17 * * *` (10:30 PM Pakistan time)
- Manual trigger: `workflow_dispatch`

### Pipeline Steps

1. Checkout repository
2. Setup Python 3.11
3. Setup Chrome
4. Install dependencies from root `requirements.txt`
5. Run `python runner.py` (with one retry on failure)
6. Upload artifacts (always)

### Required GitHub Secrets

Add these under **Settings -> Secrets and variables -> Actions**:

- `DATABASE_URL`
- `SCRAPER_API_KEY`
- `GEMINI_API_KEY`

Mapping used in workflow:

- `DATABASE_URL` -> `secrets.DATABASE_URL`
- `scraperapikey` -> `secrets.SCRAPER_API_KEY`
- `Geminiapikey` -> `secrets.GEMINI_API_KEY`

## Output and Artifacts

Typical output locations:

- FAST: `FAST University/output/fast_admissions.json`
- GIKI: `GIKI/output/giki/` and `GIKI/output/giki_admissions_latest.json`
- IBA Karachi: `IBA Karachi/output/iba_karachi_admissions.json`
- IBA Sukkur: `IBASukkur/output/`
- MAJU: `Muhammd  Ali Jinnah/output/`
- NUTECH: `NUTECH/output/`

CI artifacts include:

- `runner.log`
- `**/logs/**`
- `**/output/**`
- `**/*admissions*.json`


## Troubleshooting

### CI fails with parser error about lxml

- Ensure root `requirements.txt` contains `lxml`.
- Re-run workflow after dependency update.

### Scraper works locally but fails in CI

- Verify GitHub Secrets are present and non-empty.
- Check `runner.log` artifact for exact failing scraper.
- Confirm target website availability/rate limits.

### Node.js deprecation warning in Actions

- Warning is from action runtime migration notices.
- Workflow can still succeed while warning is present.

## Security Guidelines

- Never commit `.env` or plaintext credentials.
- Rotate any key immediately if exposed.
- Keep DB credentials and API keys in GitHub Secrets for CI.

## License

No license file is currently defined for now!
