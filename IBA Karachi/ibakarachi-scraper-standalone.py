import os
import sys
import json
import logging
from dateutil import parser
from dotenv import load_dotenv
from datetime import datetime, date
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# Add parent directory to path to import db module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.insert_admissioin import insert_admission, normalize_admission_record

load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

# ==============================
# LOGGING SETUP
# ==============================

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==============================
# CONFIG
# ==============================

IBA_URL = "https://admissions.iba.edu.pk/admission-schedule-fall2026.php"
UNIVERSITY_NAME = "IBA Karachi"

# Program name expansions
PROGRAM_EXPANSIONS = {
    "BBA": "BBA (Bachelor of Business Administration)",
    "BSACF": "BSACF (BS Accounting & Finance)",
    "BSBA (Business Analytics)": "BSBA (BS Business Analytics)",
    "BS (CS / Math)": "BS (Computer Science / Mathematics)",
    "BSECO": "BSECO (BS Economics)",
    "BSEM": "BSEM (BS Econometrics & Mathematical Economics)",
    "BSEDS": "BSEDS (BS Economics & Data Science)",
    "BSSS": "BSSS (BS Social Sciences)"
}

# ==============================
# SELENIUM SETUP
# ==============================

def setup_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=options)

def load_page_html(driver, url):
    driver.get(url)
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )
    return driver.page_source

# ==============================
# SCRAPING LOGIC
# ==============================

def normalize_text(text):
    """Normalize text by removing extra whitespace, newlines, and tabs"""
    if not text:
        return text
    # Replace multiple whitespace (including newlines and tabs) with single space
    import re
    normalized = re.sub(r'\s+', ' ', text)
    return normalized.strip()

def get_cell_text(cell):
    """Safely extract deeply nested text (td > p > span)."""
    text = " ".join(cell.stripped_strings)
    return normalize_text(text)

def expand_header_row(cells):
    """Expand header cells according to colspan."""
    expanded = []
    for cell in cells:
        colspan = int(cell.get("colspan", 1))
        text = get_cell_text(cell)
        expanded.extend([text] * colspan)
    return expanded


def parse_first_date_from_stage(dates_info, include_keywords):
    """Return earliest parsed date from matching stages."""
    parsed_dates = []
    for stage, date_list in dates_info.items():
        stage_l = stage.lower()
        if any(keyword in stage_l for keyword in include_keywords):
            for date_str in date_list:
                try:
                    parsed_dates.append(parser.parse(date_str).date())
                except Exception:
                    continue
    return min(parsed_dates) if parsed_dates else None


def parse_last_date_from_stage(dates_info, include_keywords):
    """Return latest parsed date from matching stages."""
    parsed_dates = []
    for stage, date_list in dates_info.items():
        stage_l = stage.lower()
        if any(keyword in stage_l for keyword in include_keywords):
            for date_str in date_list:
                try:
                    parsed_dates.append(parser.parse(date_str).date())
                except Exception:
                    continue
    return max(parsed_dates) if parsed_dates else None

