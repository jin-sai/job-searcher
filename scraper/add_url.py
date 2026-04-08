"""
add_url.py — Manually add job URLs to Google Sheets.

Scrapes each URL, runs the filter, and writes the result to the sheet.
Auto-detects title/company from the page <title>; prompts to confirm or override.

Usage:
    python scraper/add_url.py <url> [<url2> ...]
    python scraper/add_url.py --file urls.txt        # one URL per line
    python scraper/add_url.py <url> --title "Backend Engineer" --company "Acme" --location "Bangalore"

--title / --company / --location only apply when a single URL is given.
"""

import re
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).parent))
from career_pages_scraper import (
    get_sheet,
    get_existing_urls,
    normalize_url,
    detect_work_mode,
    extract_job_id,
    SHEET_HEADERS,
)
from filter_jobs import load_filter_config, score_job, COL, _col_letter
from resume_builder import build_resume

CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"
SHEET_NAME = "Job Search Tracker"

STATUS_COL         = _col_letter(COL["Status"])
PRIORITY_COL       = _col_letter(COL["Priority"])
NOTES_COL          = _col_letter(COL["Notes"])
WORK_MODE_COL      = _col_letter(COL["Work Mode"])
RESUME_VERSION_COL = _col_letter(COL["Resume Version"])


# ─── Page title parsing ───────────────────────────────────────────────────────

