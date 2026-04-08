"""
run_sheet_resume.py — Build tailored resumes for sheet jobs that don't have one yet.

Queries the Google Sheet for rows where:
  - Status is not "Filtered Out"
  - Resume Version column is empty
  - URL column is non-empty

Scrapes each URL, builds a tailored PDF, and writes the PDF filename back to the
Resume Version column so the job isn't picked up again on the next run.
"""

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from filter_jobs import get_sheet, COL, _col_letter
from run_url import scrape_url
from resume_builder import build_resume

RESUME_VERSION_COL = _col_letter(COL["Resume Version"])


def get_jobs_without_resume(worksheet):
    """Return (row_index, row_dict) for jobs that passed filtering but have no resume."""
    all_rows = worksheet.get_all_values()
    headers  = all_rows[0]
    jobs = []
    for i, row in enumerate(all_rows[1:], start=2):
        row_dict = dict(zip(headers, row))
        status         = row_dict.get("Status", "").strip()
        resume_version = row_dict.get("Resume Version", "").strip()
        url            = row_dict.get("URL", "").strip()
        if status in ("New", "Referral Requested") and not resume_version and url:
            jobs.append((i, row_dict))
    return jobs


def main():
    print("Connecting to Google Sheet...")
    try:
        worksheet = get_sheet()
    except Exception as e:
        print(f"[ERROR] Could not connect to sheet: {e}")
        sys.exit(1)

    jobs = get_jobs_without_resume(worksheet)

    if not jobs:
        print("No jobs found that need a resume.")
        return

    print(f"\nFound {len(jobs)} job(s) without a resume:\n")
    for idx, (row_idx, job) in enumerate(jobs, 1):
        status = job.get("Status", "")
        title  = job.get("Title", "")
        co     = job.get("Company", "")
        url    = job.get("URL", "")
        print(f"  {idx:>2}. [{status}] {title} @ {co}")
        print(f"       {url[:80]}")

    print()
    confirm = input(f"Build resumes for all {len(jobs)} job(s)? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    updates = []
    ok = 0
    for row_idx, job in jobs:
        title   = job.get("Title",    "Software Engineer")
        company = job.get("Company",  "Unknown")
        url     = job.get("URL",      "")

        print(f"\n{'='*72}")
        print(f"  {title} @ {company}")

        try:
            page_text, _ = scrape_url(url)
        except Exception as e:
            print(f"  [ERROR] Scrape failed: {e}")
            continue

        job_info = {
            "title":      title,
            "company":    company,
            "date_found": job.get("Date Found", date.today().isoformat()),
        }
        try:
            pdf_path = build_resume(page_text, job_info)
            print(f"  PDF saved : {pdf_path.name}")
            updates.append({
                "range":  f"{RESUME_VERSION_COL}{row_idx}",
                "values": [[pdf_path.name]],
            })
            ok += 1
        except FileNotFoundError as e:
            print(f"  [SKIP] {e}")
        except Exception as e:
            print(f"  [ERROR] Resume build failed: {e}")

    if updates:
        print(f"\nWriting resume filenames to sheet ({len(updates)} row(s))...")
        worksheet.batch_update(updates)
        print(f"Done. {ok}/{len(jobs)} resume(s) built successfully.")
    else:
        print("\nNo resumes were built.")


if __name__ == "__main__":
    main()
