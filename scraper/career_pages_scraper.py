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
    """Strip query parameters and normalize known ATS domain migrations."""
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc
    # Greenhouse migrated from boards.greenhouse.io → job-boards.greenhouse.io
    if netloc == "boards.greenhouse.io":
        netloc = "job-boards.greenhouse.io"
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc, query="", fragment=""))


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
             'Posted 30+ Days Ago', '27 Mar 2026', '2026-03-27', 'April 3, 2026'
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
    # Absolute date formats: short and full month names, both orderings
    for fmt in ("%d %b %Y", "%b %d, %Y", "%d %B %Y", "%B %d, %Y", "%Y-%m-%d"):
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

def scrape_company_graphql(company: dict, existing_urls: set, worksheet) -> tuple[int, int]:
    """Load a page via Playwright, intercept the GraphQL response, and extract jobs."""
    found = 0
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

    request_override = company.get("graphql_request_override")  # e.g. {"limit": 100}

    def handle_route(route):
        """Intercept matching POST requests and merge in override fields."""
        if graphql_endpoint in route.request.url and request_override:
            try:
                post_data = json.loads(route.request.post_data or "{}")
                post_data.update(request_override)
                route.continue_(post_data=json.dumps(post_data))
            except Exception:
                route.continue_()
        else:
            route.continue_()

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
            if request_override:
                page.route("**/*", handle_route)
            page.on("response", handle_response)
            page.goto(company["graphql_url"], timeout=30000)
            page.wait_for_timeout(5000)  # Allow GraphQL response to arrive
            browser.close()

        jobs = intercepted.get("jobs", [])
        if not jobs:
            print(f"  [WARN] No jobs intercepted from GraphQL response.")
            return 0

        location_key = company.get("graphql_location_key", "locations")
        url_key      = company.get("graphql_url_key")   # field containing relative/absolute URL
        raw_fields   = company.get("graphql_raw_fields", False)  # True when fields are {raw: value} dicts

        def unwrap(val):
            """Extract .raw value if field is an App Search {raw: value} dict."""
            if raw_fields and isinstance(val, dict):
                return val.get("raw", "")
            return val

        location_subkey = company.get("graphql_location_subkey")  # e.g. "city" to extract dict field

        for job in jobs:
            title   = str(unwrap(job.get("title", ""))).strip()
            loc_val = unwrap(job.get(location_key, []))
            if loc_val is None:
                loc_val = []
            if location_subkey and isinstance(loc_val, dict):
                location = loc_val.get(location_subkey, "")
            elif isinstance(loc_val, list):
                location = ", ".join(loc_val)
            else:
                location = str(loc_val)

            if not title:
                continue
            if not is_matching_role(title, company):
                continue
            if not is_matching_location(location, company):
                continue

            found += 1

            if url_key:
                url_path = str(unwrap(job.get(url_key, ""))).strip().lstrip("/")
                href = normalize_url(job_url_prefix + url_path)
                job_id = extract_job_id(href, company)
            else:
                job_id = str(unwrap(job.get("id", ""))).strip()
                if not job_id:
                    continue
                href = normalize_url(job_url_prefix + job_id + "/")

            if not href or href in existing_urls:
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
            print(f"  + {title} ({location}){' | ' + work_mode if work_mode else ''}{' | ID: ' + job_id if job_id else ''}")

    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return found, added


# ─── POST API Scraper (mynexthire / custom POST endpoints) ───────────────────