def parse_title_and_company(page_title: str) -> tuple[str, str]:
    """
    Try to extract job title + company name from a browser <title> tag.
    Handles common ATS / job board formats:
      "Backend Engineer at Acme | LinkedIn"
      "Acme Careers | Backend Engineer"
      "Backend Engineer - Acme - India"
      "Acme | Backend Engineer | Greenhouse"
    Returns ("", "") if nothing useful found.
    """
    # "Title at Company | ..."
    m = re.match(r"^(.+?)\s+at\s+(.+?)(?:\s*[|–\-]|$)", page_title, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    parts = [p.strip() for p in re.split(r"[|–]", page_title) if p.strip()]

    # If 3+ parts, middle part tends to be the title (Lever: "Company | Title | Location/Platform")
    if len(parts) >= 3:
        # Heuristic: shortest non-company looking part is the title
        # Try: first part as company, second as title
        return parts[1], parts[0]

    # 2 parts: could be "Title - Company" or "Company - Title"
    if len(parts) == 2:
        # Job title words tend to include "engineer", "developer", "analyst" etc.
        job_words = {"engineer", "developer", "analyst", "manager", "architect",
                     "lead", "sre", "devops", "backend", "frontend", "fullstack"}
        p0_is_title = any(w in parts[0].lower() for w in job_words)
        if p0_is_title:
            return parts[0], parts[1]
        return parts[1], parts[0]

    return "", ""


# ─── Scraping ─────────────────────────────────────────────────────────────────

def scrape_job_page(url: str, page) -> tuple[str, str, str]:
    """
    Scrapes a single job URL. Returns (page_text, page_title, final_url).
    Raises on fatal error.
    """
    page.goto(url, timeout=25000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout:
        pass
    text       = page.inner_text("body")
    page_title = page.title()
    final_url  = page.url
    return text, page_title, final_url


# ─── Sheet write ──────────────────────────────────────────────────────────────

def append_job_row(worksheet, title, company, location, work_mode, job_id,
                   url, date_found, status, priority, note):
    """Append one job row to the sheet (21 columns to match schema)."""
    row = [""] * len(SHEET_HEADERS)
    col = {h: i for i, h in enumerate(SHEET_HEADERS)}
    row[col["Title"]]       = title
    row[col["Company"]]     = company
    row[col["Location"]]    = location
    row[col["Work Mode"]]   = work_mode
    row[col["Job ID"]]      = job_id
    row[col["URL"]]         = url
    row[col["Date Found"]]  = date_found
    row[col["Source"]]      = "Manual"
    row[col["Status"]]      = status
    row[col["Priority"]]    = priority
    row[col["Notes"]]       = note
    worksheet.append_row(row, value_input_option="USER_ENTERED")


# ─── Interactive prompt ───────────────────────────────────────────────────────

def prompt_confirm(label: str, detected: str) -> str:
    """Show detected value; let user override or press Enter to accept."""
    if detected:
        resp = input(f"  {label} [{detected}]: ").strip()
        return resp if resp else detected
    return input(f"  {label}: ").strip()


# ─── Per-URL processing ───────────────────────────────────────────────────────

def process_url(url: str, worksheet, existing_urls: set, config: dict,
                browser, title_hint="", company_hint="", location_hint="") -> bool:
    """
    Full pipeline for one URL:
      1. Dedup check
      2. Scrape
      3. Prompt for metadata confirmation
      4. Filter
      5. Write to sheet + build resume
    Returns True if the job was added (new), False if skipped.
    """
    norm = normalize_url(url)
    if norm in existing_urls:
        print(f"  [SKIP] Already in sheet: {url}")
        return False

    print(f"\n{'─'*70}")
    print(f"  URL: {url}")

    # Scrape
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )
    page = context.new_page()
    try:
        page_text, page_title, final_url = scrape_job_page(url, page)
    except PlaywrightTimeout:
        print(f"  [ERROR] Timeout loading page — skipped.")
        context.close()
        return False
    except Exception as e:
        print(f"  [ERROR] {e} — skipped.")
        context.close()
        return False
    finally:
        context.close()

    # Domain redirect check
    orig_domain  = urllib.parse.urlparse(url).netloc
    final_domain = urllib.parse.urlparse(final_url).netloc
    if orig_domain and final_domain and orig_domain != final_domain:
        print(f"  [WARN] Redirected {orig_domain} → {final_domain} — job may be expired or behind login.")

    short_text = len(page_text.strip()) < 300
    if short_text:
        print(f"  [WARN] Only {len(page_text.strip())} chars of page text — may not have loaded fully.")

    # Auto-detect title/company from page <title>
    detected_title, detected_company = parse_title_and_company(page_title)
    title    = title_hint    or detected_title
    company  = company_hint  or detected_company
    location = location_hint or ""

    # Always confirm metadata interactively
    print(f"\n  Page title: {page_title!r}")
    print("  Confirm job metadata (press Enter to accept, or type new value):")
    title    = prompt_confirm("Title",    title)    or "Software Engineer"
    company  = prompt_confirm("Company",  company)  or "Unknown"
    location = prompt_confirm("Location", location)

    # Work mode
    work_mode = detect_work_mode(title, location)
    if not work_mode:
        t = page_text.lower()
        if "remote"   in t: work_mode = "Remote"
        elif "hybrid" in t: work_mode = "Hybrid"
        elif any(w in t for w in ("on-site", "onsite", "on site", "in-office")): work_mode = "On-site"

    job_id     = extract_job_id(norm, {})
    date_found = date.today().isoformat()

    # Filter
    fail_reason, priority = score_job(page_text, config)

    if fail_reason:
        if short_text:
            status = "Verify"
            note   = f"Scraper: page text only {len(page_text.strip())} chars — score unreliable. Filter result: {fail_reason}"
        else:
            status = "Filtered Out"
            note   = f"Auto-filtered: {fail_reason}"
        print(f"\n  Filter: FAIL — {fail_reason}  →  Status: {status}")
    else:
        status = "New"
        note   = ""
        if short_text:
            status = "Verify"
            note   = f"Scraper: page text only {len(page_text.strip())} chars — score may be unreliable."
        print(f"\n  Filter: PASS — Priority: {priority}  →  Status: {status}")

    # Confirm before writing
    print()
    confirm = input("  Add to sheet? (Y/n): ").strip().lower()
    if confirm == "n":
        print("  Skipped by user.")
        return False

    append_job_row(
        worksheet, title, company, location, work_mode, job_id,
        norm, date_found, status, priority, note,
    )
    existing_urls.add(norm)
    print(f"  Added: {title} @ {company}")

    # Build resume if passed
    if status in ("New",) and not fail_reason:
        try:
            job_info = {"title": title, "company": company, "date_found": date_found, "job_id": job_id}
            pdf_path = build_resume(page_text, job_info)
            print(f"  Resume: {pdf_path.name}")
        except FileNotFoundError as e:
            print(f"  [SKIP] Resume build skipped: {e}")
        except Exception as e:
            print(f"  [WARN] Resume build failed: {e}")

    return True


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    urls          = []
    title_hint    = ""
    company_hint  = ""
    location_hint = ""
    file_path     = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--file":
            file_path = args[i + 1]; i += 2
        elif a == "--title":
            title_hint = args[i + 1]; i += 2
        elif a == "--company":
            company_hint = args[i + 1]; i += 2
        elif a == "--location":
            location_hint = args[i + 1]; i += 2
        elif a.startswith("http"):
            urls.append(a); i += 1
        else:
            print(f"Unknown argument: {a}")
            sys.exit(1)

    if file_path:
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    if not urls:
        print("[ERROR] No URLs provided.")
        sys.exit(1)

    # --title/--company/--location only make sense for single-URL runs
    if len(urls) > 1 and (title_hint or company_hint or location_hint):
        print("[WARN] --title/--company/--location ignored when multiple URLs are provided.")
        title_hint = company_hint = location_hint = ""

    return urls, title_hint, company_hint, location_hint


def main():
    urls, title_hint, company_hint, location_hint = parse_args()

    print(f"\n{'='*70}")
    print(f"  add_url — {len(urls)} URL(s) to process")
    print(f"{'='*70}")

    print("\nConnecting to Google Sheet...")
    try:
        worksheet = get_sheet()
    except Exception as e:
        print(f"[ERROR] Could not connect to sheet: {e}")
        sys.exit(1)

    existing_urls = get_existing_urls(worksheet)
    config        = load_filter_config()

    added   = 0
    skipped = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for url in urls:
            ok = process_url(
                url, worksheet, existing_urls, config, browser,
                title_hint=title_hint,
                company_hint=company_hint,
                location_hint=location_hint,
            )
            if ok:
                added += 1
            else:
                skipped += 1
            time.sleep(1)
        browser.close()

    print(f"\n{'='*70}")
    print(f"  Done. {added} added, {skipped} skipped.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
