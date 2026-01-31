"""
NUTECH Undergraduate Admissions Scraper (Updated)
------------------------------------------------
 Using Selenium (headless) + BeautifulSoup
 Also Handling homepage pop-up
 Extracting undergraduate programs
 Extracting Upload Date & Last Date from page
 Outputs clean JSON ready for database use to so i can use for fyp project 
"""

import re
import json
import time
import logging
from datetime import datetime
from typing import List, Dict, Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup, Tag

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')


# ------------------ Driver Setup ------------------
def init_driver(headless: bool = True, timeout: int = 30):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(timeout)
    return driver


# ------------------ Modal Handling ------------------
def wait_for_modal_and_close(driver, wait_timeout=10):
    try:
        wait = WebDriverWait(driver, wait_timeout)
        # Detect any modal
        modal = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".modal-content, #myModal, div[role='dialog']"))
        )
        if modal:
            logging.info("Modal detected. Attempting to close it...")
            close_btn = driver.find_elements(By.CSS_SELECTOR, ".close, [data-dismiss='modal'], button.close")
            if close_btn:
                driver.execute_script("arguments[0].click();", close_btn[0])
                time.sleep(1)
                logging.info("Modal closed successfully.")
    except TimeoutException:
        logging.info("No modal detected or timeout reached — continuing.")
    except Exception as e:
        logging.warning(f"Error while handling modal: {e}")


# ------------------ Utility Functions ------------------
def scroll_to_bottom(driver, pause=0.7):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def extract_programs(soup: BeautifulSoup) -> List[str]:
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
    return sorted(programs)


def extract_section_text(soup: BeautifulSoup, heading_keyword: str) -> Optional[str]:
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


def is_within_opportunity_window(registration_start: datetime, registration_end: datetime, 
                                  current_date: Optional[datetime] = None) -> bool:
    """Check if current date falls within the registration window."""
    if current_date is None:
        current_date = datetime.now()
    
    # Only include if registration window includes today
    return registration_start.date() <= current_date.date() <= registration_end.date()


def extract_admission_schedule(soup: BeautifulSoup) -> List[Dict]:
    """Extract admission schedule from table and filter by opportunity window."""
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
                    logging.info(f"✓ Added {batch_info} (within opportunity window)")
                else:
                    logging.info(f"✗ Skipped {batch_info} (outside opportunity window)")
            
            except Exception as e:
                logging.debug(f"Error parsing row: {e}")
                continue
    
    return schedule_data


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


# ------------------ Main Scraper ------------------
def scrape_nutech_admissions(headless: bool = True, wait_timeout: int = 15) -> Dict:
    driver = init_driver(headless=headless, timeout=60)
    try:
        admissions_url = "https://nutech.edu.pk/admissions/"

        # Go directly to admissions page (skip homepage to avoid modal/timeout issues)
        logging.info(f"Navigating to admissions page: {admissions_url}")
        driver.get(admissions_url)
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
        )

        scroll_to_bottom(driver)
        soup = BeautifulSoup(driver.page_source, "lxml")

        # --- Extract Programs ---
        logging.info("Extracting programs...")
        programs = extract_programs(soup)
        logging.info(f"Found {len(programs)} unique programs")

        # --- Extract Admission Schedule (Opportunity Window) ---
        logging.info("Extracting admission schedule (opportunity window only)...")
        admission_schedule = extract_admission_schedule(soup)
        logging.info(f"Found {len(admission_schedule)} active admission windows")

        # --- Extract Upload Date ---
        full_text = soup.get_text(separator=" ", strip=True)
        upload_date = None
        upload_match = re.search(r"(Updated|Published|Posted)\s*on[:\-]?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s*\d{2,4})", full_text, re.I)
        if upload_match:
            try:
                d = datetime.strptime(upload_match.group(2), "%d %b %Y")
                upload_date = d.strftime("%Y-%m-%d")
            except Exception:
                upload_date = upload_match.group(2)
        else:
            dates_in_text = extract_dates_from_text(full_text)
            if dates_in_text:
                upload_date = min(dates_in_text).strftime("%Y-%m-%d")

        # --- Extract Last Date (from opportunity window) ---
        logging.info("Extracting last date from opportunity window...")
        last_date = None
        if admission_schedule:
            # Get the latest registration end date from active windows
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

        # --- Prepare Final JSON (IBA Sukkur format) ---
        result = {
            "university": "National University of Technology (NUTECH), Islamabad",
            "programs_offered": programs,
            "publish_date": upload_date or "Not Found",
            "last_date": last_date or "Not Found",
            "opportunity_window": admission_schedule if admission_schedule else [],
            "details_link": "https://nutech.edu.pk/admissions/"
        }

        if not upload_date:
            logging.warning("⚠️ Upload date not found on page.")
        if not admission_schedule:
            logging.warning("⚠️ No active admission windows found.")
        if not last_date:
            logging.warning("⚠️ Last date not found on page.")

        return result

    finally:
        driver.quit()


# ------------------ Entry Point ------------------
if __name__ == "__main__":
    logging.info("Starting NUTECH admissions scraper...")
    data = scrape_nutech_admissions(headless=True, wait_timeout=18)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    with open("nutech_admissions.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logging.info("Saved output to nutech_admissions.json")
