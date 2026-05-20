import os
import sys
import re
import json
import time
import logging
import requests
from datetime import datetime
from typing import Optional, Dict, Any
from functools import wraps
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup
from urllib.parse import urljoin

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
    UNIVERSITY_NAME = "IBA Sukkur"
    UNIVERSITY_SHORT_NAME = "IBA Sukkur"
    BASE_URL = "https://www.iba-suk.edu.pk"
    ADMISSION_URL = f"{BASE_URL}/admissions/announcements"
    
    # Request Settings
    REQUEST_TIMEOUT = 30  # seconds
    MAX_PAGES = 12  # Maximum pages to search for admissions
    
    # Retry Settings
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY = 2  # seconds
    RETRY_BACKOFF_FACTOR = 2  # multiplier for exponential backoff
    
    # Logging Settings
    LOG_LEVEL = "INFO" 
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    LOG_FILE_BACKUP_COUNT = 5

    # IBA programs
    STATIC_PROGRAMS = [
        "BBA",
        "BS Accounting & Finance",
        "BS Economics",
        "BS Computer Science",
        "BS Software Engineering",
        "BS Artificial Intelligence (AI)",
        "BS Mathematics",
        "BS Media & Communication",
        "BS Physical Education & Sports Sciences",
        "BE Electrical Engineering",
        "BE Computer Systems Engineering",
        "B.Ed"
    ]
    
    @staticmethod
    def get_output_filename():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(Config.OUTPUT_DIR, f"iba_sukkur_admissions_{timestamp}.json")
    
    @staticmethod
    def get_log_filename():
        date_str = datetime.now().strftime("%Y%m%d")
        return os.path.join(Config.LOGS_DIR, f"scraper_{date_str}.log")
    
    @staticmethod
    def ensure_directories():
        os.makedirs(Config.LOGS_DIR, exist_ok=True)
        os.makedirs(Config.OUTPUT_DIR, exist_ok=True)

# Initialize directories
Config.ensure_directories()

# ==============================
# CUSTOM EXCEPTIONS
# ==============================
class ScraperException(Exception):
    pass

class DataExtractionError(ScraperException):
    pass

