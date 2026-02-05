"""
Muhammad Ali Jinnah University Admission Scraper - Standalone Version
All dependencies consolidated into a single file
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ==============================
# CONFIGURATION
# ==============================
class Config:
    """Configuration settings for the scraper"""
    
    # Paths
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
    OUTPUT_DIR = os.path.join(BASE_DIR, "output")
    ENV_FILE = os.path.join(BASE_DIR, ".env")
    
    # University Information
    UNIVERSITY_NAME = "Muhammad Ali Jinnah University"
    UNIVERSITY_SHORT_NAME = "MAJU Karachi"
    BASE_URL = "https://jinnah.edu/"
    ADMISSION_DATES_URL = "https://jinnah.edu/key-admission-dates/"
    UNDERGRAD_PROGRAMS_URL = "https://jinnah.edu/undergraduate-programs/"
    
    # Selenium Settings
    SELENIUM_TIMEOUT = 15  # seconds
    PAGE_LOAD_TIMEOUT = 30  # seconds
    IMPLICIT_WAIT = 5  # seconds
    
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
        return os.path.join(Config.OUTPUT_DIR, f"maju_admissions_{timestamp}.json")
    
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
    logger = logging.getLogger("MAJU_Scraper")
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
    
    # Spring semester: January - June (admissions typically in Dec-Feb)
    # Fall semester: July - December (admissions typically in Jun-Aug)
    
    if month >= 1 and month <= 6:
        semester = "Spring"
        # If we're in Jan-Feb, it's for current year, otherwise next year
        if month <= 2:
            year = year
        else:
            year = year + 1
    else:
        semester = "Fall"
        year = year
    
    return f"{semester} {year}"

# ==============================
# SELENIUM SETUP
# ==============================
def setup_driver():
    """Configure and return Chrome WebDriver"""
    logger.info("Setting up Chrome WebDriver...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(Config.PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(Config.IMPLICIT_WAIT)
        logger.info("[OK] WebDriver initialized successfully")
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {e}")
        raise

# ==============================
# DATA EXTRACTION FUNCTIONS
# ==============================
@retry_on_failure()
def scrape_admission_dates(driver):
    """Scrape admission dates from the university website"""
    logger.info(f"Scraping admission dates from {Config.ADMISSION_DATES_URL}")
    
    try:
        driver.get(Config.ADMISSION_DATES_URL)
        WebDriverWait(driver, Config.SELENIUM_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "table"))
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        table = soup.find("table")
        
        if not table:
            raise DataExtractionError("Admission dates table not found")
        
        dates = {"publish_date": None, "last_date": None}
        
        for row in table.find_all("tr"):
            cols = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
            if len(cols) < 2:
                continue
            
            label = cols[0].strip().lower()
            value = cols[1].strip()
            
            # Look for Open Day (publish date)
            if "open day" in label:
                dates["publish_date"] = value
                logger.debug(f"Found publish date (Open Day): {value}")
            
            # Look for application form submission deadline
            elif "last date" in label and "application form" in label:
                dates["last_date"] = value
                logger.debug(f"Found last date: {value}")
        
        # Validate extracted dates
        if not dates["last_date"]:
            logger.warning("Last date not found in table")
        
        logger.info(f"[OK] Extracted dates - Publish: {dates['publish_date']}, Last: {dates['last_date']}")
        return dates
        
    except TimeoutException:
        logger.error("Timeout waiting for admission dates table")
        raise DataExtractionError("Timeout loading admission dates page")
    except Exception as e:
        logger.error(f"Error scraping admission dates: {e}")
        raise DataExtractionError(f"Failed to scrape admission dates: {e}")

@retry_on_failure()
def scrape_undergraduate_programs(driver):
    """Scrape list of undergraduate programs"""
    logger.info(f"Scraping programs from {Config.UNDERGRAD_PROGRAMS_URL}")
    
    try:
        driver.get(Config.UNDERGRAD_PROGRAMS_URL)
        WebDriverWait(driver, Config.SELENIUM_TIMEOUT).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a.icon-box-link"))
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        programs = []
        
        for box in soup.select("a.icon-box-link"):
            tag = box.parent.find(["h3", "h4"])
            if tag:
                program_name = tag.get_text(strip=True)
                if program_name and program_name not in programs:
                    programs.append(program_name)
        
        if not programs:
            logger.warning("No programs found on the page")
        else:
            logger.info(f"[OK] Found {len(programs)} programs")
        
        return programs
        
    except TimeoutException:
        logger.error("Timeout waiting for programs page")
        raise DataExtractionError("Timeout loading programs page")
    except Exception as e:
        logger.error(f"Error scraping programs: {e}")
        raise DataExtractionError(f"Failed to scrape programs: {e}")

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
    
    if not data.get("ai_analysis", {}).get("programs_offered"):
        issues.append("No programs found")
    
    if issues:
        logger.warning(f"Data validation issues: {', '.join(issues)}")
    else:
        logger.info("[OK] Data validation passed")
    
    return len(issues) == 0, issues

# ==============================
# DATA PERSISTENCE
# ==============================
def save_to_json(data, filename=None):
    """Save data to JSON file with atomic write"""
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
        
        logger.info(f"[OK] Data saved to {filename}")
        return filename
        
    except Exception as e:
        logger.error(f"Failed to save data: {e}")
        raise

# ==============================
# MAIN SCRAPER
# ==============================
def run_scraper():
    """Main scraper execution function"""
    start_time = time.time()
    logger.info("="*60)
    logger.info("MAJU Admission Scraper - Standalone Version")
    logger.info("="*60)
    
    # Load environment variables
    load_env_variables()
    
    driver = None
    try:
        # Setup WebDriver
        driver = setup_driver()
        
        # Scrape data
        logger.info("Starting data extraction...")
        dates = scrape_admission_dates(driver)
        programs = scrape_undergraduate_programs(driver)
        
        # Detect semester
        semester = detect_current_semester()
        logger.info(f"Detected semester: {semester}")
        
        # Structure raw data
        raw_data = [{
            "university": Config.UNIVERSITY_NAME,
            "program_title": f"{semester} Undergraduate Admissions",
            "publish_date": dates.get("publish_date"),
            "last_date": dates.get("last_date"),
            "details_link": Config.ADMISSION_DATES_URL,
            "ai_analysis": {
                "university": Config.UNIVERSITY_SHORT_NAME,
                "programs_offered": programs
            }
        }]
        
        # Validate data
        is_valid, issues = validate_scraped_data(raw_data[0])
        if not is_valid:
            logger.warning(f"Data quality issues detected: {issues}")
        
        # AI Analysis (optional - continues even if it fails)
        try:
            final_data = analyze_with_ai(raw_data)
        except AIAnalysisError as e:
            logger.warning(f"AI analysis failed, using raw data: {e}")
            final_data = raw_data
        
        # Save to file
        output_file = save_to_json(final_data)
        
        # Print summary
        execution_time = time.time() - start_time
        logger.info("="*60)
        logger.info("SCRAPING COMPLETED SUCCESSFULLY")
        logger.info(f"Execution time: {execution_time:.2f} seconds")
        logger.info(f"Output file: {output_file}")
        logger.info(f"Programs found: {len(programs)}")
        logger.info(f"Last date: {dates.get('last_date', 'N/A')}")
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
