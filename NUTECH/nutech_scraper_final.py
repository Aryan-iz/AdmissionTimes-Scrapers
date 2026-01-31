#!/usr/bin/env python3
"""
NUTECH Unified Admission Scraper
--------------------------------
Scrapes Undergraduate and Postgraduate (Masters/PhD) admission data from:
 - https://nutech.edu.pk/admission/undergraduate/
 - https://nutech.edu.pk/admission/postgraduate/

Features:
- Headless Chrome (Render/Fly.io compatible)
- Extracts admission poster image (now accurately targeting specific container ID 
  and handling Base64-encoded images).
- Extracts LATEST *CURRENTLY OPEN* admission schedule (registration & last date).
- Sends poster image to Gemini 2.5 Flash for program details
- Saves JSON output to: output/nutech_admissions.json
"""

import os
import re
import json
import time
import base64
import logging
from datetime import datetime, date
from typing import Dict, Optional, Tuple, List

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
# Note: ChromeDriverManager is used here, but for production environments 
# (like Render/Fly.io) you should use a static path for the driver.
from webdriver_manager.chrome import ChromeDriverManager 
from dotenv import load_dotenv

# --------------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------------
load_dotenv()
GEMINI_API_KEY = os.getenv("Geminiapikey", "").strip()

BASE_URL = "https://nutech.edu.pk/admission"
OUTPUT_DIR = "output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "nutech_admissions.json")

HEADLESS = True
REQUEST_TIMEOUT = 20

# Updated Gemini model reference to the latest preview model
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# --------------------------------------------------------------------
# SELENIUM SETUP
# --------------------------------------------------------------------
def init_driver(headless: bool = True):
    """Initializes and configures the Selenium Chrome driver."""
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    if headless:
        options.add_argument("--headless=new")
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        logging.error(f"Failed to initialize Chrome driver: {e}")
        # In case ChromeDriverManager fails (e.g., environment restrictions)
        raise

# --------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------
def safe_get(url: str, **kwargs):
    """Safely executes an HTTP GET request."""
    try:
        # User-Agent header helps avoid basic bot detection
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        return requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers, **kwargs)
    except Exception as e:
        logging.warning(f"Request failed for {url}: {e}")
        return None

def format_date(date_text: str) -> Optional[date]:
    """Tries to standardize various date formats to a datetime.date object."""
    if not date_text:
        return None
    
    text = date_text.strip()
    # Remove ordinal suffixes (e.g., '1st', '2nd') which break datetime parsing
    text = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', text)
    # Remove day of week in parenthesis (e.g., '(Sun)')
    text = re.sub(r'\s*\([^)]+\)', '', text)
    # Handle "onwards" by just taking the date
    text = re.sub(r'\s*onwards', '', text, flags=re.I).strip()
    
    # Updated formats: Month Name (B) and Short Year ('26')
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %B %Y", "%d %b %y"):
        try:
            # Note: We return a date object here
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    
    logging.debug(f"Could not parse date text: {date_text}")
    return None

def parse_range_dates(range_text: str) -> Tuple[Optional[date], Optional[date]]:
    """
    Parses a date range string like '19 Sep – 29 Dec' or '29 May - 06 July' 
    into start and end date objects. Assumes the current year if not specified.
    """
    if not range_text:
        return None, None
        
    # Split by common range delimiters ('–', '-')
    parts = re.split(r'\s*[–-]\s*', range_text.strip())
    
    if len(parts) != 2:
        return None, None
        
    start_text = parts[0].strip()
    end_text = parts[1].strip()
    
    start_date = format_date(start_text)
    end_date = format_date(end_text)
    
    # If the year is missing in one part (e.g., "19 Sep – 29 Dec 2025"), 
    # the format_date function might struggle.
    # We assume if the end date has a year, the start date should inherit it.
    if end_date and start_date and start_date.year == 1900: # Python's default if year is missing
        # Simple heuristic: assume the start date is in the same year as the end date
        try:
            temp_start_text = f"{start_text} {end_date.year}"
            start_date = format_date(temp_start_text)
        except Exception:
            pass # Keep original if parsing fails

    return start_date, end_date

