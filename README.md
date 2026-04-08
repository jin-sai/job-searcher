# Job Search Pipeline

Automated job discovery, filtering, and tracking for backend/SWE roles in Bangalore and Hyderabad.

## How It Works

```
Step 1: Scrape  →  Step 2: Filter + Resume Build  →  Step 3: Review (Dashboard)
```

1. **Scraper** fetches new job listings from company career pages and writes them to Google Sheets
2. **Filter** visits each job's detail page, scores it against your skill keywords, marks irrelevant ones as "Filtered Out", and builds a tailored PDF resume for passing jobs
3. **Dashboard** lets you review scored jobs, open URLs, and mark them as Applied or Not Interested

## Project Structure

```
job_search/
├── scraper/
│   ├── career_pages_scraper.py   # Scrapes company career pages
│   ├── linkedin_scraper.py       # LinkedIn scraper (needs li_at cookie)
│   ├── filter_jobs.py            # Scores and filters jobs by description
│   └── companies/                # One JSON config per company
├── api/
│   └── api.py                    # FastAPI backend (serves dashboard)
├── frontend/
│   ├── dashboard.html            # Private job review dashboard
│   └── public.html               # Public read-only job tracker
├── config/
│   ├── filter_config.json        # Skill scores, experience cap, global excludes
│   └── active_companies.json     # Controls which companies are scraped
├── credentials.json              # Google service account key (not committed)
├── run.py                        # Runs the scraper
├── requirements.txt
└── daily_scrape.yml              # GitHub Actions workflow
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **Google Sheets API**
3. Create a Service Account → download the JSON key → save as `credentials.json`
4. Create a Google Sheet named `"Job Search Tracker"` → share it with the service account email (Editor access)

### 3. Run the scraper

```bash
python run.py
```

Or run individual steps:

```bash
# Step 1 — Scrape jobs
python scraper/career_pages_scraper.py

# Step 2 — Filter and score jobs
python scraper/filter_jobs.py
```

### 4. Start the dashboard

```bash
python -m uvicorn api.api:app --reload --port 8001
```

Open `http://127.0.0.1:8001/dashboard` in your browser.

## Resume Builder

After a job passes filtering, `scraper/resume_builder.py` automatically generates a tailored PDF resume and saves it to `output/resumes/<Company>_<Role>_<Date>.pdf`.

**Prerequisites:**
- `master_resume.json` in the project root (fill in your data and tags)
- `pdflatex` on PATH: `apt-get install texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended`

You can also build a resume manually for any job URL:

```bash
python scraper/run_url.py <job-url> --title "Software Engineer" --company "Acme"
```

## Active Companies

Controlled by `config/active_companies.json`. Currently scraping:

| Company | Method |
|---------|--------|
| Adobe | Browser (Workday) |
| Apple | Browser (card-based) |
| Google | Browser (card-based) |
| Intuit | Browser |
| JPMC | Browser (Oracle HCM) |
| Meta | GraphQL interception |
| Microsoft | Browser (Eightfold) |
| Netflix | JSON API |
| Nutanix | Browser |
| NVIDIA | Browser (Eightfold) |
| Salesforce | Browser |

To add a new company, create a JSON config in `scraper/companies/` and add the company name to `config/active_companies.json`.

## Adding a New Company

Create `scraper/companies/yourcompany.json`:

```json
{
  "name": "Company Name",
  "url": "https://careers.company.com/jobs?location=india",
  "title_selector": "h3.job-title",
  "location_selector": ".job-location",
  "link_selector": "a.job-link",
  "link_attr": "href",
  "link_prefix": "https://careers.company.com",
  "role_keywords": ["software engineer", "backend"],
  "exclude_keywords": [],
  "location_keywords": ["bangalore", "bengaluru", "hyderabad", "remote"]
}
```

Global exclude keywords (intern, frontend, mobile, etc.) are in `config/filter_config.json` and applied automatically to all companies.

## Filtering & Scoring

Configured in `config/filter_config.json`:

- **`keyword_scores`** — each skill keyword has a weight. Jobs are scored by summing matched keywords. Score is written to the Priority column.
- **`max_experience_years`** — jobs requiring more years than this are filtered out
- **`min_skill_matches`** — minimum number of keywords that must match for a job to pass (default 1)
- **`exclude_title_keywords`** — global title-based excludes applied to all companies at scrape time
- **`exclude_description_keywords`** — description-based hard excludes applied during filtering (currently empty)

## Google Sheet Columns

| Column | Filled by |
|--------|-----------|
| Title, Company, Location, Work Mode, Job ID, URL | Scraper |
| Date Posted, Date Found, Source | Scraper |
| Status | Scraper (New) → Filter (Filtered Out) → You (Applied / Referral Requested / Not Interested) |
| Priority | Filter script (numeric score) |
| Date Applied, Resume Version, Referral | You |
| Interview Round, Interview Date, Recruiter Name/Contact | You |
| Feedback, Notes | You / Filter (filter reason) |

## GitHub Actions (Daily Automation)

1. Push this repo to a **private** GitHub repository
2. Go to **Settings → Secrets → Actions** → add secret `GOOGLE_CREDENTIALS_JSON` (paste contents of `credentials.json`)
3. The workflow in `daily_scrape.yml` runs automatically every day at 8:30 AM IST
