# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated job discovery pipeline for backend/fullstack/SWE roles in India. Scrapes LinkedIn and company career pages, writes results to Google Sheets, then filters them by description content. A FastAPI backend + HTML dashboard provide a read/write UI over the sheet.

## Setup & Running

```bash
pip install -r requirements.txt
playwright install chromium

# Step 1: Discover jobs (LinkedIn + career pages → Google Sheet)
python run.py

# Step 2: Filter discovered jobs by description content
python scraper/filter_jobs.py

# API server (serves dashboard at /dashboard)
uvicorn api.api:app --reload --port 8001
```

GitHub Actions runs `run.py` daily (3 AM UTC / 8:30 AM IST) via `daily_scrape.yml`.

**Required credential:** `credentials.json` (Google service account key) in the project root. In CI, written from the `GOOGLE_CREDENTIALS_JSON` secret.

## Architecture

### Step 1 — Discovery (`run.py`)
Runs two scrapers sequentially:

1. **`scraper/linkedin_scraper.py`** — `linkedin-jobs-scraper` library with event callbacks. `max_workers=1` and `slow_mo=1.5s` are intentional for rate-limit avoidance.

2. **`scraper/career_pages_scraper.py`** — Playwright-based scraper. Supports three modes per company, selected automatically based on which keys are present in the company's JSON config:
   - **`api_url`** present → direct JSON API fetch (no browser)
   - **`graphql_url`** present → Playwright loads the page and intercepts the GraphQL response
   - Neither → standard browser scraping with CSS selectors

### Step 2 — Filtering (`scraper/filter_jobs.py`)
Reads all `Status = "New"` rows with no Priority set, visits each job URL via Playwright, and applies filters from `config/filter_config.json`:
- Hard-exclude keywords in description
- Experience cap (`max_experience_years`)
- Weighted skill keyword scoring → sets `Priority` column to the numeric score; sets `Status = "Filtered Out"` on failure

After a job passes, optionally builds a tailored PDF resume via `scraper/resume_builder.py` and saves it to `output/resumes/SaiKumar_Resume_<N>.pdf`. Content is deduplicated by SHA-256 hash of the rendered `.tex` source — if an identical resume was built before, the existing file is reused. The manifest lives at `output/resumes/manifest.json`.

### API (`api/api.py`)
FastAPI app backed by the same Google Sheet. Key endpoints:
- `GET /jobs` — all columns, requires `X-API-Key` header (value: `local-dev-key`)
- `GET /jobs/public` — safe columns only, no auth
- `PATCH /jobs/{job_id}` — update status/priority/resume link/notes
- `POST /jobs/dismiss-all-new` — batch mark all `Status=New` jobs as Filtered Out in one sheet call
- `GET /stats`, `GET /companies`, `GET /cache/refresh`
- `GET /dashboard` — serves `frontend/dashboard.html`

Sheet data is cached in-process for 5 minutes (`CACHE_TTL`).

**Note:** Run on port 8001 to avoid conflicts: `uvicorn api.api:app --reload --port 8001`

## Google Sheet Schema

21 columns (order matters — scrapers write by position):

| # | Column | Notes |
|---|--------|-------|
| A | Title | |
| B | Company | |
| C | Location | |
| D | Work Mode | Remote / Hybrid / On-site (auto-detected) |
| E | Job ID | Extracted from URL |
| F | URL | Deduplication key |
| G | CTC Range | Scraped from detail page if `description_selector` set |
| H | Date Posted | |
| I | Date Found | |
| J | Source | "LinkedIn" or "Career Page" |
| K | Status | "New" → "Filtered Out" / "Applied" / etc. |
| L | Priority | Numeric score (from filter) |
| M–U | Manual tracking | Date Applied, Resume Version, Referral, Interview fields, Notes |

## Company Config Files (`scraper/companies/*.json`)

Each JSON file defines one company. Active companies are listed in `config/active_companies.json` (by filename stem, lowercase).

Required fields for **browser** mode: `name`, `url`, `title_selector`, `location_selector`, `link_selector`, `link_attr`, `link_prefix`

Optional fields: `card_selector` (card-based layout), `date_posted_selector`, `date_posted_js`, `location_js`, `description_selector` (enables CTC extraction), `job_id_url_pattern` (regex with capture group)

For **API** mode: replace the above with `api_url`.

For **GraphQL** mode: `graphql_url` (page to load), `graphql_endpoint` (URL substring to intercept), `graphql_jobs_key` (key path array into JSON), `job_url_prefix`.

All company configs also accept `role_keywords`, `exclude_keywords`, `location_keywords`. Global title exclude keywords from `config/filter_config.json` are merged into every company's `exclude_keywords` at load time.

## Frontend

- `frontend/dashboard.html` — private dashboard, served via FastAPI at `/dashboard`, requires API key for all write operations
- `frontend/public.html` — public read-only tracker, fetches data directly from Google Sheets CSV (no backend needed), hosted on GitHub Pages

## Customization Points

- **Add a company:** Create `scraper/companies/<name>.json` and add `<name>` to `config/active_companies.json`.
- **Filter tuning:** Edit `config/filter_config.json` — adjust `keyword_scores`, `max_experience_years`, `min_skill_matches`, `exclude_title_keywords`, `exclude_description_keywords`.
- **Schedule:** Edit the cron expression in `daily_scrape.yml`.