def scrape_company_post_api(company: dict, existing_urls: set, worksheet) -> tuple[int, int]:
    """Fetch jobs via a POST JSON API with custom headers and body."""
    found = 0
    added = 0
    print(f"\nScraping {company['name']} (POST API)...")

    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        headers.update(company.get("post_api_headers", {}))

        method = company.get("post_api_method", "POST").upper()
        body   = json.dumps(company.get("post_api_body", {})).encode() if method != "GET" else None
        req    = urllib.request.Request(
            company["post_api_url"],
            data=body,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        # Traverse nested key path to reach jobs list
        jobs: list = data
        for key in company.get("post_api_jobs_key", []):
            jobs = jobs[key]

        title_key    = company.get("post_api_title_key", "title")
        location_key = company.get("post_api_location_key", "location")
        id_key       = company.get("post_api_id_key")
        date_key     = company.get("post_api_date_key")
        field_filter = company.get("post_api_field_filter", {})

        for job in jobs:
            # Field-level filter (e.g. buName = "Technology")
            if field_filter and any(job.get(k) != v for k, v in field_filter.items()):
                continue

            title    = str(job.get(title_key, "") or "").strip()
            loc_raw  = job.get(location_key, "")
            if isinstance(loc_raw, str):
                location = loc_raw.strip()
            elif isinstance(loc_raw, list):
                location = ", ".join(str(x) for x in loc_raw).strip()
            elif isinstance(loc_raw, dict):
                location = ", ".join(str(v) for v in loc_raw.values() if v).strip()
            else:
                location = str(loc_raw or "").strip()

            if not title:
                continue
            if not is_matching_role(title, company):
                continue
            if not is_matching_location(location, company):
                continue

            found += 1

            # Build URL — hash-based SPAs must NOT be normalize_url'd
            if id_key:
                job_id = str(job.get(id_key, "")).strip()
                href   = company["job_url_prefix"] + job_id
            else:
                href   = normalize_url(str(job.get("url", "")).strip())
                job_id = extract_job_id(href, company)

            if not href or href in existing_urls:
                continue

            date_posted = ""
            if date_key and job.get(date_key):
                date_posted = str(job[date_key])[:10]  # "2026-04-03T14:57:23+0000" → "2026-04-03"

            work_mode = detect_work_mode(title, location)

            row = [
                title,
                company["name"],
                location,
                work_mode,
                job_id,
                href,
                "",   # CTC
                date_posted,
                datetime.today().strftime("%Y-%m-%d"),
                "Career Page",
                "New", "", "", "", "", "", "", "", "", "", "",
            ]
            worksheet.append_row(row)
            existing_urls.add(href)
            added += 1
            print(f"  + {title} ({location}){' | ' + work_mode if work_mode else ''}{' | ID: ' + job_id if job_id else ''}")

    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return found, added


# ─── RippleHire Scraper (XML POST + pagination) ──────────────────────────────

def scrape_company_ripplehire(company: dict, existing_urls: set, worksheet) -> tuple[int, int]:
    """Fetch jobs from RippleHire-powered career pages via paginated XML POST."""
    import xml.etree.ElementTree as ET

    found = 0
    added = 0
    print(f"\nScraping {company['name']} (RippleHire)...")

    token      = company["ripplehire_token"]
    acc        = company["ripplehire_acc"]
    domain     = company["ripplehire_domain"]
    location   = company.get("ripplehire_location", "")
    url_prefix = company["job_url_prefix"]

    page_num  = 0
    page_size = 50
    total     = None

    try:
        while True:
            params = json.dumps({
                "page": page_num,
                "search": "*:*",
                "token": token,
                "source": "CAREERSITE",
                "pagesize": page_size,
                "location": location,
                "acc": acc,
            })
            post_data = urllib.parse.urlencode({"careerSiteUrlParams": params, "lang": "en"}).encode()
            req = urllib.request.Request(
                f"https://{domain}/candidate/candidatejobsearch",
                data=post_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://{domain}/candidate/?token={token}&lang=en&source=CAREERSITE",
                    "User-Agent": "Mozilla/5.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())

            if total is None:
                total = int(root.findtext("totalJobCount") or 0)

            jobs = root.findall("jobVoList/jobVoList")
            if not jobs:
                break

            for job in jobs:
                title    = (job.findtext("jobTitle") or "").strip()
                location_text = (job.findtext("locations") or job.findtext("jobLocation") or "").strip()
                job_seq  = (job.findtext("jobSeq") or "").strip()

                if not title or not job_seq:
                    continue
                if not is_matching_role(title, company):
                    continue
                if not is_matching_location(location_text, company):
                    continue

                found += 1

                href = url_prefix + job_seq
                if href in existing_urls:
                    continue

                work_mode = detect_work_mode(title, location_text)
                row = [
                    title, company["name"], location_text, work_mode, job_seq, href,
                    "", "", datetime.today().strftime("%Y-%m-%d"), "Career Page",
                    "New", "", "", "", "", "", "", "", "", "", "",
                ]
                worksheet.append_row(row)
                existing_urls.add(href)
                added += 1
                print(f"  + {title} ({location_text}){' | ' + work_mode if work_mode else ''}")

            fetched_so_far = (page_num + 1) * page_size
            # Only break on total if it was actually present in the response (non-zero means it was set)
            if total and fetched_so_far >= total:
                break
            page_num += 1

    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return found, added


# ─── Zwayam Scraper (multipart POST + pagination) ────────────────────────────

def scrape_company_zwayam(company: dict, existing_urls: set, worksheet) -> tuple[int, int]:
    """Fetch jobs from Zwayam-powered career pages via paginated multipart POST."""
    found = 0
    added = 0
    print(f"\nScraping {company['name']} (Zwayam)...")

    domain     = company["zwayam_domain"]
    company_id = company["zwayam_company_id"]
    url_prefix = company["job_url_prefix"]

    start    = 0
    has_more = True
    boundary = "----ZwayamFormBoundary"

    def make_body(pagination_start: int) -> bytes:
        filter_cri = json.dumps({
            "paginationStartNo": pagination_start,
            "selectedCall": "sort",
            "sortCriteria": {"name": "modifiedDate", "isAscending": False},
            "anyOfTheseWords": "",
        })
        parts = [("filterCri", filter_cri), ("domain", domain), ("companyId", company_id)]
        body = b""
        for name, value in parts:
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        body += f"--{boundary}--\r\n".encode()
        return body

    try:
        while has_more:
            req = urllib.request.Request(
                "https://public.zwayam.com/jobs/search",
                data=make_body(start),
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "Origin": f"https://{domain}",
                    "Referer": f"https://{domain}/",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())

            result   = data["data"]
            jobs     = result["data"]
            has_more = result.get("hasMoreData", False)
            page_size = int(result.get("facetedSearchConfig", {}).get("paginationHowMuch", 10) or 10)
            if page_size <= 0:
                page_size = 10
            start    += page_size

            for job in jobs:
                source   = job.get("_source") or {}
                title    = source.get("Requisition Title", "").strip()
                location = source.get("Location", "").strip()
                job_id   = str(job.get("_id", "")).strip()

                if not title or not job_id:
                    continue
                if not is_matching_role(title, company):
                    continue
                if not is_matching_location(location, company):
                    continue

                found += 1

                href = url_prefix + job_id
                if href in existing_urls:
                    continue

                work_mode = detect_work_mode(title, location)
                row = [
                    title, company["name"], location, work_mode, job_id, href,
                    "", "", datetime.today().strftime("%Y-%m-%d"), "Career Page",
                    "New", "", "", "", "", "", "", "", "", "", "",
                ]
                worksheet.append_row(row)
                existing_urls.add(href)
                added += 1
                print(f"  + {title} ({location}){' | ' + work_mode if work_mode else ''}")

    except Exception as e:
        print(f"  [ERROR] {company['name']}: {e}")

    return found, added


# ─── API Scraper (Eightfold / direct JSON endpoints) ─────────────────────────

def scrape_company_api(company: dict, existing_urls: set, worksheet) -> tuple[int, int]:
    """Fetch jobs via a direct JSON API (no browser needed)."""
    found = 0
    added = 0
    print(f"\nScraping {company['name']} (API)...")

    try:
        req = urllib.request.Request(
            company["api_url"],
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        jobs = data.get("positions") or data.get("jobs") or []
        if not jobs and isinstance(data, dict) and "positions" not in data and "jobs" not in data:
            print(f"  [WARN] API response has no 'positions' or 'jobs' key — top-level keys: {list(data.keys())[:10]}")

        # Optional: key name to find location inside job metadata list
        metadata_location_key = company.get("metadata_location_key")
        metadata_dept_key     = company.get("metadata_dept_key")

        for job in jobs:
            title    = str(job.get("name") or job.get("title") or "").strip()

            # Metadata-based location (e.g. Greenhouse with Job Posting Location)
            if metadata_location_key:
                locations = []
                for m in job.get("metadata", []):
                    if m.get("name") == metadata_location_key:
                        val = m.get("value", [])
                        locations = val if isinstance(val, list) else [val]
                        break
                location = ", ".join(locations)
            else:
                loc_raw  = job.get("location", "")
                location = (loc_raw.get("name", "") if isinstance(loc_raw, dict) else loc_raw).strip()

            # Metadata-based department filter (e.g. Engineering only)
            if metadata_dept_key:
                dept_filter = company.get("metadata_dept_values", [])
                job_depts = []
                for m in job.get("metadata", []):
                    if m.get("name") == metadata_dept_key:
                        val = m.get("value", [])
                        job_depts = val if isinstance(val, list) else [val]
                        break
                if dept_filter and not any(d in job_depts for d in dept_filter):
                    continue

            href_raw = job.get("absolute_url") or job.get("canonicalPositionUrl") or job.get("apply_url") or job.get("url") or ""
            href     = normalize_url(str(href_raw).strip())

            if not title or not href:
                continue
            if not is_matching_role(title, company):
                continue
            if not is_matching_location(location, company):
                continue

            found += 1

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

    return found, added


# ─── Browser Scraper ──────────────────────────────────────────────────────────

def scrape_company(page, detail_page, company: dict, existing_urls: set, worksheet) -> tuple[int, int]:
    """Scrape a single company career page and return count of new jobs added."""
    found = 0
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

        if not titles:
            print(f"  [WARN] No job title elements found — selector may be broken or page did not fully render.")

        for i, title_el in enumerate(titles):
            if title_el is None:
                continue
            title = title_el.inner_text().strip()

            if not is_matching_role(title, company):
                continue

            if card_selector:
                card = cards[i]
                if company.get("link_js"):
                    try:
                        href = card.evaluate(company["link_js"]) or ""
                    except Exception as e:
                        print(f"  [WARN] link_js eval failed on card {i}: {e}")
                        href = ""
                else:
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

            found += 1

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

    return found, added


def run_career_page_scraper():
    worksheet = get_sheet()
    existing_urls = get_existing_urls(worksheet)
    print(f"Loaded {len(existing_urls)} existing URLs from sheet.")

    companies = load_companies()
    print(f"Loaded {len(companies)} company configs from {COMPANIES_DIR}.\n")

    total_added = 0
    results = []  # (company_name, mode, found, added)

    ripplehire_companies = [c for c in companies if c.get("ripplehire_token")]
    zwayam_companies     = [c for c in companies if c.get("zwayam_domain")]
    post_api_companies   = [c for c in companies if c.get("post_api_url")]
    api_companies        = [c for c in companies if c.get("api_url")]
    graphql_companies    = [c for c in companies if c.get("graphql_url")]
    browser_companies    = [c for c in companies if not c.get("ripplehire_token") and not c.get("zwayam_domain") and not c.get("post_api_url") and not c.get("api_url") and not c.get("graphql_url")]

    # RippleHire-based companies (XML POST + pagination)
    for company in ripplehire_companies:
        found, count = scrape_company_ripplehire(company, existing_urls, worksheet)
        if found == 0:
            print(f"  [WARN] {company['name']}: 0 jobs found — API/structure may have changed")
        results.append((company["name"], "RippleHire", found, count))
        total_added += count
        time.sleep(1)

    # Zwayam-based companies (multipart POST + pagination)
    for company in zwayam_companies:
        found, count = scrape_company_zwayam(company, existing_urls, worksheet)
        if found == 0:
            print(f"  [WARN] {company['name']}: 0 jobs found — API/structure may have changed")
        results.append((company["name"], "Zwayam", found, count))
        total_added += count
        time.sleep(1)

    # POST API-based companies (no browser needed)
    for company in post_api_companies:
        found, count = scrape_company_post_api(company, existing_urls, worksheet)
        if found == 0:
            print(f"  [WARN] {company['name']}: 0 jobs found — API/structure may have changed")
        results.append((company["name"], "POST API", found, count))
        total_added += count
        time.sleep(1)

    # API-based companies (no browser needed)
    for company in api_companies:
        found, count = scrape_company_api(company, existing_urls, worksheet)
        if found == 0:
            print(f"  [WARN] {company['name']}: 0 jobs found — API/structure may have changed")
        results.append((company["name"], "API", found, count))
        total_added += count
        time.sleep(1)

    # GraphQL-based companies (Playwright + response interception)
    for company in graphql_companies:
        found, count = scrape_company_graphql(company, existing_urls, worksheet)
        if found == 0:
            print(f"  [WARN] {company['name']}: 0 jobs found — API/structure may have changed")
        results.append((company["name"], "GraphQL", found, count))
        total_added += count
        time.sleep(2)

    # Browser-based companies — group by user_agent so each UA gets its own context.
    # Companies with "user_agent": null use Playwright's default (no UA set).
    # Companies with no "user_agent" key use the global Mac Chrome UA.
    _DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _SENTINEL = object()  # distinguishes "key absent" from explicit null

    def _ua_group(c):
        v = c.get("user_agent", _SENTINEL)
        return None if v is None else (v if v is not _SENTINEL else _DEFAULT_UA)

    ua_groups: dict = {}
    for c in browser_companies:
        key = _ua_group(c)
        ua_groups.setdefault(key, []).append(c)

    if ua_groups:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for ua, group in ua_groups.items():
                ctx_kwargs = {"user_agent": ua} if ua else {}
                context     = browser.new_context(**ctx_kwargs)
                page        = context.new_page()
                detail_page = context.new_page()

                for company in group:
                    found, count = scrape_company(page, detail_page, company, existing_urls, worksheet)
                    if found == 0:
                        print(f"  [WARN] {company['name']}: 0 jobs found — selector/page may have changed")
                    results.append((company["name"], "Browser", found, count))
                    total_added += count
                    time.sleep(2)

                context.close()
            browser.close()

    # ── Per-company summary ───────────────────────────────────────────────────
    print(f"\n{'─' * 62}")
    print(f"{'Company':<28} {'Mode':<10} {'Found':>5}  {'Added':>5}  {'Status'}")
    print(f"{'─' * 62}")
    for name, mode, found, count in results:
        status = "WARN: 0 jobs" if found == 0 else "OK"
        print(f"{name:<28} {mode:<10} {found:>5}  {count:>5}  {status}")
    print(f"{'─' * 62}")
    total_found = sum(f for _, _, f, _ in results)
    print(f"{'TOTAL':<28} {'':<10} {total_found:>5}  {total_added:>5}")
    print(f"{'─' * 62}")

    print(f"\nDone. {total_added} new jobs added from career pages.")
    return results


if __name__ == "__main__":
    run_career_page_scraper()