# ==============================
# LOGGING SETUP
# ==============================
def setup_logging():
    logger = logging.getLogger("IBA_Scraper")
    logger.setLevel(getattr(logging, Config.LOG_LEVEL))
    
    if logger.handlers:
        return logger
    
    file_handler = RotatingFileHandler(
        Config.get_log_filename(),
        maxBytes=Config.LOG_FILE_MAX_BYTES,
        backupCount=Config.LOG_FILE_BACKUP_COUNT
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(Config.LOG_FORMAT)
    file_handler.setFormatter(file_formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ==============================
# RETRY DECORATOR
# ==============================
def retry_on_failure(max_attempts=None, delay=None, backoff=None):
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
# UTILITY FUNCTIONS
# ==============================
def format_date(date_string: str) -> str:
    """Format date from dd-mm-yyyy to yyyy-mm-dd"""
    try:
        return datetime.strptime(date_string, "%d-%m-%Y").strftime("%Y-%m-%d")
    except Exception:
        logger.debug(f"Could not parse date: {date_string}")
        return date_string

def is_undergraduate_program(title: str) -> bool:
    """
    Check if the program title indicates an undergraduate admission.
    Changed 'undergraduate' to 'undergrad' to catch website spelling mistakes 
    like 'Undergradaute' shown in your screenshot.
    """
    undergraduate_keywords = [
        "undergrad", 
        "BS",
        "BBA",
        "BE",
        "bachelor"
    ]
    return any(re.search(keyword, title, re.I) for keyword in undergraduate_keywords)

# ==============================
# DATA EXTRACTION FUNCTIONS
# ==============================
@retry_on_failure()
def fetch_page(url: str, description: str) -> requests.Response:
    logger.debug(f"Fetching {description}: {url}")
    response = requests.get(url, timeout=Config.REQUEST_TIMEOUT)
    response.raise_for_status()
    return response

@retry_on_failure()
def scrape_announcements_page(page_num: int) -> Optional[Dict[str, Any]]:
    """Scrape a single announcements page looking for undergraduate admissions"""
    url = f"{Config.ADMISSION_URL}?page={page_num}"
    logger.info(f"Scraping announcements page {page_num}")
    
    try:
        response = fetch_page(url, f"announcements page {page_num}")
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("table.course-list-table tbody tr")
        logger.debug(f"Found {len(rows)} rows on page {page_num}")
        
        for row in rows:
            # Using ["th", "td"] ensures we capture the columns regardless of html structure
            cols = row.find_all(["th", "td"]) 
            if len(cols) < 5:
                continue
            
            title_tag = cols[1].find("a", class_="modal-link")
            if not title_tag:
                continue
            
            title = title_tag.get_text(strip=True)
            target_url = title_tag.get("data-targeturl")
            full_link = urljoin(Config.BASE_URL, target_url) if target_url else url
            
            # Grabbing dates based on visual table index 
            last_date = cols[3].get_text(strip=True)
            publish_date = cols[4].get_text(strip=True)
            
            if is_undergraduate_program(title):
                logger.info(f"[OK] Found undergraduate admission on page {page_num}: {title}")
                
                return {
                    "title": title,
                    "publish_date": format_date(publish_date),
                    "last_date": format_date(last_date),
                    "details_link": full_link
                }
        
        logger.info(f"No undergraduate admissions found on page {page_num}. Moving to next...")
        return None
        
    except Exception as e:
        logger.error(f"Error scraping page {page_num}: {e}")
        raise DataExtractionError(f"Failed to scrape page {page_num}: {e}")

# ==============================
# DATA PERSISTENCE
# ==============================
def insert_to_database(data):
    try:
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
    if filename is None:
        filename = Config.get_output_filename()
    try:
        temp_filename = filename + ".tmp"
        with open(temp_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
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
    start_time = time.time()
    logger.info("="*60)
    logger.info("IBA Sukkur Admission Scraper - Static Programs Edition")
    logger.info("="*60)
    
    try:
        logger.info("Paginating through announcements to find latest undergrad admission...")
        admission_data = None
        
        # Stack-like traversal: Check page 1, if None, check page 2, etc.
        for page in range(1, Config.MAX_PAGES + 1):
            result = scrape_announcements_page(page)
            if result:
                admission_data = result
                break  # Stops at the first/latest one it finds
        
        if not admission_data:
            logger.error("No undergraduate admissions found across all searched pages.")
            raise DataExtractionError("No undergraduate admissions found")
        
        # Build payload with STATIC programs
        final_data = [{
            "university": Config.UNIVERSITY_NAME,
            "program_title": admission_data["title"],
            "publish_date": admission_data["publish_date"],
            "last_date": admission_data["last_date"],
            "details_link": admission_data["details_link"],
            "programs_offered": Config.STATIC_PROGRAMS
        }]
        
        final_data = [normalize_admission_record(final_data[0])]
        
        # Persistence
        insert_to_database(final_data)
        output_file = save_to_json(final_data)
        
        # Summary
        execution_time = time.time() - start_time
        logger.info("="*60)
        logger.info("SCRAPING COMPLETED SUCCESSFULLY")
        logger.info(f"Execution time: {execution_time:.2f} seconds")
        logger.info(f"Programs appended: {len(Config.STATIC_PROGRAMS)} (Static Data)")
        logger.info(f"Publish date: {admission_data['publish_date']}")
        logger.info(f"Last date: {admission_data['last_date']}")
        logger.info("="*60)
        
        print("\n--- FINAL OUTPUT ---")
        print(json.dumps(final_data, indent=2))
        
        return final_data
        
    except ScraperException as e:
        logger.error(f"Scraper error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        run_scraper()
    except KeyboardInterrupt:
        logger.info("Scraper interrupted by user")
    except Exception as e:
        logger.critical(f"Scraper failed: {e}")
        exit(1)