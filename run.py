"""
Job Discovery Runner — Step 1
Runs LinkedIn scraper + career pages scraper in sequence.
Schedule this with cron or GitHub Actions to run daily.
"""

import os
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

def write_github_summary(results):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    total_found = sum(f for _, _, f, _ in results)
    total_added = sum(c for _, _, _, c in results)
    with open(summary_path, "a") as f:
        f.write("## Job Scrape Results\n\n")
        f.write("| Company | Mode | Found | Added | Status |\n")
        f.write("|---|---|---|---|---|\n")
        for name, mode, found, count in results:
            status = "**⚠ WARN**" if found == 0 else "OK"
            f.write(f"| {name} | {mode} | {found} | {count} | {status} |\n")
        f.write(f"\n**Total:** {total_found} found, {total_added} new jobs added\n")


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
    results = run_career_page_scraper()

    write_scrape_timestamp()
    write_github_summary(results)
    print("\nAll done. Check your Google Sheet for new jobs.")
