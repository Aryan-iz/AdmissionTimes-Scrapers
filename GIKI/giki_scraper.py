import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import os
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def scrape_giki_programs():
    """
    Scrape BS programs from GIKI programs page
    """
    programs_url = "https://giki.edu.pk/programs/"
    
    try:
        response = requests.get(programs_url, timeout=30, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find all BS programs
        programs = []
        
        # Based on the webpage structure, BS programs are listed in the format:
        # The programs list includes: Artificial Intelligence, Computer Engineering, etc.
        bs_programs_list = [
            "Artificial Intelligence",
            "Computer Engineering", 
            "Computer Science",
            "Cyber Security",
            "Chemical Engineering",
            "Civil Engineering",
            "Data Science",
            "Electrical Engineering",
            "Engineering Sciences",
            "Management Sciences",
            "Material Engineering",
            "Mechanical Engineering",
            "Software Engineering"
        ]
        
        # Try to find and parse dynamically
        bs_section = soup.find('h3', string=lambda text: text and 'BS' in text)
        if bs_section:
            # Find all links in the container after BS heading
            parent = bs_section.find_parent()
            if parent:
                links = parent.find_all('a')
                for link in links:
                    text = link.get_text(strip=True)
                    if text and len(text) > 3:  # Filter out empty or very short texts
                        programs.append(text)
        
        # If dynamic scraping didn't work, use the static list
        if not programs:
            programs = bs_programs_list
        
        return programs
    except Exception as e:
        print(f"Error scraping programs: {e}")
        return []

def scrape_giki_admission_dates():
    """
    Scrape admission dates from GIKI admissions page
    """
    admissions_url = "https://giki.edu.pk/admissions/admissions-undergraduates/"
    
    try:
        response = requests.get(admissions_url, timeout=30, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        dates = {
            "application_start": None,
            "application_deadline": None
        }
        
        # Find the Important Dates table
        # Look for heading containing "IMPORTANT DATES"
        important_dates_heading = soup.find(['h2', 'h3', 'h4'], string=lambda text: text and 'IMPORTANT DATES' in text.upper())
        
        if important_dates_heading:
            # Find the table after the heading
            table = important_dates_heading.find_next('table')
            if table:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        key = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)
                        
                        if 'Application Start' in key:
                            dates['application_start'] = value
                        elif 'Application Deadline' in key:
                            dates['application_deadline'] = value
        
        return dates
    except Exception as e:
        print(f"Error scraping admission dates: {e}")
        return None

def format_date(date_str):
    """
    Convert date string to YYYY-MM-DD format
    Example: "April 13, 2025" -> "2025-04-13"
    """
    try:
        date_obj = datetime.strptime(date_str, "%B %d, %Y")
        return date_obj.strftime("%Y-%m-%d")
    except:
        return date_str

def main():
    print("Starting GIKI scraper...")
    
    # Scrape programs
    print("\nScraping BS programs...")
    programs = scrape_giki_programs()
    print(f"Found {len(programs)} programs: {programs}")
    
    # Scrape admission dates
    print("\nScraping admission dates...")
    dates = scrape_giki_admission_dates()
    print(f"Admission dates: {dates}")
    
    # Create output directory if it doesn't exist
    output_dir = os.path.join(os.path.dirname(__file__), 'output')
    os.makedirs(output_dir, exist_ok=True)
    
    # Create the output JSON structure
    output_data = []
    
    if programs and dates:
        # Format dates
        publish_date = format_date(dates.get('application_start', ''))
        last_date = format_date(dates.get('application_deadline', ''))
        
        # Create a single entry with all programs
        entry = {
            "university": "GIKI",
            "program_title": "Admissions 2025 Undergraduate Programs",
            "publish_date": publish_date,
            "last_date": last_date,
            "details_link": "https://giki.edu.pk/admissions/admissions-undergraduates/",
            "advertisement_link": "https://giki.edu.pk/programs/",
            "programs": programs
        }
        output_data.append(entry)
    
    # Save to JSON file
    output_file = os.path.join(output_dir, 'giki_admissions_latest.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Data saved to: {output_file}")
    print(f"\nTotal entries: {len(output_data)}")
    
    # Print the output
    print("\n" + "="*50)
    print("OUTPUT:")
    print("="*50)
    print(json.dumps(output_data, indent=2, ensure_ascii=False))
    
    return output_data

if __name__ == "__main__":
    main()
