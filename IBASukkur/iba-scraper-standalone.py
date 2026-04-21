"""
IBA Sukkur Admission Scraper - Standalone Production Version
All dependencies consolidated into a single file
Matches MAJU/NUTECH scraper structure with comprehensive logging and standardized output
"""

import os
import sys
import re
import io
import json
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from functools import wraps
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Add parent directory to path to import db module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.insert_admissioin import insert_admission, normalize_admission_record

# PDF handling
try:
    from PyPDF2 import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

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
    
    # AI Settings
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    AI_MODEL = "google/gemini-2.0-flash-001"
    AI_TIMEOUT = 30  # seconds
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    GEMINI_MODEL = "gemini-2.0-flash"

    # Program extraction strategy
    # When enabled, scraper uses this stable static list and skips heavy PDF+AI program extraction.
    USE_STATIC_PROGRAMS_ONLY = True
    STATIC_PROGRAMS_OFFERED = [
        "BBA Accounting & Finance",
        "BBA Media & Communication",
        "BBA Physical Education & Sports Sciences",
        "B.Ed",
        "BS Computer Science",
        "BS Software Engineering",
        "BS Computer Science Specialization in Artificial Intelligence (AI)",
        "BS Electrical Engineering",
        "BS Mathematics Specialization in Data Science",
        "BS Mathematics Specialization in Actuarial & Risk Management",
        "BS Artificial Intelligence (AI)",
        "BE Computer Systems Engineering",
        "BE Electrical Engineering Specialization in Power",
        "BE Electrical Engineering Specialization in Electronics",
        "BE Electrical Engineering Specialization in Telecommunication",
    ]
    
    # Logging Settings
    LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    LOG_FILE_BACKUP_COUNT = 5
    
    @staticmethod
    def get_output_filename():
        """Generate timestamped output filename"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(Config.OUTPUT_DIR, f"iba_sukkur_admissions_{timestamp}.json")
    
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
    logger = logging.getLogger("IBA_Scraper")
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
                if "=" not in line:
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
    # Keep current calendar year.
    if 1 <= month <= 6:
        semester = "Spring"
    else:
        semester = "Fall"
    
    return f"{semester} {year}"

# ==============================
# UTILITY FUNCTIONS
# ==============================
def format_date(date_string: str) -> str:
    """
    Format date from dd-mm-yyyy to yyyy-mm-dd
    
    Args:
        date_string: Date in dd-mm-yyyy format
        
    Returns:
        Date in yyyy-mm-dd format or original string if parsing fails
    """
    try:
        return datetime.strptime(date_string, "%d-%m-%Y").strftime("%Y-%m-%d")
    except Exception:
        logger.debug(f"Could not parse date: {date_string}")
        return date_string

def is_undergraduate_program(title: str) -> bool:
    """
    Check if the program title indicates an undergraduate admission
    
    Args:
        title: Program title to check
        
    Returns:
        True if undergraduate program, False otherwise
    """
    normalized = re.sub(r"[_-]+", " ", title or "")

    # Handle common spelling variants seen on source pages (undergradaute, udergraduate).
    undergrad_patterns = [
        r"\bundergrad\w*\b",
        r"\budergrad\w*\b",
        r"\bbs\b",
        r"\bbba\b",
        r"\bbe\b",
        r"\bbachelor\w*\b",
    ]
    excluded_patterns = [
        r"\bms\b",
        r"\bm\.?phil\b",
        r"\bph\.?d\b",
        r"\bmba\b",
        r"\bdiploma\b",
    ]

    has_undergrad_signal = any(re.search(p, normalized, re.I) for p in undergrad_patterns)
    has_excluded_signal = any(re.search(p, normalized, re.I) for p in excluded_patterns)

    # If both exist, prefer undergrad signal for mixed titles.
    return has_undergrad_signal or (has_undergrad_signal and has_excluded_signal)


def score_admission_title(title: str) -> int:
    """Score announcement titles so the newest, relevant undergrad notice is selected."""
    text = re.sub(r"[_-]+", " ", (title or "").lower())
    score = 0

    if "2026" in text:
        score += 100
    if re.search(r"\bundergrad\w*\b|\budergrad\w*\b", text):
        score += 60
    if "main campus" in text:
        score += 35
    if re.search(r"phase\s*[-_]?\s*(i|1)\b", text):
        score += 25

    # De-prioritize non-undergraduate categories.
    if re.search(r"\bms\b|\bm\.?phil\b|\bph\.?d\b|\bmba\b|\bdiploma\b", text):
        score -= 80

    return score


def select_preferred_pdf_link(soup: BeautifulSoup) -> Optional[str]:
    """Select Main Campus Undergraduate Phase-I advertisement PDF when available."""
    candidates = []

    for link in soup.find_all("a"):
        link_text = link.get_text(" ", strip=True)
        href = link.get("href") or ""
        if not href:
            continue

        full_link = urljoin(Config.BASE_URL, href)
        text = re.sub(r"[_-]+", " ", link_text.lower())
        href_lower = href.lower()

        # Consider only advertisement/PDF style links.
        if not (
            href_lower.endswith(".pdf")
            or "advert" in text
            or "admission_documents" in href_lower
        ):
            continue

        score = 0
        if all(k in text for k in ["main", "campus", "advertisement"]):
            score += 200
        if re.search(r"undergrad\w*|udergrad\w*", text):
            score += 80
        if re.search(r"phase\s*[-_]?\s*(i|1)\b", text):
            score += 70
        if "2026" in text or "2026" in href_lower:
            score += 20

        # De-prioritize non-target docs.
        if "campuses" in text and "main campus" not in text:
            score -= 40
        if "sample test" in text or "eligibility" in text:
            score -= 60

        candidates.append((score, full_link, link_text))

    if not candidates:
        return None

    best = max(candidates, key=lambda item: item[0])
    logger.info(f"[OK] Selected PDF link: {best[2]}")
    return best[1]


def clean_program_name(name: str) -> str:
    """Clean OCR/PDF artifacts in extracted program names."""
    if not name:
        return ""

    cleaned = str(name)

    # Normalize common ligatures and unicode artifacts from PDF extraction.
    replacements = {
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\u2013": "-",
        "\u2014": "-",
        "\u2019": "'",
        "\u00a0": " ",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)

    # Fix common OCR misspellings seen in this source.
    cleaned = re.sub(r"\bActurial\b", "Actuarial", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bArti\s*ficial\b", "Artificial", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;\t\n\r")
    return cleaned


def extract_programs_from_pdf_text(pdf_text: str) -> List[str]:
    """Extract undergraduate program names from PDF text and split merged strings."""
    if not pdf_text:
        return []

    # Match each program starting with a known undergraduate prefix and stop
    # when the next program prefix starts or line/document ends.
    pattern = re.compile(
        r'(?:BBA|B\.Ed|BE|BS)\s+[A-Za-z][A-Za-z0-9&(),/\-\s]{2,120}?(?=(?:\s(?:BBA|B\.Ed|BE|BS)\s)|\n|$)',
        re.IGNORECASE,
    )

    found = []
    for m in pattern.finditer(pdf_text):
        item = clean_program_name(m.group(0))
        if len(item) >= 6:
            found.append(item)

    # Deduplicate while preserving order
    deduped = []
    seen = set()
    for item in found:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped


def has_combined_program_chunks(programs: List[str]) -> bool:
    """Heuristic: detect likely merged program strings from AI output."""
    for p in programs:
        token_count = len(p.split())
        if token_count > 10 and "," not in p:
            return True
    return False


def normalize_program_list(programs: List[str]) -> List[str]:
    """Apply final cleanup and dedupe for program list."""
    normalized = []
    seen = set()
    for p in programs or []:
        cleaned = clean_program_name(p)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(cleaned)
    return normalized

# ==============================
# PDF PROCESSING
# ==============================
def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract text from PDF bytes
    
    Args:
        pdf_bytes: PDF content as bytes
        
    Returns:
        Extracted text from PDF
    """
    if not PDF_AVAILABLE:
        logger.warning("PyPDF2 not installed. Cannot extract PDF text.")
        return ""
    
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        pdf_reader = PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        logger.info(f"[OK] Extracted {len(text)} characters from PDF")
        return text
    except Exception as e:
        logger.warning(f"Failed to extract text from PDF: {e}")
        return ""

