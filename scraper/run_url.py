"""
run_url.py — Local-only script: scrape a job URL, score it, and build a tailored resume PDF.

Usage:
    python scraper/run_url.py <url> [--title "Title"] [--company "Company"]

Examples:
    python scraper/run_url.py https://careers.example.com/jobs/123
    python scraper/run_url.py https://... --title "Backend Engineer" --company "Acme"
"""

import sys
import re
import urllib.parse
from pathlib import Path
from datetime import date

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Make sibling imports work
sys.path.insert(0, str(Path(__file__).parent))
from filter_jobs import load_filter_config, score_job, extract_max_required_years, _kw_match
from resume_builder import build_resume, extract_jd_keywords


# ─── CLI arg parsing ──────────────────────────────────────────────────────────

def parse_args():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print("Usage: python scraper/run_url.py <url> [--title TITLE] [--company COMPANY]")
        sys.exit(1)

    url = args[0]
    title   = "Software Engineer"
    company = "Unknown"

    if "--title" in args:
        idx = args.index("--title")
        title = args[idx + 1]
    if "--company" in args:
        idx = args.index("--company")
        company = args[idx + 1]

    return url, title, company


# ─── Scraping ─────────────────────────────────────────────────────────────────

def scrape_url(url: str) -> tuple[str, str]:
    """
    Returns (page_text, final_url).
    Raises on timeout or fatal error.
    """
    print("\n── Step 1: Scraping page ────────────────────────────────────────────────")
    print(f"   URL: {url}")

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
        page.goto(url, timeout=25000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            pass

        text      = page.inner_text("body")
        final_url = page.url
        browser.close()

    print(f"   Final URL : {final_url}")
    print(f"   Page text : {len(text):,} chars")

    orig_domain  = urllib.parse.urlparse(url).netloc
    final_domain = urllib.parse.urlparse(final_url).netloc
    if orig_domain and final_domain and orig_domain != final_domain:
        print(f"\n   [WARN] Redirected {orig_domain} → {final_domain}")
        print("         Job may be expired or behind a login wall.")

    if len(text.strip()) < 300:
        print(f"\n   [WARN] Very short page text ({len(text.strip())} chars) — content may not have loaded.")

    return text, final_url


# ─── Scoring detail ───────────────────────────────────────────────────────────

def print_score_detail(page_text: str, config: dict):
    print("\n── Step 2: Scoring ──────────────────────────────────────────────────────")

    text_lower = page_text.lower()
    keyword_scores = config.get("keyword_scores", {})

    # Experience
    max_years = extract_max_required_years(page_text)
    max_cfg   = config.get("max_experience_years", 6)
    exp_str   = f"{max_years} yrs" if max_years is not None else "not mentioned"
    exp_ok    = max_years is None or max_years <= max_cfg
    print(f"   Experience cap  : {max_cfg} yrs  |  JD requires: {exp_str}  {'✓' if exp_ok else '✗ FAIL'}")

    # Exclude keywords
    excluded = [kw for kw in config.get("exclude_description_keywords", []) if _kw_match(kw.lower(), text_lower)]
    if excluded:
        print(f"   Excluded kws    : {', '.join(excluded)}  ✗ FAIL")
    else:
        print(f"   Excluded kws    : none  ✓")

    # Keyword hits
    matched = {kw: score for kw, score in keyword_scores.items() if _kw_match(kw.lower(), text_lower)}
    missed  = {kw: score for kw, score in keyword_scores.items() if kw not in matched}

    print(f"\n   Matched keywords ({len(matched)}/{len(keyword_scores)}):")
    if matched:
        for kw, score in sorted(matched.items(), key=lambda x: -x[1]):
            print(f"     {score:>4}  {kw}")
    else:
        print("     (none)")

    total = sum(matched.values())
    thresholds = config.get("priority_thresholds", {"High": 25, "Medium": 10})
    if total >= thresholds.get("High", 25):
        label = "High"
    elif total >= thresholds.get("Medium", 10):
        label = "Medium"
    else:
        label = "Low"

    print(f"\n   Total score : {total}  →  Priority: {label}")

    if missed:
        print(f"\n   Missed keywords ({len(missed)}):")
        for kw, score in sorted(missed.items(), key=lambda x: -x[1]):
            print(f"     {score:>4}  {kw}")

    # Pass/fail
    fail_reason, priority = score_job(page_text, config)
    print(f"\n   Filter result   : {'PASS  (Priority: ' + priority + ')' if not fail_reason else 'FAIL  — ' + fail_reason}")

    return fail_reason, priority


# ─── Resume build ─────────────────────────────────────────────────────────────

def print_jd_keywords(page_text: str):
    print("\n── Step 3: JD keyword extraction ───────────────────────────────────────")
    freq, high_priority = extract_jd_keywords(page_text)

    top = sorted(freq.items(), key=lambda x: -x[1])[:30]
    print(f"   Top 30 JD keywords (freq):")
    for kw, count in top:
        hp = " *" if kw in high_priority else ""
        print(f"     {count:>3}x  {kw}{hp}")
    print("   (* = high-priority: freq ≥ 3 or in tech keyword list)")


def run_resume_build(page_text: str, title: str, company: str):
    print("\n── Step 4: Building tailored resume ────────────────────────────────────")
    job_info = {
        "title":      title,
        "company":    company,
        "date_found": date.today().isoformat(),
        "job_id":     "",
    }
    try:
        pdf_path = build_resume(page_text, job_info)
        print(f"   PDF saved : {pdf_path}")
    except FileNotFoundError as e:
        print(f"   [SKIP] {e}")
    except RuntimeError as e:
        print(f"   [ERROR] {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    url, title, company = parse_args()

    print(f"\n{'='*72}")
    print(f"  Job URL : {url}")
    print(f"  Title   : {title}")
    print(f"  Company : {company}")
    print(f"{'='*72}")

    # Step 1: scrape
    page_text, _ = scrape_url(url)

    # Step 2: score
    config = load_filter_config()
    fail_reason, priority = print_score_detail(page_text, config)

    # Step 3: JD keywords (always shown, useful even if filtered out)
    print_jd_keywords(page_text)

    # Step 4: resume (only if passed)
    if not fail_reason:
        run_resume_build(page_text, title, company)
    else:
        print(f"\n── Step 4: Skipping resume build (job filtered out) ─────────────────────")

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    main()
