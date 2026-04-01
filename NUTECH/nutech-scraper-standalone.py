"""
NUTECH Undergraduate Admissions Scraper - Standalone Production Version
All dependencies consolidated into a single file
Matches MAJU scraper structure with comprehensive logging and standardized output
"""

import os
import sys
import re
import json
import time
import logging
import requests
from datetime import datetime
from typing import List, Dict, Optional
from functools import wraps
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# Add parent directory to path to import db module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.insert_admissioin import insert_admission, normalize_admission_record

# ==============================
# CONFIGURATION
# ==============================
class Config:
    """Configuration settings for the scraper"""
    
    # Paths
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
    OUTPUT_DIR = os.path.join(BASE_DIR, "output")
    ENV_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", ".env"))
    
    # University Information
    UNIVERSITY_NAME = "National University of Technology (NUTECH), Islamabad"
    UNIVERSITY_SHORT_NAME = "NUTECH Islamabad"
    ADMISSIONS_URL = "https://nutech.edu.pk/admissions/"
    
    # Selenium Settings
    SELENIUM_TIMEOUT = 15  # seconds
    PAGE_LOAD_TIMEOUT = 60  # seconds
    IMPLICIT_WAIT = 5  # seconds
    SCROLL_PAUSE = 0.7  # seconds between scrolls
    
    # Retry Settings
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY = 2  # seconds
    RETRY_BACKOFF_FACTOR = 2  # multiplier for exponential backoff
    
    # AI Settings
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    AI_MODEL = "google/gemini-2.0-flash-001"
    AI_TIMEOUT = 30  # seconds
    
    # Logging Settings
    LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    LOG_FILE_BACKUP_COUNT = 5
    
    @staticmethod
    def get_output_filename():
        """Generate timestamped output filename"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(Config.OUTPUT_DIR, f"nutech_admissions_{timestamp}.json")
    
    @staticmethod
    def get_log_filename():
        """Generate dated log filename"""
        date_str = datetime.now().strftime("%Y%m%d")
        return os.path.join(Config.LOGS_DIR, f"scraper_{date_str}.log")
    
    @staticmethod
    def ensure_directories():
        """Create necessary directories if they don't exist"""
        os.makedirs(Config.LOGS_DIR, exist_ok=True)
        os.makedirs(Config.OUTPUT_DIR, exist_ok=True)

# Initialize directories
Config.ensure_directories()

# ==============================
# CUSTOM EXCEPTIONS
# ==============================
class ScraperException(Exception):
    """Base exception for scraper errors"""
    pass

class DataExtractionError(ScraperException):
    """Raised when data extraction fails"""
    pass

class AIAnalysisError(ScraperException):
    """Raised when AI analysis fails"""
    pass

