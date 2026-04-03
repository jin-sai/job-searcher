"""
Job Discovery Runner — Step 1
Runs LinkedIn scraper + career pages scraper in sequence.
Schedule this with cron or GitHub Actions to run daily.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scraper"))

# from linkedin_scraper import run_linkedin_scraper
from career_pages_scraper import run_career_page_scraper

if __name__ == "__main__":
    print("=" * 50)
    print("JOB DISCOVERY — STEP 1")
    print("=" * 50)

    # print("\n[1/2] Running LinkedIn scraper...")
    # run_linkedin_scraper()

    print("\n[2/2] Running career pages scraper...")
    run_career_page_scraper()

    print("\nAll done. Check your Google Sheet for new jobs.")
