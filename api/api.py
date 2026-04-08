"""
Job Search API — FastAPI Backend
Serves job data from Google Sheets with two access levels:
  GET /jobs         — private, full data (all columns)
  GET /jobs/public  — public, safe columns only (no resume/contact info)
  PATCH /jobs/{job_id} — update status, priority, resume link, cover letter link

Data is cached for 5 minutes to avoid hammering the Sheets API.
"""

import time
from pathlib import Path
from typing import Optional

import gspread
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.oauth2.service_account import Credentials
from pydantic import BaseModel

# ─── Config ───────────────────────────────────────────────────────────────────

SHEET_NAME       = "Job Search Tracker"
CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"
API_KEY          = "local-dev-key"   # change this to something secret
CACHE_TTL        = 300               # seconds (5 minutes)

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

# Columns exposed on the public endpoint
PUBLIC_COLUMNS = ["Title", "Company", "Location", "Work Mode", "URL", "Date Posted", "Date Found", "Status", "Priority"]

# ─── Cache ────────────────────────────────────────────────────────────────────

_cache: dict = {"data": None, "timestamp": 0}


def get_worksheet():
    creds  = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1


def load_jobs(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["data"] and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["data"]

    ws   = get_worksheet()
    rows = ws.get_all_records(numericise_ignore=["all"])

    # Add row_index so we can update specific rows
    for i, row in enumerate(rows, start=2):  # 1=header, data starts at 2
        row["_row"] = i

    _cache["data"]      = rows
    _cache["timestamp"] = now
    return rows


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Job Search API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

RESUMES_DIR = Path(__file__).parent.parent / "output" / "resumes"

@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.get("/resumes/{filename}", include_in_schema=False)
def serve_resume(
    filename: str,
    x_api_key: Optional[str] = Header(default=None),
    api_key: Optional[str] = None,   # query param fallback for direct links
):
    """Serve a resume PDF from the local resumes directory."""
    key = x_api_key or api_key
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    # Prevent path traversal
    safe_path = (RESUMES_DIR / filename).resolve()
    if not str(safe_path).startswith(str(RESUMES_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="Resume not found")
    return FileResponse(str(safe_path), media_type="application/pdf", content_disposition_type="inline")


# ─── Auth helper ──────────────────────────────────────────────────────────────

def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ─── Models ───────────────────────────────────────────────────────────────────

class JobUpdate(BaseModel):
    status:         Optional[str] = None
    priority:       Optional[str] = None
    resume_version: Optional[str] = None   # Google Drive link
    cover_letter:   Optional[str] = None   # Google Drive link (stored in Notes)
    date_applied:   Optional[str] = None
    notes:          Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/jobs")
def get_jobs_private(
    status:  Optional[str] = None,
    company: Optional[str] = None,
    x_api_key: Optional[str] = Header(default=None),
):
    """Private endpoint — returns all columns. Requires API key."""
    require_api_key(x_api_key)
    jobs = load_jobs()

    if status:
        jobs = [j for j in jobs if j.get("Status", "").lower() == status.lower()]
    if company:
        jobs = [j for j in jobs if j.get("Company", "").lower() == company.lower()]

    # Expose row number as stable unique id
    return [_with_row_id(j) for j in jobs]


@app.get("/jobs/public")
def get_jobs_public(
    company: Optional[str] = None,
):
    """Public endpoint — returns safe columns only, no auth required."""
    jobs = load_jobs()

    # Filter to only passed jobs (not filtered out)
    jobs = [j for j in jobs if j.get("Status", "") not in ("Filtered Out", "")]

    if company:
        jobs = [j for j in jobs if j.get("Company", "").lower() == company.lower()]

    return [_public_view(j) for j in jobs]


@app.patch("/jobs/{job_id}")
def update_job(
    job_id: str,
    update: JobUpdate,
    x_api_key: Optional[str] = Header(default=None),
):
    """Update a job's status, resume link, cover letter, etc."""
    require_api_key(x_api_key)

    jobs = load_jobs()
    # Primary: match by row id (stable unique identifier)
    match = next((j for j in jobs if str(j["_row"]) == job_id), None)
    if not match:
        # Legacy fallback: match by Job ID column value
        match = next((j for j in jobs if str(j.get("Job ID", "")) == job_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    row_idx = match["_row"]
    ws      = get_worksheet()

    col = {h: i + 1 for i, h in enumerate(SHEET_HEADERS)}
    updates = []

    if update.status is not None:
        updates.append({"range": f"K{row_idx}", "values": [[update.status]]})
    if update.priority is not None:
        updates.append({"range": f"L{row_idx}", "values": [[update.priority]]})
    if update.resume_version is not None:
        updates.append({"range": f"N{row_idx}", "values": [[update.resume_version]]})
    if update.date_applied is not None:
        updates.append({"range": f"M{row_idx}", "values": [[update.date_applied]]})
    if update.notes is not None:
        updates.append({"range": f"U{row_idx}", "values": [[update.notes]]})

    if updates:
        ws.batch_update(updates)
        # Invalidate cache
        _cache["data"] = None

    return {"success": True, "updated_fields": [u["range"] for u in updates]}


@app.post("/jobs/dismiss-all-new")
def dismiss_all_new(x_api_key: Optional[str] = Header(default=None)):
    """Mark all New jobs as Filtered Out in one batch."""
    require_api_key(x_api_key)
    jobs = load_jobs()
    ws   = get_worksheet()

    targets = [j for j in jobs if j.get("Status") == "New"]
    if not targets:
        return {"success": True, "updated": 0}

    updates = []
    for j in targets:
        row = j["_row"]
        updates.append({"range": f"K{row}", "values": [["Filtered Out"]]})
        updates.append({"range": f"U{row}", "values": [["Manually filtered"]]})

    ws.batch_update(updates)
    _cache["data"] = None
    return {"success": True, "updated": len(targets)}


@app.get("/companies")
def get_companies():
    """Return list of all companies in the sheet."""
    jobs = load_jobs()
    companies = sorted(set(j.get("Company", "") for j in jobs if j.get("Company")))
    return companies


@app.get("/stats")
def get_stats(x_api_key: Optional[str] = Header(default=None)):
    """Summary stats — requires API key."""
    require_api_key(x_api_key)
    jobs = load_jobs()

    total        = len(jobs)
    filtered_out = sum(1 for j in jobs if j.get("Status") == "Filtered Out")
    passed       = sum(1 for j in jobs if j.get("Status") == "New")
    applied      = sum(1 for j in jobs if j.get("Status") == "Applied")

    by_company = {}
    for j in jobs:
        c = j.get("Company", "Unknown")
        by_company[c] = by_company.get(c, 0) + 1

    return {
        "total": total,
        "filtered_out": filtered_out,
        "passed": passed,
        "applied": applied,
        "by_company": by_company,
    }


@app.get("/cache/refresh")
def refresh_cache(x_api_key: Optional[str] = Header(default=None)):
    """Force reload data from Google Sheets."""
    require_api_key(x_api_key)
    load_jobs(force=True)
    return {"success": True, "message": "Cache refreshed"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _strip_internal(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _with_row_id(job: dict) -> dict:
    d = _strip_internal(job)
    d["_id"] = job["_row"]   # expose row number as unique id
    return d


def _public_view(job: dict) -> dict:
    return {k: job.get(k, "") for k in PUBLIC_COLUMNS}