# ==============================
# LOGGING SETUP
# ==============================
def setup_logging():
    """Configure logging with file and console handlers"""
    logger = logging.getLogger("NUTECH_Scraper")
    logger.setLevel(getattr(logging, Config.LOG_LEVEL))
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        Config.get_log_filename(),
        maxBytes=Config.LOG_FILE_MAX_BYTES,
        backupCount=Config.LOG_FILE_BACKUP_COUNT
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(Config.LOG_FORMAT)
    file_handler.setFormatter(file_formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ==============================
# ENVIRONMENT VARIABLES
# ==============================
def load_env_variables():
    """Load environment variables from .env file"""
    if not os.path.exists(Config.ENV_FILE):
        logger.warning(f".env file not found at {Config.ENV_FILE}")
        return False
    
    try:
        with open(Config.ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
        logger.info("[OK] Environment variables loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to load .env file: {e}")
        return False

# ==============================
# RETRY DECORATOR
# ==============================
def retry_on_failure(max_attempts=None, delay=None, backoff=None):
    """Decorator to retry function on failure with exponential backoff"""
    if max_attempts is None:
        max_attempts = Config.MAX_RETRY_ATTEMPTS
    if delay is None:
        delay = Config.RETRY_DELAY
    if backoff is None:
        backoff = Config.RETRY_BACKOFF_FACTOR
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 1
            current_delay = delay
            
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    
                    logger.warning(f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. Retrying in {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
                    attempt += 1
            
        return wrapper
    return decorator

# ==============================
# SEMESTER DETECTION
# ==============================
def detect_current_semester():
    """Detect current semester based on current date"""
    now = datetime.now()
    year = now.year
    month = now.month
    
    # Spring semester: January - June
    # Fall semester: July - December
    # Use the current calendar year directly.
    if 1 <= month <= 6:
        semester = "Spring"
    else:
        semester = "Fall"
    
    return f"{semester} {year}"

# ==============================
# SELENIUM SETUP
# ==============================
def setup_driver():
    """Configure and return Chrome WebDriver"""
    logger.info("Setting up Chrome WebDriver...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(Config.PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(Config.IMPLICIT_WAIT)
        logger.info("[OK] WebDriver initialized successfully")
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {e}")
        raise

# ==============================
# UTILITY FUNCTIONS
# ==============================
def scroll_to_bottom(driver, pause=None):
    """Scroll to bottom of page to load all content"""
    if pause is None:
        pause = Config.SCROLL_PAUSE
    
    logger.debug("Scrolling to bottom of page...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    logger.debug("Finished scrolling")

def wait_for_modal_and_close(driver, wait_timeout=10):
    """Handle and close any modal dialogs"""
    try:
        wait = WebDriverWait(driver, wait_timeout)
        modal = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".modal-content, #myModal, div[role='dialog']"))
        )
        if modal:
            logger.info("Modal detected. Attempting to close it...")
            close_btn = driver.find_elements(By.CSS_SELECTOR, ".close, [data-dismiss='modal'], button.close")
            if close_btn:
                driver.execute_script("arguments[0].click();", close_btn[0])
                time.sleep(1)
                logger.info("Modal closed successfully")
    except TimeoutException:
        logger.debug("No modal detected")
    except Exception as e:
        logger.warning(f"Error while handling modal: {e}")

# ==============================
# DATE PARSING FUNCTIONS
# ==============================
def parse_date_range(date_str: str, year: Optional[int] = None) -> Optional[datetime]:
    """Parse date strings like '19 Sep', '19 Sep - 29 Dec', etc."""
    if not date_str or not isinstance(date_str, str):
        return None
    
    # Extract just the first date if it's a range
    date_part = date_str.strip().split('-')[0].strip().split('–')[0].strip()
    
    if not year:
        year = datetime.now().year
    
    for fmt in (f"{year} %d %b", "%d %b %Y", "%d %B %Y", "%d %b", "%d %B"):
        try:
            if "%Y" not in fmt and year:
                d = datetime.strptime(f"{year} {date_part}", f"%Y {fmt}")
            else:
                d = datetime.strptime(date_part.strip(), fmt)
            if d.year == 1900:
                d = d.replace(year=year)
            return d
        except ValueError:
            continue
    
    return None

def extract_dates_from_text(text):
    """Extract possible date patterns and convert to datetime objects."""
    pattern = r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)'
    matches = re.findall(pattern, text, re.I)
    dates = []
    for m in matches:
        date_str = m.strip()
        for fmt in ("%d %b %Y", "%d %B %Y", "%d %b", "%d %B"):
            try:
                d = datetime.strptime(date_str, fmt)
                if d.year == 1900:
                    d = d.replace(year=datetime.now().year)
                dates.append(d)
                break
            except Exception:
                continue
    return dates

# ==============================
# DATA EXTRACTION FUNCTIONS
# ==============================
@retry_on_failure()
def extract_programs(soup: BeautifulSoup) -> List[str]:
    """Extract undergraduate programs from the page"""
    logger.info("Extracting programs...")
    programs = set()
    prefix_re = re.compile(r'^\s*(BS|BE|BET|B\.S\.|B\.Sc|BSc)\b', re.I)
    
    for li in soup.find_all("li"):
        text = li.get_text(separator=" ", strip=True)
        if prefix_re.match(text):
            # Filter out concatenated strings (longer than 100 chars)
            if len(text) < 100:
                programs.add(text)
    
    for p in soup.find_all(["p", "div"]):
        lines = [line.strip() for line in p.get_text(separator=" ", strip=True).splitlines() if line.strip()]
        for line in lines:
            if prefix_re.match(line):
                # Filter out concatenated strings
                if len(line) < 100:
                    programs.add(line)
    
    programs_list = sorted(programs)
    logger.info(f"[OK] Found {len(programs_list)} unique programs")
    return programs_list

def is_within_opportunity_window(registration_start: datetime, registration_end: datetime, 
                                  current_date: Optional[datetime] = None) -> bool:
    """Check if current date falls within the registration window."""
    if current_date is None:
        current_date = datetime.now()
    
    # Only include if registration window includes today
    return registration_start.date() <= current_date.date() <= registration_end.date()

@retry_on_failure()
def extract_admission_schedule(soup: BeautifulSoup) -> List[Dict]:
    """Extract admission schedule from table and filter by opportunity window."""
    logger.info("Extracting admission schedule (opportunity window only)...")
    current_date = datetime.now()
    schedule_data = []
    
    # Find the table containing admission schedule
    tables = soup.find_all("table")
    
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        
        # Check if this is the admission schedule table
        headers = [h.get_text(strip=True).lower() for h in rows[0].find_all(["th", "td"])]
        if not any("registration" in h or "schedule" in h or "details" in h for h in headers):
            continue
        
        # Process rows
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            
            # Parse row data
            try:
                batch_info = cells[1] if len(cells) > 1 else ""
                registration_period = cells[2] if len(cells) > 2 else ""
                schedule = cells[3] if len(cells) > 3 else ""
                center = cells[4] if len(cells) > 4 else ""
                
                if not registration_period or not batch_info:
                    continue
                
                # Parse registration dates (format: "9 Jan – 23 Feb")
                reg_dates = re.split(r'[-–—]', registration_period)
                if len(reg_dates) != 2:
                    continue
                
                reg_start_str = reg_dates[0].strip()
                reg_end_str = reg_dates[1].strip()
                
                # Parse dates
                reg_start = parse_date_range(reg_start_str, current_date.year)
                reg_end = parse_date_range(reg_end_str, current_date.year)
                
                if not reg_start or not reg_end:
                    continue
                
                # Check if this is within the opportunity window
                if is_within_opportunity_window(reg_start, reg_end, current_date):
                    entry = {
                        "batch": batch_info,
                        "registration_start": reg_start.strftime("%Y-%m-%d"),
                        "registration_end": reg_end.strftime("%Y-%m-%d"),
                        "test_schedule": schedule,
                        "center": center
                    }
                    schedule_data.append(entry)
                    logger.info(f"[OK] Added {batch_info} (within opportunity window)")
                else:
                    logger.debug(f"✗ Skipped {batch_info} (outside opportunity window)")
            
            except Exception as e:
                logger.debug(f"Error parsing row: {e}")
                continue
    
    logger.info(f"[OK] Found {len(schedule_data)} active admission windows")
    return schedule_data

def extract_section_text(soup: BeautifulSoup, heading_keyword: str) -> Optional[str]:
    """Extract text from a section identified by heading keyword"""
    heading_tags = soup.find_all(re.compile("^h[1-4]$"))
    keyword_lower = heading_keyword.lower()
    for h in heading_tags:
        if keyword_lower in h.get_text(separator=" ", strip=True).lower():
            parts = []
            for sib in h.find_next_siblings():
                if sib.name and re.match("^h[1-4]$", sib.name, re.I):
                    break
                parts.append(sib.get_text(separator=" ", strip=True))
            return " ".join(parts).strip()
    return None

@retry_on_failure()
def scrape_nutech_data(driver):
    """Scrape all data from NUTECH admissions page"""
    logger.info(f"Navigating to {Config.ADMISSIONS_URL}")
    
    try:
        driver.get(Config.ADMISSIONS_URL)
        WebDriverWait(driver, Config.SELENIUM_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
        )
        
        # Handle any modals
        wait_for_modal_and_close(driver)
        
        # Scroll to load all content
        scroll_to_bottom(driver)
        
        # Parse page
        soup = BeautifulSoup(driver.page_source, "lxml")
        
        # Extract programs
        programs = extract_programs(soup)
        
        # Extract admission schedule
        admission_schedule = extract_admission_schedule(soup)
        
        # Extract dates
        full_text = soup.get_text(separator=" ", strip=True)

        # Determine publish date from the admission schedule first.
        # For this scraper, publish_date should represent opening/registration start.
        upload_date = None
        if admission_schedule:
            start_dates = []
            for entry in admission_schedule:
                try:
                    start_dates.append(datetime.strptime(entry["registration_start"], "%Y-%m-%d"))
                except Exception:
                    continue
            if start_dates:
                upload_date = min(start_dates).strftime("%Y-%m-%d")

        # Fallback to page-level published/updated date only when schedule dates are unavailable.
        if not upload_date:
            upload_match = re.search(r"(Updated|Published|Posted)\s*on[:\-]?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s*\d{2,4})", full_text, re.I)
            if upload_match:
                raw_date = upload_match.group(2).strip()
                parsed = None
                for fmt in ("%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y"):
                    try:
                        parsed = datetime.strptime(raw_date, fmt)
                        break
                    except Exception:
                        continue
                upload_date = parsed.strftime("%Y-%m-%d") if parsed else raw_date
        
        # Extract last date from opportunity window
        last_date = None
        if admission_schedule:
            latest_date = None
            for entry in admission_schedule:
                try:
                    end_date = datetime.strptime(entry["registration_end"], "%Y-%m-%d")
                    if latest_date is None or end_date > latest_date:
                        latest_date = end_date
                except Exception:
                    continue
            if latest_date:
                last_date = latest_date.strftime("%Y-%m-%d")
        
        if not last_date:
            schedule_text = extract_section_text(soup, "Admission Schedule") or full_text
            schedule_dates = extract_dates_from_text(schedule_text)
            last_date = max(schedule_dates).strftime("%Y-%m-%d") if schedule_dates else None
        
        # Validation warnings
        if not upload_date:
            logger.warning("Publish date not found on page")
        if not last_date:
            logger.warning("Last date not found on page")
        if not programs:
            logger.warning("No programs found on page")
        
        logger.info(f"[OK] Data extraction completed - Programs: {len(programs)}, Last date: {last_date}")
        
        return {
            "programs": programs,
            "publish_date": upload_date,
            "last_date": last_date,
            "admission_schedule": admission_schedule
        }
        
    except TimeoutException:
        logger.error("Timeout waiting for page to load")
        raise DataExtractionError("Timeout loading admissions page")
    except Exception as e:
        logger.error(f"Error scraping data: {e}")
        raise DataExtractionError(f"Failed to scrape data: {e}")

# ==============================
# AI ANALYSIS
# ==============================
@retry_on_failure(max_attempts=2)
def analyze_with_ai(scraped_data):
    """Send data to AI for cleaning and validation"""
    api_key = os.environ.get("scraperapikey")
    
    if not api_key:
        logger.warning("AI API key not found. Skipping AI analysis.")
        raise AIAnalysisError("API key not configured")
    
    logger.info("Sending data to AI for analysis...")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        "Process this university admission JSON data: "
        "1. Clean and validate the data. "
        "2. Add a field 'ai_comments' inside 'ai_analysis' summarizing the status. "
        "3. Do NOT add 'announcement' or 'programs' fields to the output. "
        "4. Output ONLY valid JSON in the requested list structure. "
        f"Data: {json.dumps(scraped_data)}"
    )
    
    payload = {
        "model": Config.AI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a data cleaner. Output valid JSON list only."},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = requests.post(
            Config.OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=Config.AI_TIMEOUT
        )
        response.raise_for_status()
        
        ai_content = response.json()['choices'][0]['message']['content']
        
        # Strip markdown code blocks if present
        if "```json" in ai_content:
            ai_content = ai_content.split("```json")[1].split("```")[0].strip()
        elif "```" in ai_content:
            ai_content = ai_content.split("```")[1].split("```")[0].strip()
        
        cleaned_data = json.loads(ai_content)
        
        # Remove unwanted fields if AI added them
        if isinstance(cleaned_data, list):
            for item in cleaned_data:
                item.pop("announcement", None)
                item.pop("programs", None)
        
        logger.info("[OK] AI analysis completed successfully")
        return cleaned_data
        
    except requests.exceptions.Timeout:
        logger.error("AI API request timed out")
        raise AIAnalysisError("AI API timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"AI API request failed: {e}")
        raise AIAnalysisError(f"AI API error: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse AI response: {e}")
        raise AIAnalysisError(f"Invalid AI response: {e}")

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
        logger.info("[OK] Data validation passed")
    
    return len(issues) == 0, issues

# ==============================
# DATA PERSISTENCE
# ==============================
def insert_to_database(data):
    """Insert data into PostgreSQL database"""
    try:
        # Data should be a list with a single record
        if isinstance(data, list) and len(data) > 0:
            record = data[0]
            logger.info("Inserting data into database...")
            insert_admission(record)
            logger.info("[OK] Data successfully inserted into database")
            return True
        else:
            logger.error("Invalid data format for database insertion")
            return False
    except Exception as e:
        logger.error(f"Failed to insert data into database: {e}")
        raise

def save_to_json(data, filename=None):
    """Save data to JSON file with atomic write (backup only)"""
    if filename is None:
        filename = Config.get_output_filename()
    
    try:
        # Write to temporary file first
        temp_filename = filename + ".tmp"
        with open(temp_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Atomic rename
        if os.path.exists(filename):
            os.remove(filename)
        os.rename(temp_filename, filename)
        
        logger.info(f"[OK] Backup data saved to {filename}")
        return filename
        
    except Exception as e:
        logger.error(f"Failed to save backup data: {e}")
        raise

# ==============================
# MAIN SCRAPER
# ==============================
def run_scraper():
    """Main scraper execution function"""
    start_time = time.time()
    logger.info("="*60)
    logger.info("NUTECH Admission Scraper - Standalone Version")
    logger.info("="*60)
    
    # Load environment variables
    load_env_variables()
    
    driver = None
    try:
        # Setup WebDriver
        driver = setup_driver()
        
        # Scrape data
        logger.info("Starting data extraction...")
        scraped_data = scrape_nutech_data(driver)
        
        # Detect semester
        semester = detect_current_semester()
        logger.info(f"Detected semester: {semester}")
        
        # Structure raw data with flattened format
        raw_data = [{
            "university": Config.UNIVERSITY_NAME,
            "program_title": f"{semester} Undergraduate Admissions",
            "publish_date": scraped_data.get("publish_date"),
            "last_date": scraped_data.get("last_date"),
            "details_link": Config.ADMISSIONS_URL,
            "programs_offered": scraped_data.get("programs", [])
        }]
        raw_data = [normalize_admission_record(raw_data[0])]
        
        # Validate data
        is_valid, issues = validate_scraped_data(raw_data[0])
        if not is_valid:
            logger.warning(f"Data quality issues detected: {issues}")
        
        # Use raw data as final data (no AI processing needed for simple format)
        final_data = raw_data
        
        # Insert into database
        insert_to_database(final_data)
        
        # Also save backup to file
        output_file = save_to_json(final_data)
        
        # Print summary
        execution_time = time.time() - start_time
        logger.info("="*60)
        logger.info("SCRAPING COMPLETED SUCCESSFULLY")
        logger.info(f"Execution time: {execution_time:.2f} seconds")
        logger.info(f"Backup file: {output_file}")
        logger.info(f"Programs found: {len(scraped_data.get('programs', []))}")
        logger.info(f"Last date: {scraped_data.get('last_date', 'N/A')}")
        logger.info("="*60)
        
        # Print final output to console
        print("\n--- FINAL OUTPUT ---")
        print(json.dumps(final_data, indent=2))
        
        return final_data
        
    except ScraperException as e:
        logger.error(f"Scraper error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise
    finally:
        if driver:
            driver.quit()
            logger.info("WebDriver closed")

# ==============================
# ENTRY POINT
# ==============================
if __name__ == "__main__":
    try:
        run_scraper()
    except KeyboardInterrupt:
        logger.info("Scraper interrupted by user")
    except Exception as e:
        logger.critical(f"Scraper failed: {e}")
        exit(1)
