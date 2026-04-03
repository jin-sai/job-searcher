"""
LinkedIn Job Scraper — Step 1: Job Discovery
Searches for backend/fullstack/software engineering roles in India
and writes new jobs to Google Sheets.
"""

import logging
import time
from datetime import datetime

from linkedin_jobs_scraper import LinkedinScraper
from linkedin_jobs_scraper.events import Events, EventMetrics, EventData
from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters
from linkedin_jobs_scraper.filters import (
    RelevanceFilters,
    TimeFilters,
    TypeFilters,
    ExperienceLevelFilters,
    OnSiteOrRemoteFilters,
)

import gspread
from google.oauth2.service_account import Credentials

# ─── Configuration ────────────────────────────────────────────────────────────

SHEET_NAME = "Job Search Tracker"          # Name of your Google Sheet
CREDENTIALS_FILE = "credentials.json"      # Path to your service account JSON

# Job role keywords to search on LinkedIn
SEARCH_QUERIES = [
    "Backend Developer",
    "Backend Engineer",
    "Fullstack Developer",
    "Full Stack Engineer",
    "Software Engineer Backend",
]

LOCATION = "India"

EXPERIENCE_LEVELS = [
    ExperienceLevelFilters.ASSOCIATE,
    ExperienceLevelFilters.MID_SENIOR,
]

# Roles to EXCLUDE (too junior or irrelevant)
EXCLUDE_TITLE_KEYWORDS = [
    "intern", "internship", "junior", "fresher", "trainee",
    "frontend only", "mobile", "android", "ios", "qa", "test",
]

# Max results per search query
RESULTS_PER_QUERY = 50

# ─── Google Sheets Setup ──────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_HEADERS = [
    "Title", "Company", "Location", "URL",
    "Date Found", "Source", "Status", "Notes"
]


def get_sheet():
    """Connect to Google Sheets and return the worksheet."""
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open(SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        # Create the sheet if it doesn't exist
        spreadsheet = client.create(SHEET_NAME)
        print(f"Created new spreadsheet: {SHEET_NAME}")

    # Use first worksheet, or create if empty
    worksheet = spreadsheet.sheet1

    # Write headers if sheet is empty
    if worksheet.row_count == 0 or worksheet.cell(1, 1).value != "Title":
        worksheet.clear()
        worksheet.append_row(SHEET_HEADERS)
        print("Headers written to sheet.")

    return worksheet


def get_existing_urls(worksheet):
    """Fetch all job URLs already in the sheet to avoid duplicates."""
    try:
        url_col_index = SHEET_HEADERS.index("URL") + 1  # 1-indexed
        urls = worksheet.col_values(url_col_index)
        return set(urls[1:])  # Skip header row
    except Exception:
        return set()


def write_job_to_sheet(worksheet, job: dict, existing_urls: set):
    """Append a job row to the sheet if it's not already present."""
    url = job.get("url", "")

    # Skip if already tracked
    if url in existing_urls:
        return False

    # Skip if title contains excluded keywords
    title_lower = job.get("title", "").lower()
    if any(kw in title_lower for kw in EXCLUDE_TITLE_KEYWORDS):
        print(f"  Skipped (excluded keyword): {job['title']}")
        return False

    row = [
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        url,
        datetime.today().strftime("%Y-%m-%d"),
        job.get("source", "LinkedIn"),
        "New",       # Default status — change to "Applied", "Rejected" etc.
        "",          # Notes column — empty by default
    ]

    worksheet.append_row(row)
    existing_urls.add(url)  # Update local set to prevent duplicates within same run
    return True


# ─── Scraper ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)

# Shared state across callbacks
jobs_found = []
jobs_added = 0
worksheet = None
existing_urls = set()


def on_data(data: EventData):
    """Called for every job found by the scraper."""
    global jobs_added

    job = {
        "title": data.title,
        "company": data.company,
        "location": data.location,
        "url": data.link,
        "source": "LinkedIn",
    }

    print(f"Found: {data.title} @ {data.company} — {data.location}")

    added = write_job_to_sheet(worksheet, job, existing_urls)
    if added:
        jobs_added += 1
        print(f"  Added to sheet.")


def on_metrics(metrics: EventMetrics):
    print(f"\nMetrics: {metrics}")


def on_error(error):
    print(f"[ERROR] {error}")


def run_linkedin_scraper():
    """Main entry point — runs all search queries and writes to Sheets."""
    global worksheet, existing_urls

    print("Connecting to Google Sheets...")
    worksheet = get_sheet()
    existing_urls = get_existing_urls(worksheet)
    print(f"Loaded {len(existing_urls)} existing jobs from sheet.\n")

    scraper = LinkedinScraper(
        headless=True,       # Run browser invisibly
        max_workers=1,       # One query at a time (safer)
        slow_mo=1.5,         # Delay between actions (seconds) — avoids rate limiting
        page_load_timeout=40,
    )

    scraper.on(Events.DATA, on_data)
    scraper.on(Events.METRICS, on_metrics)
    scraper.on(Events.ERROR, on_error)

    queries = []
    for keyword in SEARCH_QUERIES:
        queries.append(
            Query(
                query=keyword,
                options=QueryOptions(
                    locations=[LOCATION],
                    apply_link=False,       # Don't follow apply links
                    skip_promoted_jobs=False,
                    limit=RESULTS_PER_QUERY,
                    filters=QueryFilters(
                        relevance=RelevanceFilters.RECENT,
                        time=TimeFilters.WEEK,          # Jobs posted in last week
                        type=[TypeFilters.FULL_TIME],
                        experience=EXPERIENCE_LEVELS,
                        on_site_or_remote=[
                            OnSiteOrRemoteFilters.ON_SITE,
                            OnSiteOrRemoteFilters.REMOTE,
                            OnSiteOrRemoteFilters.HYBRID,
                        ],
                    ),
                ),
            )
        )

    print(f"Starting LinkedIn scraper for {len(queries)} queries...\n")
    scraper.run(queries)

    print(f"\nDone. {jobs_added} new jobs added to '{SHEET_NAME}'.")


if __name__ == "__main__":
    run_linkedin_scraper()