def parse_round_table(table, round_name):
    """
    Parse a single round table (Round 1 or Round 2)
    and return ONLY undergraduate admissions with dates and programs.
    """
    rows = table.find_all("tr")
    if not rows:
        return {"round": round_name, "programs": [], "dates": {}}

    # Find the level row dynamically (contains Undergraduate/Postgraduate markers)
    level_row_idx = None
    level_row = []
    for idx, row in enumerate(rows):
        cells = row.find_all(["td", "th"])
        expanded = expand_header_row(cells)
        joined = " ".join([c.lower() for c in expanded if c])
        if "undergraduate" in joined:
            level_row_idx = idx
            level_row = expanded
            break

    if level_row_idx is None:
        logger.warning(f"Could not locate level row for {round_name}")
        return {"round": round_name, "programs": [], "dates": {}}

    # Find the program row dynamically (first row after level row with BS/BBA labels)
    program_row_idx = None
    program_row = []
    for idx in range(level_row_idx + 1, len(rows)):
        cells = rows[idx].find_all(["td", "th"])
        expanded = expand_header_row(cells)
        if any(
            text and (
                text.strip().upper().startswith("BS") or
                text.strip().upper().startswith("BBA")
            )
            for text in expanded
        ):
            program_row_idx = idx
            program_row = expanded
            break

    if program_row_idx is None:
        logger.warning(f"Could not locate program row for {round_name}")
        return {"round": round_name, "programs": [], "dates": {}}

    # Identify undergraduate column indices
    ug_columns = [
        i for i, level in enumerate(level_row)
        if level and "undergraduate" in level.lower()
    ]

    # Extract all programs for undergraduate columns
    programs = []
    for idx in ug_columns:
        if idx < len(program_row):
            program_text = program_row[idx]
            if program_text and program_text not in ["-", "N/A"]:
                # Split multiple programs (e.g., "BBA, BSACF & BSBA")
                prog_list = [p.strip() for p in program_text.replace("&", ",").split(",")]
                programs.extend([p for p in prog_list if p])

    # Remove duplicates while preserving order
    programs = list(dict.fromkeys(programs))
    
    # Expand program names to include full forms
    programs = [PROGRAM_EXPANSIONS.get(p, p) for p in programs]

    # Extract dates from DATA ROWS (rows after program row)
    dates_info = {}
    for row in rows[program_row_idx + 1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        stage = get_cell_text(cells[0])
        date_cells = cells[1:]

        for idx in ug_columns:
            if idx >= len(date_cells):
                continue

            date_value = get_cell_text(date_cells[idx])
            if not date_value or date_value.lower() in ["-", "n/a"]:
                continue

            # Store dates by stage
            if stage not in dates_info:
                dates_info[stage] = []
            dates_info[stage].append(date_value)

    return {
        "round": round_name,
        "programs": programs,
        "dates": dates_info
    }

def scrape_raw_undergraduate_data(html):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("div#main table.w3-table.w3-striped")

    results = []
    if len(tables) >= 1:
        results.append(parse_round_table(tables[0], "Round 1"))
    if len(tables) >= 2:
        results.append(parse_round_table(tables[1], "Round 2"))
    return results

# ==============================
# DETERMINE ACTIVE ROUND
# ==============================

def determine_active_round(rounds_data):
    """
    Determine which round is currently in the opportunity window
    based on today's date.
    """
    today = datetime.today().date()
    
    rounds_with_windows = []

    for round_info in rounds_data:
        dates_info = round_info["dates"]

        forms_start = parse_first_date_from_stage(
            dates_info,
            ["online forms", "availability", "application start", "applications open", "forms open"]
        )
        form_deadline = parse_last_date_from_stage(
            dates_info,
            ["form submission", "deadline", "last date", "application deadline", "close"]
        )

        rounds_with_windows.append((round_info, forms_start, form_deadline))

    # 1) Prefer currently active round window
    for round_info, forms_start, form_deadline in rounds_with_windows:
        if forms_start and form_deadline:
            if forms_start <= today <= form_deadline:
                logger.info(f"✓ Active round: {round_info['round']} (Window: {forms_start} to {form_deadline})")
                return round_info, forms_start, form_deadline

    # 2) If none active, pick the nearest upcoming round by start date
    upcoming = [
        (round_info, forms_start, form_deadline)
        for round_info, forms_start, form_deadline in rounds_with_windows
        if forms_start and forms_start >= today
    ]
    if upcoming:
        upcoming.sort(key=lambda item: item[1])
        round_info, forms_start, form_deadline = upcoming[0]
        logger.info(f"✓ Upcoming round selected: {round_info['round']} (Starts: {forms_start})")
        return round_info, forms_start, form_deadline

    # 3) Fallback: if no upcoming start exists, choose round with latest known deadline
    with_deadlines = [
        (round_info, forms_start, form_deadline)
        for round_info, forms_start, form_deadline in rounds_with_windows
        if form_deadline
    ]
    if with_deadlines:
        with_deadlines.sort(key=lambda item: item[2], reverse=True)
        round_info, forms_start, form_deadline = with_deadlines[0]
        logger.warning(f"No active/upcoming start date found. Using latest deadline round: {round_info['round']}")
        return round_info, forms_start, form_deadline

    # 4) Final fallback
    logger.warning("Could not derive round window; falling back to first parsed round")
    return rounds_data[0], None, None

# ==============================
# BUILD OUTPUT
# ==============================

def build_output_json(round_info, publish_date, last_date):
    """Build standardized output matching NUTECH format"""
    return {
        "university": UNIVERSITY_NAME,
        "program_title": f"Fall 2026 Undergraduate Admissions - {round_info['round']}",
        "publish_date": publish_date.strftime("%Y-%m-%d") if publish_date else None,
        "last_date": last_date.strftime("%Y-%m-%d") if last_date else None,
        "details_link": IBA_URL,
        "programs_offered": round_info["programs"]
    }

# ==============================
# DATA VALIDATION
# ==============================

def validate_scraped_data(data):
    """Validate scraped data quality"""
    issues = []
    
    if not data.get("last_date"):
        issues.append("Missing application deadline")
    
    if not data.get("programs_offered"):
        issues.append("No programs found")
    
    if issues:
        logger.warning(f"Data validation issues: {', '.join(issues)}")
    else:
        logger.info("✓ Data validation passed")
    
    return len(issues) == 0, issues

# ==============================
# DATA PERSISTENCE
# ==============================

def insert_to_database(data):
    """Insert data into PostgreSQL database"""
    try:
        if isinstance(data, list) and len(data) > 0:
            record = data[0]
            logger.info("Inserting data into database...")
            insert_admission(record)
            logger.info("✓ Data successfully inserted into database")
            return True
        else:
            logger.error("Invalid data format for database insertion")
            return False
    except Exception as e:
        logger.error(f"Failed to insert data into database: {e}")
        return False

def save_to_json(data, filename="iba_karachi_admissions.json"):
    """Save data to JSON file (backup only)"""
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = os.path.join(output_dir, filename)
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump([data], f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ Backup data saved to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save backup data: {e}")
        raise

# ==============================
# MAIN PIPELINE
# ==============================

def scrape_iba_karachi():
    logger.info("="*60)
    logger.info("IBA Karachi Admission Scraper - Standalone Version")
    logger.info("="*60)
    
    driver = setup_driver()
    try:
        # Step 1: Load page
        logger.info("Loading admission schedule page...")
        html = load_page_html(driver, IBA_URL)
        
        # Step 2: Scrape all rounds
        logger.info("Extracting admission data...")
        rounds_data = scrape_raw_undergraduate_data(html)
        
        if not rounds_data:
            logger.error("No admission data found")
            return None
        
        logger.info(f"✓ Found {len(rounds_data)} admission rounds")
        
        # Step 3: Determine active round
        active_round, publish_date, last_date = determine_active_round(rounds_data)
        
        # Step 4: Build output
        output_data = normalize_admission_record(build_output_json(active_round, publish_date, last_date))
        
        logger.info(f"Programs found: {len(output_data['programs_offered'])}")
        logger.info(f"Last date: {output_data.get('last_date', 'N/A')}")
        
        # Step 5: Validate data
        is_valid, issues = validate_scraped_data(output_data)
        if not is_valid:
            logger.warning(f"Data quality issues detected: {issues}")
        
        # Step 6: Save backup to file (always runs)
        save_to_json(output_data)
        
        # Step 7: Insert into database (non-fatal if fails)
        insert_to_database([output_data])
        
        # Step 8: Display output
        logger.info("="*60)
        logger.info("FINAL OUTPUT:")
        logger.info("="*60)
        print(json.dumps([output_data], indent=2))
        logger.info("="*60)
        
        return output_data
        
    finally:
        driver.quit()
        logger.info("WebDriver closed")


if __name__ == "__main__":
    try:
        scrape_iba_karachi()
    except KeyboardInterrupt:
        logger.info("Scraper interrupted by user")
    except Exception as e:
        logger.critical(f"Scraper failed: {e}", exc_info=True)
        exit(1)
