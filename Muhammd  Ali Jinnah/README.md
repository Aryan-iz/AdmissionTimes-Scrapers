# Muhammad Ali Jinnah University Scraper

A production-ready web scraper for extracting admission dates and program information from Muhammad Ali Jinnah University (MAJU) website.

## Features

✅ **Production-Ready**
- Comprehensive logging system with file rotation
- Retry mechanism with exponential backoff
- Robust error handling with custom exceptions
- Data validation and quality checks
- Atomic file writes for data persistence

✅ **Smart Features**
- Automatic semester detection based on current date
- Dynamic configuration management
- AI-powered data cleaning and validation
- Timestamped output files
- Performance metrics tracking

✅ **Reliability**
- Handles network failures gracefully
- Retries failed requests automatically
- Continues operation even if AI analysis fails
- Detailed logging for debugging

## Installation

### Prerequisites
- Python 3.8 or higher
- Google Chrome browser
- ChromeDriver (automatically managed by Selenium)

### Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure environment variables:**

Create a `.env` file in the same directory:
```env
scraperapikey=your_openrouter_api_key_here
```

> **Note:** The AI analysis feature is optional. The scraper will work without the API key, but won't perform AI-based data cleaning.

## Usage

### Basic Usage

Run the production scraper:
```bash
python muhammadalijinnah-scrapper-production.py
```

Run the original scraper (for comparison):
```bash
python muhammadalijinnah-scrapper.py
```

### Output

The scraper creates two types of output:

1. **JSON Files** (in `output/` directory):
   - Timestamped files: `maju_admissions_YYYYMMDD_HHMMSS.json`
   - Contains structured admission data

2. **Log Files** (in `logs/` directory):
   - Daily log files: `scraper_YYYYMMDD.log`
   - Detailed execution logs with timestamps

### Sample Output

```json
[
  {
    "university": "Muhammad Ali Jinnah University",
    "program_title": "Spring 2026 Undergraduate Admissions",
    "publish_date": null,
    "last_date": "Saturday, February 14, 2026",
    "details_link": "https://jinnah.edu/key-admission-dates/",
    "ai_analysis": {
      "university": "MAJU Karachi",
      "programs_offered": [
        "BBA",
        "BS FinTech",
        "BS Psychology",
        "BS Software Engineering",
        "BS Biotechnology",
        "BS Business Analytics"
      ],
      "ai_comments": "Perfect"
    }
  }
]
```

## Configuration

Edit `config.py` to customize:

- **Timeouts**: Selenium wait times, page load timeouts
- **Retry Settings**: Max attempts, delay, backoff factor
- **Logging**: Log level, file size limits
- **Paths**: Output and log directories
- **AI Settings**: Model selection, API timeout

## File Structure

```
Muhammd  Ali Jinnah/
├── muhammadalijinnah-scrapper.py          # Original scraper
├── muhammadalijinnah-scrapper-production.py  # Production version
├── config.py                               # Configuration settings
├── requirements.txt                        # Python dependencies
├── .env                                    # Environment variables (create this)
├── logs/                                   # Log files (auto-created)
│   └── scraper_YYYYMMDD.log
└── output/                                 # JSON output files (auto-created)
    └── maju_admissions_YYYYMMDD_HHMMSS.json
```

## Differences: Original vs Production

| Feature | Original | Production |
|---------|----------|------------|
| Logging | Print statements | File + console logging with rotation |
| Error Handling | Bare except clauses | Specific exceptions with context |
| Retry Logic | ❌ None | ✅ 3 attempts with exponential backoff |
| Data Persistence | ❌ Console only | ✅ JSON files with timestamps |
| Configuration | ❌ Hardcoded | ✅ Centralized config file |
| Validation | ❌ None | ✅ Data quality checks |
| Semester Detection | ❌ Hardcoded | ✅ Auto-detected |
| Performance Metrics | ❌ None | ✅ Execution time tracking |

## Troubleshooting

### ChromeDriver Issues
If you encounter ChromeDriver errors:
```bash
# Update Selenium (it auto-manages ChromeDriver)
pip install --upgrade selenium
```

### Missing .env File
The scraper will work without `.env`, but AI analysis will be skipped:
```
⚠️ Warning: .env file not found
⚠️ AI API key not found. Skipping AI analysis.
```

### Network Timeouts
If scraping fails due to timeouts, increase timeout values in `config.py`:
```python
SELENIUM_TIMEOUT = 20  # Increase from 15
PAGE_LOAD_TIMEOUT = 45  # Increase from 30
```

### Empty Programs List
If no programs are found, check if the website structure changed. Update the CSS selector in the scraper:
```python
# Current selector
soup.select("a.icon-box-link")
```

## Deployment

### Production Checklist

- [x] Install all dependencies from `requirements.txt`
- [x] Configure `.env` file with API key
- [x] Test scraper locally
- [x] Set up log rotation (already configured)
- [x] Configure monitoring for log files
- [ ] Set up scheduled execution (cron job/Task Scheduler)
- [ ] Configure alerts for failures
- [ ] Set up backup for output files

### Scheduled Execution

**Linux/Mac (cron):**
```bash
# Run daily at 9 AM
0 9 * * * cd /path/to/scraper && python muhammadalijinnah-scrapper-production.py
```

**Windows (Task Scheduler):**
1. Open Task Scheduler
2. Create Basic Task
3. Set trigger (e.g., daily at 9 AM)
4. Action: Start a program
5. Program: `python`
6. Arguments: `muhammadalijinnah-scrapper-production.py`
7. Start in: `E:\FYP-WEBSCRAPPERS\admitly-scraper\Muhammd  Ali Jinnah`

## Monitoring

Check logs regularly:
```bash
# View latest log
tail -f logs/scraper_YYYYMMDD.log

# Search for errors
grep "ERROR" logs/*.log
```

## License

This scraper is for educational/research purposes. Ensure compliance with the university's terms of service and robots.txt.

## Support

For issues or questions, check the log files first. They contain detailed information about what went wrong.
