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
import urllib.parse
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import sys
sys.path.insert(0, str(Path(__file__).parent))
from resume_builder import build_resume

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
    """Find the highest experience requirement mentioned in text.
    Handles: 'X+ years experience', 'X-Y years exp', 'minimum X years', 'X yrs exp'.
    """
    found = []
    # Range: "8-10 years experience", "8 to 10 years exp" — use upper bound
    for m in re.finditer(r"(\d+)\s*[-–to]+\s*(\d+)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)", text, re.IGNORECASE):
        found.append(max(int(m.group(1)), int(m.group(2))))
    # Standard: "8+ years experience", "8 yrs exp"
    for m in re.finditer(r"(\d+)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)", text, re.IGNORECASE):
        found.append(int(m.group(1)))
    # Prefixed: "minimum 8 years", "at least 10 years"
    for m in re.finditer(r"(?:minimum|at\s+least|min\.?)\s+(\d+)\s*\+?\s*(?:years?|yrs?)", text, re.IGNORECASE):
        found.append(int(m.group(1)))
    return max(found) if found else None


def _kw_match(kw: str, text_lower: str) -> bool:
    """Match keyword with word boundaries to avoid partial matches (e.g. 'ios' in 'previous').
    Falls back to plain substring match for keywords containing special characters (e.g. 'c++')
    where word-boundary anchors are unreliable.
    """
    if len(kw) <= 4 or " " not in kw:
        # Word boundaries only work reliably when the keyword starts and ends with \w characters.
        # If the keyword has leading/trailing non-word chars (like c++), use substring match.
        if re.match(r"^\w", kw) and re.search(r"\w$", kw):
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
    """Flush a list of {range, values} batch updates with retry.
    Returns the worksheet (possibly a fresh reconnected instance on retry).
    """
    if not updates:
        return worksheet
    for attempt in range(retries):
        try:
            worksheet.batch_update(updates)
            return worksheet
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [WARN] Batch update failed (attempt {attempt+1}), retrying in 10s... {e}")
                time.sleep(10)
                # Re-open the sheet to get a fresh connection; return the new instance to caller
                try:
                    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
                    client = gspread.authorize(creds)
                    worksheet = client.open(SHEET_NAME).sheet1
                except Exception:
                    pass
            else:
                print(f"    [ERROR] Batch update failed permanently: {e}")
    return worksheet


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

    STATUS_COL         = _col_letter(COL["Status"])
    PRIORITY_COL       = _col_letter(COL["Priority"])
    NOTES_COL          = _col_letter(COL["Notes"])
    WORK_MODE_COL      = _col_letter(COL["Work Mode"])
    RESUME_VERSION_COL = _col_letter(COL["Resume Version"])

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
                # Wait for network to settle (SPA content loads after initial HTML)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeout:
                    pass  # fall through and grab whatever rendered
                page_text = page.inner_text("body")
            except PlaywrightTimeout:
                print(f"    [TIMEOUT] skipping")
                time.sleep(1)
                continue
            except Exception as e:
                print(f"    [ERROR] {e}")
                time.sleep(1)
                continue

            # ── Guard 1: domain redirect ─────────────────────────────────────
            # If the page redirected to a different domain (expired listing,
            # login wall, ATS redirect), the text we grabbed is not the job
            # description. Mark as Filtered Out so it doesn't loop forever.
            try:
                orig_domain  = urllib.parse.urlparse(url).netloc
                final_domain = urllib.parse.urlparse(page.url).netloc
                if orig_domain and final_domain and orig_domain != final_domain:
                    note = f"Scraper: URL redirected to {final_domain} — job may be expired or behind login"
                    print(f"    [WARN] Redirected {orig_domain} → {final_domain} — marking Need Verification")
                    pending_updates.append({"range": f"{STATUS_COL}{row_idx}", "values": [["Need Verification"]]})
                    pending_updates.append({"range": f"{NOTES_COL}{row_idx}", "values": [[note]]})
                    filtered_out += 1
                    time.sleep(1)
                    continue
            except Exception:
                pass  # malformed URL — proceed normally

            # ── Guard 2: suspiciously short page text ────────────────────────
            # < 300 chars usually means a loading skeleton, spinner, or bot
            # block. Score it anyway (maybe the job is genuinely sparse), but
            # prepend a warning to Notes so the user can verify manually.
            short_text_note = ""
            if len(page_text.strip()) < 300:
                short_text_note = f"Scraper: page text only {len(page_text.strip())} chars — score may be unreliable; verify manually. "
                print(f"    [WARN] Page text only {len(page_text.strip())} chars — score may be unreliable")

            fail_reason, priority = score_job(page_text, config)

            if fail_reason:
                if short_text_note:
                    # Page likely didn't load — don't trust the filter result; send to Need Verification
                    pending_updates.append({"range": f"{STATUS_COL}{row_idx}", "values": [["Need Verification"]]})
                    pending_updates.append({"range": f"{NOTES_COL}{row_idx}", "values": [[f"{short_text_note}Filter result unreliable: {fail_reason}"]]})
                    print(f"    ? Need Verification (short page text, filter result unreliable: {fail_reason})")
                else:
                    pending_updates.append({"range": f"{STATUS_COL}{row_idx}", "values": [["Filtered Out"]]})
                    pending_updates.append({"range": f"{NOTES_COL}{row_idx}", "values": [[f"Auto-filtered: {fail_reason}"]]})
                    print(f"    - Filtered Out: {fail_reason}")
                filtered_out += 1
            else:
                if priority:
                    pending_updates.append({"range": f"{PRIORITY_COL}{row_idx}", "values": [[priority]]})
                if short_text_note:
                    pending_updates.append({"range": f"{STATUS_COL}{row_idx}", "values": [["Need Verification"]]})
                    pending_updates.append({"range": f"{NOTES_COL}{row_idx}", "values": [[short_text_note.strip()]]})
                    print(f"    ? Need Verification (short page text) | Priority: {priority}")
                else:
                    print(f"    + Passed | Priority: {priority}")
                passed += 1

                # Build a tailored resume PDF for this job
                try:
                    job_info = {
                        "title":      title,
                        "company":    company,
                        "date_found": job.get("Date Found", ""),
                    }
                    pdf_path = build_resume(page_text, job_info)
                    print(f"    📄 Resume: {pdf_path.name}")
                except FileNotFoundError as e:
                    print(f"    [SKIP] Resume build skipped: {e}")
                except Exception as e:
                    print(f"    [WARN] Resume build failed: {e}")

            # ── Guard 3: Work Mode backfill ──────────────────────────────────
            # Discovery only checks title + location. If Work Mode is still
            # blank, try to detect it from the full job description text.
            if not job.get("Work Mode", "").strip():
                t = page_text.lower()
                if "remote" in t:
                    detected_mode = "Remote"
                elif "hybrid" in t:
                    detected_mode = "Hybrid"
                elif "on-site" in t or "onsite" in t or "on site" in t or "in-office" in t:
                    detected_mode = "On-site"
                else:
                    detected_mode = ""
                if detected_mode:
                    pending_updates.append({"range": f"{WORK_MODE_COL}{row_idx}", "values": [[detected_mode]]})
                    print(f"    ~ Work Mode backfilled from description: {detected_mode}")

            # Flush every N jobs to avoid losing progress on crashes
            if (i + 1) % FLUSH_EVERY == 0 and pending_updates:
                print(f"  [Flushing {len(pending_updates)} updates to sheet...]")
                worksheet = _flush_updates(worksheet, pending_updates)
                pending_updates = []
                time.sleep(2)

            time.sleep(1)  # be polite between page loads

        browser.close()

    # Final flush
    if pending_updates:
        print(f"  [Flushing final {len(pending_updates)} updates...]")
        _flush_updates(worksheet, pending_updates)  # return value not needed after last flush

    print(f"\nDone. {passed} passed, {filtered_out} filtered out.")


if __name__ == "__main__":
    run_filter()
