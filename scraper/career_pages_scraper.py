"""
Career Pages Scraper — Step 1: Job Discovery
Scrapes job listings directly from company career pages
and writes new matching roles to Google Sheets.

Company configs live in the companies/ folder (one JSON file per company).
Each config defines its own role_keywords, exclude_keywords, location_keywords,
and optionally description_selector + job_id_url_pattern for richer data extraction.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── Configuration ────────────────────────────────────────────────────────────

SHEET_NAME = "Job Search Tracker"
CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"
COMPANIES_DIR = Path(__file__).parent / "companies"

# ─── Google Sheets Setup ──────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_HEADERS = [
    # Auto-filled by scraper
    "Title", "Company", "Location", "Work Mode", "Job ID", "URL",
    "CTC Range", "Date Posted", "Date Found", "Source",
    # Manual tracking
    "Status", "Priority",
    "Date Applied", "Resume Version", "Referral",
    # Interview tracking
    "Interview Round", "Interview Date",
    "Recruiter Name", "Recruiter Contact",
    "Feedback", "Notes",
]


def normalize_url(url: str) -> str:
    """Strip query parameters from a URL to get the canonical job URL."""
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def get_sheet():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    try:
        spreadsheet = client.open(SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        spreadsheet = client.create(SHEET_NAME)
    worksheet = spreadsheet.sheet1
    existing_headers = worksheet.row_values(1)
    if existing_headers != SHEET_HEADERS:
        # Update header row only — data rows are untouched
        worksheet.delete_rows(1)
        worksheet.insert_row(SHEET_HEADERS, index=1)
    return worksheet


def get_existing_urls(worksheet):
    try:
        urls = worksheet.col_values(SHEET_HEADERS.index("URL") + 1)
        return set(urls[1:])
    except Exception:
        return set()


# ─── Company Config Loader ────────────────────────────────────────────────────

ACTIVE_COMPANIES_FILE = Path(__file__).parent.parent / "config" / "active_companies.json"
FILTER_CONFIG_FILE    = Path(__file__).parent.parent / "config" / "filter_config.json"


def load_global_exclude_keywords() -> list[str]:
    """Load global title exclude keywords from filter_config.json."""
    try:
        with open(FILTER_CONFIG_FILE) as f:
            return json.load(f).get("exclude_title_keywords", [])
    except Exception:
        return []


def load_companies() -> list[dict]:
    """Load company configs from companies/ filtered by active_companies.json.
    Merges global exclude_title_keywords into each company's exclude_keywords."""
    with open(ACTIVE_COMPANIES_FILE) as f:
        active = [name.lower() for name in json.load(f)]

    global_excludes = load_global_exclude_keywords()

    companies = []
    for path in sorted(COMPANIES_DIR.glob("*.json")):
        if path.stem.lower() in active:
            with open(path) as f:
                config = json.load(f)
            # Merge global excludes with company-specific ones (deduplicated)
            company_excludes = config.get("exclude_keywords", [])
            config["exclude_keywords"] = list(dict.fromkeys(global_excludes + company_excludes))
            companies.append(config)
    return companies


# ─── Enrichment Helpers ───────────────────────────────────────────────────────