# ==============================
# DATA EXTRACTION FUNCTIONS
# ==============================
@retry_on_failure()
def fetch_page(url: str, description: str) -> requests.Response:
    """
    Fetch a webpage with retry logic
    
    Args:
        url: URL to fetch
        description: Description for logging
        
    Returns:
        Response object
    """
    logger.debug(f"Fetching {description}: {url}")
    response = requests.get(url, timeout=Config.REQUEST_TIMEOUT)
    response.raise_for_status()
    logger.debug(f"[OK] Successfully fetched {description}")
    return response

@retry_on_failure()
def scrape_announcements_page(page_num: int) -> Optional[Dict[str, Any]]:
    """
    Scrape a single announcements page looking for undergraduate admissions
    
    Args:
        page_num: Page number to scrape
        
    Returns:
        Dictionary with admission data or None if not found
    """
    url = f"{Config.ADMISSION_URL}?page={page_num}"
    logger.info(f"Scraping announcements page {page_num}")
    
    try:
        response = fetch_page(url, f"announcements page {page_num}")
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("table.course-list-table tbody tr")
        logger.debug(f"Found {len(rows)} rows on page {page_num}")
        
        candidates = []
        for row in rows:
            cols = row.find_all("th")
            if len(cols) < 5:
                continue
            
            # Extract basic information
            title_tag = cols[1].find("a", class_="modal-link")
            if not title_tag:
                continue
            
            title = title_tag.get_text(strip=True)
            target_url = title_tag.get("data-targeturl")
            full_link = urljoin(Config.BASE_URL, target_url)
            publish_date = cols[4].get_text(strip=True)
            last_date = cols[3].get_text(strip=True)
            
            # Check if this is an undergraduate admission and score it.
            if is_undergraduate_program(title):
                candidate = {
                    "title": title,
                    "publish_date": format_date(publish_date),
                    "last_date": format_date(last_date),
                    "details_link": full_link
                }
                candidates.append((score_admission_title(title), candidate))

        if candidates:
            # Prefer highest-score announcement (for example 2026 undergraduate main-campus notice).
            candidates.sort(key=lambda item: item[0], reverse=True)
            selected = candidates[0][1]
            logger.info(f"[OK] Found undergraduate admission: {selected['title']}")
            return selected
        
        logger.debug(f"No undergraduate admissions found on page {page_num}")
        return None
        
    except Exception as e:
        logger.error(f"Error scraping page {page_num}: {e}")
        raise DataExtractionError(f"Failed to scrape page {page_num}: {e}")

