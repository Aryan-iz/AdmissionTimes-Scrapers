"""
FAST University Admission Scraper - Standalone Version
Extracts undergraduate programs and admission dates using Selenium
"""

import os
import sys
import json
import logging
import re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# Add parent directory to path to import db module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.insert_admissioin import insert_admission, normalize_admission_record

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

UNIVERSITY_NAME = "FAST University"
PROGRAMS_URL = "https://nu.edu.pk/Degree-Programs"
SCHEDULE_URL = "https://nu.edu.pk/admissions/schedule"

# ==============================
# SELENIUM SETUP
# ==============================

def setup_driver():
    """Setup Chrome WebDriver with headless options"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return webdriver.Chrome(options=options)

# ==============================
# SCRAPING FUNCTIONS
# ==============================

def scrape_undergraduate_programs(driver):
    """
    Scrape undergraduate programs from degree programs page
    Returns list of program names
    """
    logger.info("Navigating to degree programs page...")
    driver.get(PROGRAMS_URL)
    
    # Wait for page to load
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    programs = []
    
    # Find all links that contain program information
    # Looking for patterns like "Bachelor of Science (Computer Science)"
    all_links = soup.find_all('a', href=True)
    
    for link in all_links:
        text = link.get_text(strip=True)
        
        # Match undergraduate programs (Bachelor of...)
        if text.startswith('Bachelor of'):
            # Clean up the text
            program = text.strip()
            if program and program not in programs:
                programs.append(program)
    
    logger.info(f"✓ Found {len(programs)} undergraduate programs")
    return programs

def scrape_admission_dates(driver):
    """
    Scrape admission dates from schedule page
    Returns dict with publish_date and last_date
    """
    logger.info("Navigating to admission schedule page...")
    driver.get(SCHEDULE_URL)
    
    # Wait for table to load
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )
    
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    dates = {
        "publish_date": None,
        "last_date": None
    }
    
    # Find all tables
    tables = soup.find_all('table')
    
    for table in tables:
        rows = table.find_all('tr')
        
        # Check if this table has undergraduate column
        if len(rows) > 0:
            header_row = rows[0]
            headers = [cell.get_text(strip=True) for cell in header_row.find_all(['td', 'th'])]
            
            # Look for "Undergraduate Programs" column
            ug_col_index = None
            for i, header in enumerate(headers):
                if 'undergraduate' in header.lower():
                    ug_col_index = i
                    break
            
            if ug_col_index is None:
                continue
            
            # Now find the "Admission Application Submission" row
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                if len(cells) > ug_col_index:
                    row_label = cells[0].get_text(strip=True).lower()
                    
                    if 'admission application' in row_label or 'submission' in row_label:
                        # Get the date range from undergraduate column
                        date_text = cells[ug_col_index].get_text(strip=True)
                        
                        # Parse date range like "May 19 (Mon) - Jul 4 (Fri)"
                        if '-' in date_text:
                            parts = date_text.split('-')
                            if len(parts) == 2:
                                start_date_str = parts[0].strip()
                                end_date_str = parts[1].strip()
                                
                                # Extract dates (remove day names in parentheses)
                                start_date_str = re.sub(r'\([^)]*\)', '', start_date_str).strip()
                                end_date_str = re.sub(r'\([^)]*\)', '', end_date_str).strip()
                                
                                dates["publish_date"] = format_date(start_date_str, year=2025)
                                dates["last_date"] = format_date(end_date_str, year=2025)
                                break
            
            if dates["last_date"]:
                break
    
    logger.info(f"✓ Extracted dates - Start: {dates['publish_date']}, Deadline: {dates['last_date']}")
    return dates

def format_date(date_str, year=None):
    """
    Convert date string to ISO format YYYY-MM-DD
    Handles multiple date formats
    """
    if not date_str:
        return None
    
    # Add year if not present
    if year and str(year) not in date_str:
        date_str = f"{date_str} {year}"
    
    # Try different date formats
    formats = [
        "%B %d %Y",       # May 19 2025
        "%b %d %Y",       # May 19 2025
        "%B %d, %Y",      # May 19, 2025
        "%d-%m-%Y",       # 19-05-2025
        "%d/%m/%Y",       # 19/05/2025
        "%Y-%m-%d"        # 2025-05-19 (already correct)
    ]
    
    for fmt in formats:
        try:
            date_obj = datetime.strptime(date_str.strip(), fmt)
            return date_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    logger.warning(f"Could not parse date: {date_str}")
    return None

# ==============================
# BUILD OUTPUT
# ==============================

def build_output_json(programs, dates):
    """Build standardized output matching other scrapers"""
    return {
        "university": UNIVERSITY_NAME,
        "program_title": "Undergraduate Admissions",
        "publish_date": dates.get("publish_date"),
        "last_date": dates.get("last_date"),
        "details_link": SCHEDULE_URL,
        "programs_offered": programs
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
        logger.warning(f"Database insert failed, continuing with JSON backup only: {e}")
        return False

def save_to_json(data, filename="fast_admissions.json"):
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

def scrape_fast_university():
    """Main scraper execution function"""
    logger.info("="*60)
    logger.info("FAST University Admission Scraper - Standalone Version")
    logger.info("="*60)
    
    driver = setup_driver()
    try:
        # Step 1: Scrape programs
        programs = scrape_undergraduate_programs(driver)
        
        if not programs:
            logger.error("No programs found")
            return None
        
        # Step 2: Scrape admission dates
        dates = scrape_admission_dates(driver)
        
        # Step 3: Build output
        output_data = normalize_admission_record(build_output_json(programs, dates))
        
        logger.info(f"Programs found: {len(output_data['programs_offered'])}")
        logger.info(f"Last date: {output_data.get('last_date', 'N/A')}")
        
        # Step 4: Validate data
        is_valid, issues = validate_scraped_data(output_data)
        if not is_valid:
            logger.warning(f"Data quality issues detected: {issues}")
        
        # Step 5: Insert into database (non-fatal if DB is unavailable)
        db_inserted = insert_to_database([output_data])
        if not db_inserted:
            logger.info("Proceeding without DB insert; backup file will still be written")
        
        # Step 6: Save backup to file
        save_to_json(output_data)
        
        # Step 7: Display output
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
        scrape_fast_university()
    except KeyboardInterrupt:
        logger.info("Scraper interrupted by user")
    except Exception as e:
        logger.critical(f"Scraper failed: {e}", exc_info=True)
        exit(1)