def parse_posted_date(text: str) -> str:
    """Convert relative or absolute posted text to YYYY-MM-DD.
    Handles: 'Posted Today', 'Posted Yesterday', 'Posted X Days Ago',
             'Posted 30+ Days Ago', '27 Mar 2026', '2026-03-27'
    Returns empty string if unparseable.
    """
    t = text.strip().lower()
    today = datetime.today()
    if "today" in t:
        return today.strftime("%Y-%m-%d")
    if "yesterday" in t:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    match = re.search(r"(\d+)\+?\s*days?\s*ago", t)
    if match:
        return (today - timedelta(days=int(match.group(1)))).strftime("%Y-%m-%d")
    # Absolute date formats: "27 Mar 2026" or "Mar 27, 2026"
    for fmt in ("%d %b %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def detect_work_mode(title: str, location: str) -> str:
    """Infer work mode from title and location text."""
    text = (title + " " + location).lower()
    if "remote" in text:
        return "Remote"
    if "hybrid" in text:
        return "Hybrid"
    if "on-site" in text or "onsite" in text or "on site" in text or "in-office" in text:
        return "On-site"
    return ""


def extract_job_id(url: str, company: dict) -> str:
    """Extract job ID from URL using a company-specific or generic pattern."""
    pattern = company.get("job_id_url_pattern")
    if pattern:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Generic fallback: last numeric sequence of 5+ digits in the URL path
    matches = re.findall(r"\d{5,}", url)
    return matches[-1] if matches else ""


def extract_ctc(detail_page, job_url: str, company: dict) -> str:
    """Visit the job detail page and try to extract salary/CTC info.
    Only runs if the company config includes a description_selector."""
    description_selector = company.get("description_selector")
    if not description_selector:
        return ""
    try:
        detail_page.goto(job_url, timeout=20000)
        detail_page.wait_for_timeout(2000)
        el = detail_page.query_selector(description_selector)
        if not el:
            return ""
        text = el.inner_text()
        # Salary patterns (India-focused, with USD fallback)
        patterns = [
            r"₹\s*[\d,.]+\s*[-–to]+\s*₹\s*[\d,.]+\s*(?:lpa|lakhs?|l\.p\.a\.)?",
            r"₹\s*[\d,.]+\s*(?:lpa|lakhs?|l\.p\.a\.)",
            r"[\d,.]+\s*[-–to]+\s*[\d,.]+\s*(?:lpa|lakhs?\s*per\s*annum|l\.p\.a\.)",
            r"[\d,.]+\s*(?:lpa|lakhs?\s*per\s*annum|l\.p\.a\.)",
            r"\$[\d,.]+[kK]?\s*[-–to]+\s*\$[\d,.]+[kK]?",
            r"(?:salary|ctc|compensation|pay)[:\s]+[\₹\$]?[\d,.]+[kK]?",
        ]
        for p in patterns:
            match = re.search(p, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
    except Exception:
        pass
    return ""


# ─── Matching Logic ───────────────────────────────────────────────────────────

def is_matching_role(title: str, company: dict) -> bool:
    t = title.lower()
    if any(ex in t for ex in company.get("exclude_keywords", [])):
        return False
    return any(kw in t for kw in company.get("role_keywords", []))


def is_matching_location(location: str, company: dict) -> bool:
    """Empty location_keywords means accept all locations."""
    location_keywords = company.get("location_keywords", [])
    if not location_keywords:
        return True
    return any(kw in location.lower() for kw in location_keywords)


# ─── GraphQL Scraper (Meta / intercepted browser response) ───────────────────

def scrape_company_graphql(company: dict, existing_urls: set, worksheet) -> int:
    """Load a page via Playwright, intercept the GraphQL response, and extract jobs."""
    added = 0
    print(f"\nScraping {company['name']} (GraphQL)...")

    graphql_endpoint = company["graphql_endpoint"]
    jobs_key_path    = company["graphql_jobs_key"]   # e.g. ["data", "job_search_with_featured_jobs", "all_jobs"]
    job_url_prefix   = company["job_url_prefix"]

    intercepted = {}

    def handle_response(response):
        if graphql_endpoint in response.url and not intercepted.get("done"):
            try:
                body = response.json()
                # Traverse the key path to reach the jobs list
                node = body
                for key in jobs_key_path:
                    node = node[key]
                intercepted["jobs"] = node
                intercepted["done"] = True
            except Exception:
                pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()
            page.on("response", handle_response)
            page.goto(company["graphql_url"], timeout=30000)
            page.wait_for_timeout(5000)  # Allow GraphQL response to arrive
            browser.close()

        jobs = intercepted.get("jobs", [])
        if not jobs:
            print(f"  [WARN] No jobs intercepted from GraphQL response.")
            return 0

        for job in jobs:
            job_id   = str(job.get("id", "")).strip()
            title    = job.get("title", "").strip()
            # locations is a list of strings
            locations = job.get("locations", [])
            location  = ", ".join(locations) if isinstance(locations, list) else str(locations)

            if not title or not job_id:
                continue
            if not is_matching_role(title, company):
                continue
            if not is_matching_location(location, company):
                continue

            href = normalize_url(job_url_prefix + job_id + "/")
            if href in existing_urls:
                continue

            work_mode = detect_work_mode(title, location)

            row = [
                title,
                company["name"],
                location,
                work_mode,
                job_id,
                href,
                "",   # CTC
                "",   # Date Posted
                datetime.today().strftime("%Y-%m-%d"),
                "Career Page",
                "New", "", "", "", "", "", "", "", "", "", "",
            ]
            worksheet.append_row(row)
            existing_urls.add(href)
            added += 1
            print(f"  + {title} ({location}){' | ' + work_mode if work_mode else ''} | ID: {job_id}")

    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return added


# ─── API Scraper (Eightfold / direct JSON endpoints) ─────────────────────────

def scrape_company_api(company: dict, existing_urls: set, worksheet) -> int:
    """Fetch jobs via a direct JSON API (no browser needed)."""
    added = 0
    print(f"\nScraping {company['name']} (API)...")

    try:
        req = urllib.request.Request(
            company["api_url"],
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        jobs = data.get("positions", data.get("jobs", []))

        for job in jobs:
            title    = job.get("name", job.get("title", "")).strip()
            loc_raw  = job.get("location", "")
            location = (loc_raw.get("name", "") if isinstance(loc_raw, dict) else loc_raw).strip()
            href     = normalize_url(job.get("absolute_url", job.get("canonicalPositionUrl", job.get("apply_url", job.get("url", "")))).strip())

            if not title or not href:
                continue
            if not is_matching_role(title, company):
                continue
            if not is_matching_location(location, company):
                continue
            if href in existing_urls:
                continue

            job_id    = extract_job_id(href, company)
            work_mode = detect_work_mode(title, location)

            row = [
                title,
                company["name"],
                location,
                work_mode,
                job_id,
                href,
                "",   # CTC
                "",   # Date Posted
                datetime.today().strftime("%Y-%m-%d"),
                "Career Page",
                "New", "", "", "", "", "", "", "", "", "",
            ]
            worksheet.append_row(row)
            existing_urls.add(href)
            added += 1
            print(f"  + {title} ({location}){' | ' + work_mode if work_mode else ''}{' | ID: ' + job_id if job_id else ''}")

    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return added


# ─── Browser Scraper ──────────────────────────────────────────────────────────

def scrape_company(page, detail_page, company: dict, existing_urls: set, worksheet) -> int:
    """Scrape a single company career page and return count of new jobs added."""
    added = 0
    print(f"\nScraping {company['name']}...")

    try:
        page.goto(company["url"], timeout=30000)
        page.wait_for_timeout(3000)  # Let JS render

        card_selector = company.get("card_selector")

        if card_selector:
            # Card-based mode: location/link queried within each card element
            cards        = page.query_selector_all(card_selector)
            titles       = [c.query_selector(company["title_selector"]) for c in cards]
            posted_dates = []
        else:
            cards        = None
            titles       = page.query_selector_all(company["title_selector"])
            locations    = page.query_selector_all(company["location_selector"])
            links        = page.query_selector_all(company["link_selector"])
            posted_dates = page.query_selector_all(company["date_posted_selector"]) if company.get("date_posted_selector") else []

        for i, title_el in enumerate(titles):
            if title_el is None:
                continue
            title = title_el.inner_text().strip()

            if not is_matching_role(title, company):
                continue

            if card_selector:
                card    = cards[i]
                link_el = card.query_selector(company["link_selector"])
                href    = link_el.get_attribute(company["link_attr"]) if link_el else ""
                # Support JS expression for complex location extraction
                if company.get("location_js"):
                    loc_text = card.evaluate(company["location_js"]) or ""
                else:
                    loc_el   = card.query_selector(company["location_selector"])
                    loc_text = loc_el.inner_text().strip() if loc_el else ""
                # Support JS expression for date posted extraction
                if company.get("date_posted_js"):
                    posted_text_card = card.evaluate(company["date_posted_js"]) or ""
                else:
                    posted_text_card = ""
            else:
                field_idx = company.get("location_field_index")
                if field_idx is not None:
                    loc_idx = i * (field_idx + 1) + field_idx
                    loc_text = locations[loc_idx].inner_text().strip() if loc_idx < len(locations) else ""
                elif i < len(locations):
                    loc_text = locations[i].inner_text().strip()
                else:
                    loc_text = ""
                href = links[i].get_attribute(company["link_attr"]) if i < len(links) else ""

            # Some Workday instances return "locations\nCity" — take last non-empty line
            loc_lines = [l.strip() for l in loc_text.splitlines() if l.strip()]
            location = loc_lines[-1] if loc_lines else ""

            if not is_matching_location(location, company):
                continue

            # Build full URL
            href = href or ""
            if href and not href.startswith("http"):
                href = company["link_prefix"] + href
            href = normalize_url(href)

            if not href or href in existing_urls:
                continue

            job_id    = extract_job_id(href, company)
            work_mode = detect_work_mode(title, location)
            ctc       = extract_ctc(detail_page, href, company)
            if card_selector:
                posted_text = posted_text_card
            else:
                posted_text = posted_dates[i].inner_text().strip() if i < len(posted_dates) else ""
            date_posted = parse_posted_date(posted_text)

            row = [
                title,
                company["name"],
                location,
                work_mode,
                job_id,
                href,
                ctc,
                date_posted,
                datetime.today().strftime("%Y-%m-%d"),
                "Career Page",
                "New",   # Status
                "",      # Priority
                "",      # Date Applied
                "",      # Resume Version
                "",      # Referral
                "",      # Interview Round
                "",      # Interview Date
                "",      # Recruiter Name
                "",      # Recruiter Contact
                "",      # Feedback
                "",      # Notes
            ]
            worksheet.append_row(row)
            existing_urls.add(href)
            added += 1
            print(f"  + {title} ({location}){' | ' + work_mode if work_mode else ''}{' | ID: ' + job_id if job_id else ''}")

    except PlaywrightTimeout:
        print(f"  [TIMEOUT] {company['name']} page took too long — skipping.")
    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return added


def run_career_page_scraper():
    worksheet = get_sheet()
    existing_urls = get_existing_urls(worksheet)
    print(f"Loaded {len(existing_urls)} existing URLs from sheet.")

    companies = load_companies()
    print(f"Loaded {len(companies)} company configs from {COMPANIES_DIR}.\n")

    total_added = 0

    api_companies      = [c for c in companies if c.get("api_url")]
    graphql_companies  = [c for c in companies if c.get("graphql_url")]
    browser_companies  = [c for c in companies if not c.get("api_url") and not c.get("graphql_url")]

    # API-based companies (no browser needed)
    for company in api_companies:
        count = scrape_company_api(company, existing_urls, worksheet)
        total_added += count
        time.sleep(1)

    # GraphQL-based companies (Playwright + response interception)
    for company in graphql_companies:
        count = scrape_company_graphql(company, existing_urls, worksheet)
        total_added += count
        time.sleep(2)

    # Browser-based companies
    if browser_companies:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page        = context.new_page()
            detail_page = context.new_page()

            for company in browser_companies:
                count = scrape_company(page, detail_page, company, existing_urls, worksheet)
                total_added += count
                time.sleep(2)

            browser.close()

    print(f"\nDone. {total_added} new jobs added from career pages.")


if __name__ == "__main__":
    run_career_page_scraper()