@retry_on_failure()
def scrape_detail_page(detail_url: str) -> Optional[str]:
    """
    Scrape detail page to get PDF link
    
    Args:
        detail_url: URL of the detail page
        
    Returns:
        PDF URL or None if not found
    """
    logger.info("Scraping detail page for PDF link")
    
    try:
        response = fetch_page(detail_url, "detail page")
        soup = BeautifulSoup(response.text, "html.parser")
        
        pdf_link = select_preferred_pdf_link(soup)
        if not pdf_link:
            logger.warning("No advertisement PDF link found on detail page")
            return None
        logger.info(f"[OK] Found advertisement PDF: {pdf_link}")
        
        return pdf_link
        
    except Exception as e:
        logger.error(f"Error scraping detail page: {e}")
        raise DataExtractionError(f"Failed to scrape detail page: {e}")

@retry_on_failure()
def download_and_extract_pdf(pdf_url: str) -> str:
    """
    Download PDF and extract text
    
    Args:
        pdf_url: URL of the PDF to download
        
    Returns:
        Extracted text from PDF
    """
    logger.info(f"Downloading PDF from {pdf_url}")

    if not PDF_AVAILABLE:
        logger.warning("PyPDF2 not installed. Skipping PDF text extraction.")
        return ""
    
    try:
        response = requests.get(pdf_url, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        pdf_bytes = response.content
        logger.info(f"[OK] Downloaded PDF ({len(pdf_bytes)} bytes)")
        
        # Extract text
        pdf_text = extract_text_from_pdf(pdf_bytes)
        if not pdf_text:
            logger.warning("PDF text extraction returned empty content")
            return ""
        
        return pdf_text
        
    except Exception as e:
        logger.error(f"Error downloading/extracting PDF: {e}")
        raise DataExtractionError(f"Failed to process PDF: {e}")

# ==============================
# AI ANALYSIS
# ==============================
@retry_on_failure(max_attempts=2)
def analyze_pdf_with_ai(pdf_text: str) -> Dict[str, Any]:
    """
    Analyze PDF text with AI to extract program information
    
    Args:
        pdf_text: Extracted text from PDF
        
    Returns:
        Dictionary with analysis results
    """
    api_key = os.environ.get("scraperapikey")
    
    if not api_key:
        logger.warning("AI API key not found. Skipping AI analysis.")
        raise AIAnalysisError("API key not configured")
    
    logger.info("Sending PDF text to AI for analysis...")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://admitly-scraper.app",
        "X-Title": "Admitly Scraper"
    }
    
    prompt = (
        "You are an academic data extractor. Analyze the following text extracted from a university admission notice PDF "
        "and return ONLY a JSON object with the following fields:\n"
        "- university (string)\n"
        "- programs_offered (array of program names)\n"
        "- ai_comments (string summarizing the admission status)\n\n"
        "Focus only on UNDERGRADUATE programs (e.g., BS, BBA, BE, etc.). "
        "Return ONLY the JSON, no markdown formatting or explanation.\n\n"
        f"PDF TEXT:\n{pdf_text[:4000]}"
    )
    
    payload = {
        "model": Config.AI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a data extractor. Output valid JSON only."},
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
        
        analysis = json.loads(ai_content)
        logger.info("[OK] AI analysis completed successfully")
        return analysis
        
    except requests.exceptions.Timeout:
        logger.error("AI API request timed out")
        raise AIAnalysisError("AI API timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"AI API request failed: {e}")
        raise AIAnalysisError(f"AI API error: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse AI response: {e}")
        raise AIAnalysisError(f"Invalid AI response: {e}")


@retry_on_failure(max_attempts=2)
def analyze_pdf_with_gemini(pdf_text: str) -> Dict[str, Any]:
    """
    Analyze PDF text with Gemini API directly.

    Args:
        pdf_text: Extracted text from PDF

    Returns:
        Dictionary with analysis results
    """
    api_key = os.environ.get("Geminiapikey")
    if not api_key:
        logger.warning("Gemini API key not found. Skipping Gemini fallback.")
        raise AIAnalysisError("Gemini API key not configured")

    logger.info("OpenRouter failed. Falling back to Gemini API...")

    prompt = (
        "You are an academic data extractor. Analyze the following text extracted from a university admission notice PDF "
        "and return ONLY a JSON object with the following fields:\n"
        "- university (string)\n"
        "- programs_offered (array of program names)\n"
        "- ai_comments (string summarizing the admission status)\n\n"
        "Focus only on UNDERGRADUATE programs (e.g., BS, BBA, BE, etc.). "
        "Return ONLY the JSON, no markdown formatting or explanation.\n\n"
        f"PDF TEXT:\n{pdf_text[:6000]}"
    )

    url = Config.GEMINI_API_URL.format(model=Config.GEMINI_MODEL)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    try:
        response = requests.post(
            f"{url}?key={api_key}",
            json=payload,
            timeout=Config.AI_TIMEOUT,
        )
        response.raise_for_status()

        gemini_json = response.json()
        content = gemini_json["candidates"][0]["content"]["parts"][0]["text"]

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        analysis = json.loads(content)
        logger.info("[OK] Gemini fallback analysis completed successfully")
        return analysis

    except requests.exceptions.Timeout:
        logger.error("Gemini API request timed out")
        raise AIAnalysisError("Gemini API timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"Gemini API request failed: {e}")
        raise AIAnalysisError(f"Gemini API error: {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse Gemini response: {e}")
        raise AIAnalysisError(f"Invalid Gemini response: {e}")

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
    logger.info("IBA Sukkur Admission Scraper - Standalone Version")
    logger.info("="*60)
    
    # Load environment variables
    load_env_variables()
    
    try:
        # Search for latest undergraduate admission
        logger.info("Searching for latest undergraduate admission...")
        admission_data = None
        
        for page in range(1, Config.MAX_PAGES + 1):
            result = scrape_announcements_page(page)
            if result:
                admission_data = result
                break
        
        if not admission_data:
            logger.error("No undergraduate admissions found")
            raise DataExtractionError("No undergraduate admissions found")
        
        pdf_url = None
        pdf_text = None
        programs_offered = normalize_program_list(Config.STATIC_PROGRAMS_OFFERED)
        ai_comments = "Using static undergraduate program list"

        if Config.USE_STATIC_PROGRAMS_ONLY:
            logger.info("Using hardcoded undergraduate program list. Skipping PDF+AI extraction.")
        else:
            # Get PDF link from detail page
            pdf_url = scrape_detail_page(admission_data["details_link"])

            if pdf_url:
                try:
                    pdf_text = download_and_extract_pdf(pdf_url)

                    # AI Analysis with OpenRouter first, then Gemini fallback.
                    ai_result = None
                    try:
                        ai_result = analyze_pdf_with_ai(pdf_text)
                    except AIAnalysisError as openrouter_error:
                        logger.warning(f"OpenRouter analysis failed: {openrouter_error}")
                        try:
                            ai_result = analyze_pdf_with_gemini(pdf_text)
                        except AIAnalysisError as gemini_error:
                            logger.warning(f"Gemini fallback failed: {gemini_error}")

                    if ai_result:
                        ai_programs = normalize_program_list(ai_result.get("programs_offered", []))
                        ai_comments = ai_result.get("ai_comments", "Data extracted successfully")

                        # If AI output looks merged/noisy, prefer deterministic extraction from PDF text.
                        extracted_programs = extract_programs_from_pdf_text(pdf_text)
                        if extracted_programs and (
                            has_combined_program_chunks(ai_programs)
                            or len(extracted_programs) > len(ai_programs)
                        ):
                            programs_offered = normalize_program_list(extracted_programs)
                            logger.info(f"Normalized programs from PDF text: {len(programs_offered)}")
                        elif ai_programs:
                            programs_offered = ai_programs

                    # Last fallback from PDF text when both AI providers fail.
                    if not programs_offered and pdf_text:
                        programs_offered = normalize_program_list(extract_programs_from_pdf_text(pdf_text)[:30])
                        logger.info(f"Extracted {len(programs_offered)} programs from PDF text")

                except Exception as e:
                    logger.warning(f"PDF processing failed: {e}")

            # Last safeguard: keep stable static list if extraction produced nothing.
            if not programs_offered:
                programs_offered = normalize_program_list(Config.STATIC_PROGRAMS_OFFERED)
                logger.info("No programs extracted from PDF/AI. Falling back to static list.")
        
        # Detect semester
        semester = detect_current_semester()
        logger.info(f"Detected semester: {semester}")
        
        # Structure final data with flattened format
        final_data = [{
            "university": Config.UNIVERSITY_NAME,
            "program_title": admission_data["title"],
            "publish_date": admission_data["publish_date"],
            "last_date": admission_data["last_date"],
            "details_link": admission_data["details_link"],
            "programs_offered": programs_offered
        }]
        final_data = [normalize_admission_record(final_data[0])]
        
        # Validate data
        is_valid, issues = validate_scraped_data(final_data[0])
        if not is_valid:
            logger.warning(f"Data quality issues detected: {issues}")
        
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
        logger.info(f"Programs found: {len(programs_offered)}")
        logger.info(f"Last date: {admission_data['last_date']}")
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
