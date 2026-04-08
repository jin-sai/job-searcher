"""
run_jd.py — Build a tailored resume from pasted JD text (no URL needed).

Usage:
    python scraper/run_jd.py [--title "Title"] [--company "Company"]

Prompts for JD text interactively; type END on a blank line to finish.
"""

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from run_url import print_jd_keywords, run_resume_build


def main():
    args = sys.argv[1:]

    company = ""
    title   = ""
    job_id  = ""
    if "--company" in args:
        company = args[args.index("--company") + 1]
    if "--title" in args:
        title = args[args.index("--title") + 1]
    if "--job-id" in args:
        job_id = args[args.index("--job-id") + 1]

    if not company:
        company = input("Company name: ").strip() or "Unknown"
    if not title:
        title = input("Job title   : ").strip() or "Software Engineer"
    if not job_id:
        job_id = input("Job ID      (press Enter to skip): ").strip()

    print("\nPaste the job description below.")
    print("When done, type END on its own line and press Enter.\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)

    jd_text = "\n".join(lines)
    if not jd_text.strip():
        print("[ERROR] No JD text entered.")
        sys.exit(1)

    print(f"\n{'='*72}")
    print(f"  Title   : {title}")
    print(f"  Company : {company}")
    if job_id:
        print(f"  Job ID  : {job_id}")
    print(f"  JD text : {len(jd_text):,} chars")
    print(f"{'='*72}")

    print_jd_keywords(jd_text)
    run_resume_build(jd_text, title, company, job_id)

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    main()
