"""
Job Discovery Runner — Step 1
Runs LinkedIn scraper + career pages scraper in sequence.
Schedule this with cron or GitHub Actions to run daily.
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, str(Path(__file__).parent / "scraper"))

# from linkedin_scraper import run_linkedin_scraper
from career_pages_scraper import run_career_page_scraper

CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def write_scrape_timestamp():
    IST = timezone(timedelta(hours=5, minutes=30))
    timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    creds  = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    client = gspread.authorize(creds)
    ss     = client.open("Job Search Tracker")
    try:
        meta = ss.worksheet("Meta")
    except gspread.WorksheetNotFound:
        meta = ss.add_worksheet(title="Meta", rows=2, cols=2)
        meta.update([["last_scraped", ""]], "A1")
    meta.update([["last_scraped", timestamp]], "A1")
    print(f"Scrape timestamp written: {timestamp}")

if __name__ == "__main__":
    print("=" * 50)
    print("JOB DISCOVERY — STEP 1")
    print("=" * 50)

    # print("\n[1/2] Running LinkedIn scraper...")
    # run_linkedin_scraper()

    print("\n[2/2] Running career pages scraper...")
    run_career_page_scraper()

    write_scrape_timestamp()
    print("\nAll done. Check your Google Sheet for new jobs.")