# --------------------------------------------------------------------
# GEMINI IMAGE ANALYSIS (omitted for brevity, no changes here)
# --------------------------------------------------------------------
def analyze_image_with_gemini(img_source: str) -> Dict:
    """
    Uses the Gemini API to analyze the admission poster for program details.
    Accepts either a standard URL or a data:image/jpeg;base64,... URI.
    """
    if not GEMINI_API_KEY:
        logging.warning("Gemini API key not set, skipping analysis.")
        return {"error": "GEMINI_API_KEY not configured"}

    image_b64 = None
    mime_type = 'image/jpeg' # Default type

    if img_source.startswith("data:"):
        # Handle Base64 Data URI
        try:
            # Format: data:<mime-type>;base64,<data>
            header, data = img_source.split(',', 1)
            # Extract mime type using regex
            mime_match = re.search(r'data:(.*?);', header)
            if mime_match:
                mime_type = mime_match.group(1)
            image_b64 = data
        except Exception as e:
            logging.warning(f"Failed to parse Base64 URI: {e}")
            return {"error": "Failed to parse Base64 URI"}
    else:
        # Handle standard URL
        resp = safe_get(img_source)
        if not resp or resp.status_code != 200:
            logging.warning(f"Failed to download image {img_source}")
            return {"error": "Failed to download image"}

        # Attempt to determine MIME type from response headers
        mime_type = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0]
        if 'image/' not in mime_type:
            mime_type = 'image/jpeg' # Fallback
            
        image_b64 = base64.b64encode(resp.content).decode("utf-8")

    if not image_b64:
        return {"error": "Could not obtain image data."}

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Extract the university name and a list of all programs offered "
                            "from this admission advertisement image. "
                            "Return only the valid JSON object: "
                            '{"university": "...", "programs_offered": ["..."]}'
                        )
                    },
                    {"inlineData": {"mimeType": mime_type, "data": image_b64}},
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    try:
        r = requests.post(GEMINI_API_URL, json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()

        candidates = data.get("candidates", [])
        if not candidates:
            return {"error": "Gemini returned no candidates"}

        text = candidates[0]["content"]["parts"][0].get("text", "").strip()
        try:
            return json.loads(text)
        except Exception:
            # If JSON parsing fails, return the raw text for debugging
            return {"raw_text": text}
    except Exception as e:
        logging.error(f"Gemini API call failed: {e}")
        return {"error": "Gemini API failure", "details": str(e)}


# --------------------------------------------------------------------
# PAGE SCRAPER
# --------------------------------------------------------------------
def scrape_page(driver, url: str, program_level: str) -> Dict:
    """
    Scrapes the specified NUTECH admission page.
    Modified to target the admission poster image and robustly extract 
    currently open dates.
    """
    logging.info(f"Scraping {program_level} page: {url}")
    driver.get(url)
    time.sleep(3) # Wait for page content to fully load

    soup = BeautifulSoup(driver.page_source, "html.parser")

    # --- IMAGE FINDING LOGIC (Using known IDs: 795, 794, 356) ---
    poster_url = None
    content_area = soup.find('div', id='795') or \
                   soup.find('div', id='794') or \
                   soup.find('div', id='356')
    
    if not content_area:
        content_area = soup.find('div', class_='page-content') or \
                       soup.find('article') or \
                       soup.find('section')

    if content_area:
        img_tag = content_area.find("img")
        if img_tag and img_tag.get("src"):
            poster_url = img_tag["src"]
            if not poster_url.startswith("http") and not poster_url.startswith("data:"):
                poster_url = "https://nutech.edu.pk" + poster_url
    
    if not poster_url:
        img_tag = soup.find("img")
        if img_tag and img_tag.get("src"):
            poster_url = img_tag["src"]
            if not poster_url.startswith("http") and not poster_url.startswith("data:"):
                poster_url = "https://nutech.edu.pk" + poster_url
    # --------------------------------------------------------------------

    # --- MODIFIED DATE EXTRACTION LOGIC ---
    current_upload_date, current_last_date = None, None
    today = datetime.utcnow().date()
    
    # Helper function to clean text
    def clean_text(text):
        # Replace non-breaking spaces and strip general whitespace
        return text.replace(u'\xa0', u' ').strip()
        
    schedule_table = soup.find('table') 

    if schedule_table:
        rows = schedule_table.find_all("tr")
        
        # 1. LOGIC for POSTGRADUATE Table (Open/Close structure)
        if program_level == "Postgraduate":
            open_date_str, close_date_str = None, None
            
            # Find all date pairs first
            for row in rows:
                cols = row.find_all(["td", "th"])
                
                if len(cols) >= 2:
                    label = clean_text(cols[0].get_text())
                    date_text = clean_text(cols[1].get_text())
                    
                    if re.search(r'open|start', label, re.I):
                        open_date_str = date_text
                    
                    if re.search(r'close|last', label, re.I):
                        close_date_str = date_text
            
            # Now check if the most recent pair is currently open
            if open_date_str and close_date_str:
                open_date = format_date(open_date_str)
                close_date = format_date(close_date_str)
                
                if open_date and close_date and open_date <= today <= close_date:
                    current_upload_date = open_date
                    current_last_date = close_date
        
        # 2. SPECIAL LOGIC for Multi-Session Undergraduate Table (NUET-X)
        elif program_level == "Undergraduate":
            # Find the rows that contain test schedules (NUET-1, NUET-2, etc.)
            nuet_rows = [row for row in rows if re.search(r'NUET-\d', clean_text(row.get_text()))]
            
            # Iterate through all sessions to find the currently active one
            for row in nuet_rows:
                cols = row.find_all(["td", "th"])
                
                if len(cols) >= 4:
                    # Column 3 (index 2): Registration Range (e.g., "29 May - 06 July")
                    reg_range_text = clean_text(cols[2].get_text())
                    
                    start_date, end_date = parse_range_dates(reg_range_text)
                    
                    if start_date and end_date and start_date <= today <= end_date:
                        # Found the currently open session's registration dates
                        current_upload_date = start_date
                        current_last_date = end_date
                        break # Stop at the first active session found
        
    # Convert dates back to string format for JSON output
    upload_date_str = current_upload_date.strftime("%Y-%m-%d") if current_upload_date else None
    last_date_str = current_last_date.strftime("%Y-%m-%d") if current_last_date else None
    
    # --------------------------------------------------------------------

    ai_analysis = analyze_image_with_gemini(poster_url) if poster_url else {}

    return {
        "university": "National University of Technology (NUTECH), Islamabad",
        "program_level": program_level,
        "admission_page": url,
        "poster_image": poster_url,
        # Use the currently active dates
        "upload_date": upload_date_str,
        "last_date_to_apply": last_date_str,
        "ai_analysis": ai_analysis,
        "scraped_at": datetime.utcnow().isoformat() + "Z",
    }

# --------------------------------------------------------------------
# MAIN SCRAPER
# --------------------------------------------------------------------
def scrape_nutech_admissions():
    """Main function to orchestrate the scraping and saving process."""
    driver = init_driver(headless=HEADLESS)

    pages = {
        "Undergraduate": f"{BASE_URL}/undergraduate/",
        "Postgraduate": f"{BASE_URL}/postgraduate/",
    }

    results = []
    for level, url in pages.items():
        data = scrape_page(driver, url, level)
        results.append(data)
        logging.info(f"✅ Completed: {level}")

    driver.quit()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    logging.info(f"🎓 Finished scraping. Results saved to {OUTPUT_FILE}")
    return results

# --------------------------------------------------------------------
# RUN
# --------------------------------------------------------------------
if __name__ == "__main__":
    # Ensure you set the GEMINI_API_KEY environment variable to run this.
    try:
        data = scrape_nutech_admissions()
        # The print statement is useful for command-line output
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        logging.error(f"A critical error occurred during execution: {e}")
        # Exit with a non-zero status code to indicate failure
        exit(1)