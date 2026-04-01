"""
GIKI University Admission Scraper - Production Ready (Phase 1: Data Extraction)
Fetches and parses admission data, saves to structured JSON files.
No database or hashing in this phase.
"""
import requests
from bs4 import BeautifulSoup
import json
import logging
import os
import sys
import time
import glob
import urllib3
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Add parent directory to path to import db module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.insert_admissioin import insert_admission, normalize_admission_record

# Disable SSL warnings only if needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    """Centralized configuration"""
    UNIVERSITY_NAME = "GIKI"
    PROGRAMS_URL = "https://giki.edu.pk/programs/"
    ADMISSIONS_URL = "https://giki.edu.pk/admissions/admissions-undergraduates/"
    
    # Request settings
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    VERIFY_SSL = False
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    
    # Output settings
    BASE_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
    LOG_LEVEL = "INFO"
    
    # Fallback program list (if dynamic scraping fails)
    DEFAULT_BS_PROGRAMS = [
        "Artificial Intelligence", "Computer Engineering", "Computer Science",
        "Cyber Security", "Chemical Engineering", "Civil Engineering",
        "Data Science", "Electrical Engineering", "Engineering Sciences",
        "Management Sciences", "Material Engineering", "Mechanical Engineering",
        "Software Engineering"
    ]

# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logger():
    """Configure structured logging"""
    logger = logging.getLogger("giki_scraper")
    logger.setLevel(getattr(logging, Config.LOG_LEVEL.upper()))
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

# =============================================================================
# NETWORK UTILITIES
# =============================================================================

def fetch_page(url: str, description: str = "") -> Optional[BeautifulSoup]:
    """
    Fetch webpage with retry logic and error handling
    
    Args:
        url: URL to fetch
        description: Human-readable description for logging
        
    Returns:
        BeautifulSoup object or None on failure
    """
    for attempt in range(1, Config.MAX_RETRIES + 1):
        try:
            logger.info(f"Fetching {description or url} (attempt {attempt}/{Config.MAX_RETRIES})")

            # Avoid unstable inherited proxy settings for direct university site access.
            session = requests.Session()
            session.trust_env = False
            response = session.get(
                url,
                timeout=Config.REQUEST_TIMEOUT,
                verify=Config.VERIFY_SSL,
                headers={'User-Agent': Config.USER_AGENT}
            )
            response.raise_for_status()
            
            logger.info(f"✓ Successfully fetched {description or url}")
            return BeautifulSoup(response.content, 'html.parser')
            
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt}/{Config.MAX_RETRIES}")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error: {str(e)[:100]}")
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e}")
            return None  # Don't retry on 4xx/5xx
        except Exception as e:
            logger.error(f"Unexpected error: {type(e).__name__}: {e}")
        
        if attempt < Config.MAX_RETRIES:
            logger.info(f"Retrying in {Config.RETRY_DELAY} seconds...")
            time.sleep(Config.RETRY_DELAY)
    
    logger.error(f"Failed to fetch after {Config.MAX_RETRIES} attempts")
    return None


def load_latest_backup_data() -> Optional[Dict]:
    """Load the latest saved output file for fallback when live scraping fails."""
    pattern = os.path.join(Config.BASE_OUTPUT_DIR, "giki", "giki_admissions_*.json")
    files = [f for f in glob.glob(pattern) if not f.endswith("giki_admissions_latest.json")]
    if not files:
        return None

    latest = max(files, key=os.path.getmtime)
    try:
        with open(latest, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, list) and payload:
            logger.warning(f"Using fallback data from latest backup: {latest}")
            return payload[0]
    except Exception as e:
        logger.warning(f"Failed to load fallback backup file: {e}")
    return None

# =============================================================================
# DATA EXTRACTION
# =============================================================================

def scrape_programs() -> List[str]:
    """
    Scrape BS programs from GIKI programs page
    
    Returns:
        List of program names (uses fallback if scraping fails)
    """
    logger.info("Starting BS programs extraction")
    
    soup = fetch_page(Config.PROGRAMS_URL, "programs page")
    if not soup:
        logger.warning("Failed to fetch programs page, using fallback list")
        return Config.DEFAULT_BS_PROGRAMS
    
    programs = []
    
    try:
        # Find BS section heading
        bs_section = soup.find('h3', string=lambda text: text and 'BS' in text)
        
        if bs_section:
            parent = bs_section.find_parent()
            if parent:
                links = parent.find_all('a')
                for link in links:
                    text = link.get_text(strip=True)
                    if text and len(text) > 3:  # Filter noise
                        programs.append(text)
        
        if programs:
            logger.info(f"✓ Extracted {len(programs)} BS programs dynamically")
            return programs
        else:
            logger.warning("No programs found via parsing, using fallback")
            return Config.DEFAULT_BS_PROGRAMS
            
    except Exception as e:
        logger.error(f"Error parsing programs: {e}", exc_info=True)
        logger.warning("Using fallback program list")
        return Config.DEFAULT_BS_PROGRAMS

def scrape_admission_dates() -> Optional[Dict[str, str]]:
    """
    Scrape admission dates from GIKI admissions page
    
    Returns:
        Dict with 'application_start' and 'application_deadline' or None
    """
    logger.info("Starting admission dates extraction")
    
    soup = fetch_page(Config.ADMISSIONS_URL, "admissions page")
    if not soup:
        return None
    
    dates = {"application_start": None, "application_deadline": None}
    
    try:
        # Find Important Dates section
        heading = soup.find(
            ['h2', 'h3', 'h4'],
            string=lambda text: text and 'IMPORTANT DATES' in text.upper()
        )
        
        if not heading:
            logger.warning("Important Dates section not found")
            return None
        
        table = heading.find_next('table')
        if not table:
            logger.warning("Important Dates table not found")
            return None
        
        # Parse table rows
        for row in table.find_all('tr'):
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                
                if 'Application Start' in key:
                    dates['application_start'] = value
                elif 'Application Deadline' in key:
                    dates['application_deadline'] = value
        
        if dates['application_start'] and dates['application_deadline']:
            logger.info("✓ Successfully extracted admission dates")
            return dates
        else:
            logger.warning(f"Incomplete dates found: {dates}")
            return dates  # Return partial data
            
    except Exception as e:
        logger.error(f"Error parsing admission dates: {e}", exc_info=True)
        return None

