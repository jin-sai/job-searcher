"""
Job Filter — Step 2: Description-based Filtering
Reads all "New" jobs from the Google Sheet, visits each job URL,
scrapes the full page text, and applies keyword + experience filters.

Results written back to the sheet:
  - Status → "Filtered Out"  (failed a filter)
  - Status → "New"           (passed all filters, unchanged)
  - Priority → "High" / "Medium" (based on skill match count)
"""

import json
import re
import time
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── Configuration ────────────────────────────────────────────────────────────

SHEET_NAME       = "Job Search Tracker"
CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"
FILTER_CONFIG    = Path(__file__).parent.parent / "config" / "filter_config.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_HEADERS = [
    "Title", "Company", "Location", "Work Mode", "Job ID", "URL",
    "CTC Range", "Date Posted", "Date Found", "Source",
    "Status", "Priority",
    "Date Applied", "Resume Version", "Referral",
    "Interview Round", "Interview Date",
    "Recruiter Name", "Recruiter Contact",
    "Feedback", "Notes",
]

COL = {h: i + 1 for i, h in enumerate(SHEET_HEADERS)}  # 1-indexed column numbers


# ─── Sheet helpers ────────────────────────────────────────────────────────────

def get_sheet():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1


def get_new_jobs(worksheet):
    """Return list of (row_index, row_dict) for rows with Status == 'New' and no Priority score yet."""
    all_rows = worksheet.get_all_values()
    headers  = all_rows[0]
    jobs = []
    for i, row in enumerate(all_rows[1:], start=2):
        row_dict = dict(zip(headers, row))
        if row_dict.get("Status", "").strip() == "New" and not row_dict.get("Priority", "").strip():
            jobs.append((i, row_dict))
    return jobs


# ─── Filter logic ─────────────────────────────────────────────────────────────

def load_filter_config() -> dict:
    with open(FILTER_CONFIG) as f:
        return json.load(f)


def extract_max_required_years(text: str) -> int | None:
    """Find the highest 'X+ years' requirement mentioned in text."""
    matches = re.findall(r"(\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:experience|exp)", text, re.IGNORECASE)
    if matches:
        return max(int(m) for m in matches)
    return None


def _kw_match(kw: str, text_lower: str) -> bool:
    """Match keyword with word boundaries to avoid partial matches (e.g. 'ios' in 'previous')."""
    # Short keywords (<=4 chars) or keywords without spaces use word boundaries
    if len(kw) <= 4 or " " not in kw:
        return bool(re.search(r"\b" + re.escape(kw) + r"\b", text_lower))
    return kw in text_lower


def score_job(text: str, config: dict) -> tuple[str | None, str]:
    """
    Returns (fail_reason, priority).
    fail_reason is None if job passes all filters.
    priority is 'High', 'Medium', or '' based on weighted keyword score.
    """
    text_lower = text.lower()

    # 1. Hard exclude keywords in description
    for kw in config.get("exclude_description_keywords", []):
        if _kw_match(kw.lower(), text_lower):
            return f"excluded keyword: '{kw}'", ""

    # 2. Experience cap
    max_years = extract_max_required_years(text)
    if max_years is not None and max_years > config.get("max_experience_years", 7):
        return f"requires {max_years}+ years experience", ""

    # 3. Weighted skill score
    keyword_scores = config.get("keyword_scores", {})
    min_matches    = config.get("min_skill_matches", 1)

    matched = {kw: score for kw, score in keyword_scores.items() if _kw_match(kw.lower(), text_lower)}

    if len(matched) < min_matches:
        return f"no skill match", ""

    total = sum(matched.values())

    return None, str(total)


# ─── Batch sheet writer ───────────────────────────────────────────────────────

# Column letters for batch_update (1-indexed → A=1, B=2 … U=21)
_COL_LETTER = {i+1: chr(65+i) for i in range(26)}

def _col_letter(col_num: int) -> str:
    if col_num <= 26:
        return chr(64 + col_num)
    return chr(64 + (col_num - 1) // 26) + chr(65 + (col_num - 1) % 26)


def _flush_updates(worksheet, updates: list, retries: int = 3):
    """Flush a list of {range, values} batch updates with retry."""
    if not updates:
        return
    # Re-create a fresh gspread client on retry to avoid stale connections
    for attempt in range(retries):
        try:
            worksheet.batch_update(updates)
            return
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [WARN] Batch update failed (attempt {attempt+1}), retrying in 10s... {e}")
                time.sleep(10)
                # Re-open the sheet to get a fresh connection
                try:
                    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
                    client = gspread.authorize(creds)
                    worksheet = client.open(SHEET_NAME).sheet1
                except Exception:
                    pass
            else:
                print(f"    [ERROR] Batch update failed permanently: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_filter():
    worksheet  = get_sheet()
    config     = load_filter_config()
    jobs       = get_new_jobs(worksheet)

    print(f"Found {len(jobs)} 'New' jobs to filter.\n")

    if not jobs:
        print("Nothing to filter.")
        return

    filtered_out = 0
    passed       = 0
    pending_updates: list = []
    FLUSH_EVERY = 10  # flush to sheet every N jobs

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

        for i, (row_idx, job) in enumerate(jobs):
            url     = job.get("URL", "").strip()
            title   = job.get("Title", "").strip()
            company = job.get("Company", "").strip()

            if not url:
                continue

            print(f"  [{row_idx}] {company} - {title}")

            # Fetch page text
            page_text = ""
            try:
                page.goto(url, timeout=25000)
                page.wait_for_timeout(3000)
                page_text = page.inner_text("body")
            except PlaywrightTimeout:
                print(f"    [TIMEOUT] skipping")
                time.sleep(1)
                continue
            except Exception as e:
                print(f"    [ERROR] {e}")
                time.sleep(1)
                continue

            fail_reason, priority = score_job(page_text, config)

            if fail_reason:
                pending_updates.append({"range": f"K{row_idx}", "values": [["Filtered Out"]]})
                pending_updates.append({"range": f"U{row_idx}", "values": [[f"Auto-filtered: {fail_reason}"]]})
                print(f"    - Filtered Out: {fail_reason}")
                filtered_out += 1
            else:
                if priority:
                    pending_updates.append({"range": f"L{row_idx}", "values": [[priority]]})
                print(f"    + Passed | Priority: {priority}")
                passed += 1

            # Flush every N jobs to avoid losing progress on crashes
            if (i + 1) % FLUSH_EVERY == 0 and pending_updates:
                print(f"  [Flushing {len(pending_updates)} updates to sheet...]")
                _flush_updates(worksheet, pending_updates)
                pending_updates = []
                time.sleep(2)

            time.sleep(1)  # be polite between page loads

        browser.close()

    # Final flush
    if pending_updates:
        print(f"  [Flushing final {len(pending_updates)} updates...]")
        _flush_updates(worksheet, pending_updates)

    print(f"\nDone. {passed} passed, {filtered_out} filtered out.")


if __name__ == "__main__":
    run_filter()