# =============================================================================
# DATA TRANSFORMATION
# =============================================================================

def format_date(date_str: str) -> str:
    """
    Convert date string to ISO format YYYY-MM-DD
    
    Args:
        date_str: Date string like "April 13, 2025"
        
    Returns:
        Formatted date or original string if parsing fails
    """
    if not date_str:
        return None
    
    for fmt in ("%B %d, %Y", "%d-%b-%Y"):
        try:
            date_obj = datetime.strptime(date_str, fmt)
            return date_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue

    logger.warning(f"Could not parse date: {date_str}")
    return date_str

def build_output_json(programs: List[str], dates: Dict[str, str]) -> Dict:
    """
    Build normalized JSON output structure
    
    Args:
        programs: List of program names
        dates: Dict with admission dates
        
    Returns:
        Structured JSON dict
    """
    return {
        "university": Config.UNIVERSITY_NAME,
        "program_title": "Admissions 2025 Undergraduate Programs",
        "publish_date": format_date(dates.get('application_start')),
        "last_date": format_date(dates.get('application_deadline')),
        "details_link": Config.ADMISSIONS_URL,
        "programs_offered": programs
    }

# =============================================================================
# DATA VALIDATION
# =============================================================================

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

# =============================================================================
# FILE OPERATIONS
# =============================================================================

def insert_to_database(data):
    """Insert data into PostgreSQL database"""
    try:
        # Data should be a list with a single record
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

def save_to_json(data: Dict) -> str:
    """
    Save data to timestamped JSON file (backup only)
    
    Args:
        data: Data dict to save
        
    Returns:
        Path to saved file
    """
    # Create output directory: output/giki/
    university_dir = os.path.join(Config.BASE_OUTPUT_DIR, "giki")
    os.makedirs(university_dir, exist_ok=True)
    
    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"giki_admissions_{timestamp}.json"
    filepath = os.path.join(university_dir, filename)
    
    # Also save as "latest" for easy access
    latest_filepath = os.path.join(university_dir, "giki_admissions_latest.json")
    
    try:
        # Save timestamped version
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump([data], f, indent=2, ensure_ascii=False)
        
        # Save latest version
        with open(latest_filepath, 'w', encoding='utf-8') as f:
            json.dump([data], f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ Backup data saved to {filepath}")
        return filepath
        
    except IOError as e:
        logger.error(f"Failed to save backup JSON: {e}")
        raise

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def create_summary(success: bool, message: str, data: Optional[Dict] = None) -> Dict:
    """
    Create execution summary
    
    Args:
        success: Whether scraping succeeded
        message: Human-readable status message
        data: Optional scraped data
        
    Returns:
        Summary dict
    """
    summary = {
        "success": success,
        "university": Config.UNIVERSITY_NAME,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }
    
    if data:
        summary["data"] = data
        summary["programs_count"] = len(data.get("programs_offered", []))
    
    return summary

def main():
    """Main execution flow"""
    logger.info("="*60)
    logger.info(f"Starting {Config.UNIVERSITY_NAME} Admission Scraper")
    logger.info("="*60)
    
    try:
        # Step 1: Scrape programs
        programs = scrape_programs()
        if not programs:
            summary = create_summary(False, "Failed to extract programs")
            logger.error(json.dumps(summary, indent=2))
            return summary
        
        # Step 2: Scrape admission dates
        dates = scrape_admission_dates()
        if not dates or not dates.get('application_start') or not dates.get('application_deadline'):
            fallback = load_latest_backup_data()
            if fallback and fallback.get("publish_date") and fallback.get("last_date"):
                dates = {
                    "application_start": fallback.get("publish_date"),
                    "application_deadline": fallback.get("last_date"),
                }
                logger.warning("Admission dates fetch failed; using dates from latest local backup")
            else:
                summary = create_summary(False, "Failed to extract admission dates")
                logger.error(json.dumps(summary, indent=2))
                return summary

        # Step 3: Build output JSON
        output_data = normalize_admission_record(build_output_json(programs, dates))
        
        # Step 4: Validate data
        is_valid, issues = validate_scraped_data(output_data)
        if not is_valid:
            logger.warning(f"Data quality issues detected: {issues}")
        
        # Step 5: Save backup to file (always)
        filepath = save_to_json(output_data)

        # Step 6: Insert into database (non-fatal if unreachable)
        insert_to_database([output_data])
        
        # Step 7: Display output
        logger.info("="*60)
        logger.info("OUTPUT:")
        logger.info("="*60)
        logger.info(json.dumps([output_data], indent=2, ensure_ascii=False))
        logger.info("="*60)
        
        # Return success summary for exit code
        summary = create_summary(
            True,
            f"Successfully scraped {len(programs)} programs",
            output_data
        )
        return summary
        
    except KeyboardInterrupt:
        logger.warning("Scraper interrupted by user")
        return create_summary(False, "Interrupted by user")
    
    except Exception as e:
        logger.critical(f"Critical error: {e}", exc_info=True)
        return create_summary(False, f"Critical error: {str(e)[:100]}")

if __name__ == "__main__":
    result = main()
    sys.exit(0 if result.get("success") else 1)
