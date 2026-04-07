"""
Resume Builder — Step 3 (optional): Tailored PDF resume per passing job.

Given a job's description text and basic info (title, company), this module:
  1. Extracts keywords from the JD (frequency + curated tech list)
  2. Scores every bullet in master_resume.json by tag overlap
  3. Selects the best bullets per experience entry and top 3 projects
  4. Renders a .tex file via Jinja2 (Jake's Resume template)
  5. Compiles to PDF with pdflatex
  6. Uploads the PDF to a Google Drive folder ("Tailored Resumes")
  7. Returns the Drive shareable link

Called from filter_jobs.py after a job passes all filters.
Fails gracefully — a resume build failure never crashes the filter run.

Prerequisites:
  - master_resume.json in project root (fill in your data + tags)
  - pdflatex on PATH  (apt-get install texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended)
  - credentials.json  (same Google service account used by the sheet)
  - pip: jinja2, google-api-python-client, google-auth-httplib2
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR             = Path(__file__).parent.parent
MASTER_RESUME_PATH   = BASE_DIR / "master_resume.json"
TECH_KEYWORDS_PATH   = BASE_DIR / "config" / "tech_keywords.json"
TEMPLATE_DIR         = BASE_DIR / "resume"
CREDENTIALS_FILE     = BASE_DIR / "credentials.json"
OUTPUT_DIR           = BASE_DIR / "output" / "resumes"
DRIVE_FOLDER_NAME    = "Tailored Resumes"

# ─── Stop words ───────────────────────────────────────────────────────────────

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "at", "to",
    "for", "of", "with", "is", "are", "was", "were", "be", "been", "have",
    "has", "will", "would", "could", "should", "may", "might", "do", "does",
    "did", "from", "by", "this", "that", "these", "those", "we", "our", "you",
    "your", "i", "it", "its", "not", "no", "so", "up", "out", "about", "what",
    "which", "who", "how", "when", "where", "all", "any", "each", "other",
    "their", "they", "them", "than", "then", "also", "into", "through",
    "during", "before", "after", "above", "below", "between", "such", "while",
    "although", "however", "therefore", "thus", "as", "well", "can", "us",
    "must", "role", "work", "team", "join", "help", "use", "using", "used",
    "build", "building", "built", "develop", "developing", "developed",
    "experience", "years", "year", "looking", "strong", "good", "excellent",
    "ability", "knowledge", "skills", "skill", "understanding", "working",
    "including", "etc", "e.g", "i.e", "plus", "bonus", "nice",
}


# ─── LaTeX helpers ────────────────────────────────────────────────────────────

def latex_escape(text: str) -> str:
    """Escape special LaTeX characters in user-supplied strings."""
    # Backslash must come first to avoid double-escaping
    text = text.replace("\\", r"\textbackslash{}")
    text = text.replace("&",  r"\&")
    text = text.replace("%",  r"\%")
    text = text.replace("$",  r"\$")
    text = text.replace("#",  r"\#")
    text = text.replace("_",  r"\_")
    text = text.replace("{",  r"\{")
    text = text.replace("}",  r"\}")
    text = text.replace("~",  r"\textasciitilde{}")
    text = text.replace("^",  r"\textasciicircum{}")
    return text


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        # Custom delimiters — none of these appear in LaTeX source
        block_start_string="((*",
        block_end_string="*))",
        variable_start_string="((",
        variable_end_string="))",
        comment_start_string="((#",   # override default {# which clashes with LaTeX \newcommand{#1}
        comment_end_string="#))",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )
    env.filters["le"] = latex_escape
    return env


# ─── Keyword extraction ───────────────────────────────────────────────────────

def _load_tech_keywords() -> set[str]:
    if not TECH_KEYWORDS_PATH.exists():
        return set()
    return {k.lower() for k in json.loads(TECH_KEYWORDS_PATH.read_text())}


def extract_jd_keywords(jd_text: str) -> tuple[dict[str, int], set[str]]:
    """
    Returns:
        freq          — {word: count} for all non-stop words in the JD
        high_priority — words appearing 3+ times OR in the curated tech list
    """
    tech_set = _load_tech_keywords()

    # Tokenise: keep alphanumeric runs plus common tech chars (+, #, .)
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#._-]*", jd_text.lower())

    freq: dict[str, int] = {}
    for tok in tokens:
        if tok not in STOPWORDS and len(tok) > 1:
            freq[tok] = freq.get(tok, 0) + 1

    # Also check multi-word tech terms (scan raw text once)
    jd_lower = jd_text.lower()
    for kw in tech_set:
        if " " in kw and kw in jd_lower:
            freq[kw] = freq.get(kw, 0) + jd_lower.count(kw)

    high_priority: set[str] = {
        w for w, c in freq.items()
        if c >= 3 or w in tech_set
    }

    return freq, high_priority


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _score_bullet(bullet: dict, freq: dict[str, int], high_priority: set[str]) -> float:
    """
    Score = sum of JD frequencies for each tag that appears in the JD,
    plus a bonus for high-priority (common or tech-list) matches.
    """
    score = 0.0
    for tag in bullet.get("tags", []):
        tag_lower = tag.lower()
        if tag_lower in freq:
            score += freq[tag_lower]
            if tag_lower in high_priority:
                score += 2.0   # bonus for high-signal matches
    return score


def _select_bullets(
    bullets: list[dict],
    freq: dict[str, int],
    high_priority: set[str],
    min_bullets: int = 2,
    max_bullets: int = 5,
) -> list[dict]:
    scored = sorted(
        [(b, _score_bullet(b, freq, high_priority)) for b in bullets],
        key=lambda x: x[1],
        reverse=True,
    )
    # Take bullets with score > 0, up to max; always keep at least min
    positive = [b for b, s in scored if s > 0][:max_bullets]
    if len(positive) < min_bullets:
        positive = [b for b, _ in scored[:min_bullets]]
    return positive


# ─── Summary builder ──────────────────────────────────────────────────────────

_DOMAIN_SIGNALS: list[tuple[str, list[str]]] = [
    ("full-stack", ["fullstack", "full-stack", "full stack", "react", "angular", "vue", "frontend", "nodejs"]),
    ("backend", ["backend", "microservices", "spring boot", "api", "server-side", "java", "golang"]),
    ("data engineering", ["spark", "airflow", "flink", "etl", "data engineering", "databricks"]),
    ("platform / SRE", ["sre", "platform", "devops", "infrastructure", "terraform"]),
]


def _build_summary(top_tags: list[str], job_info: dict, years_exp: int) -> str:
    domain = "software"
    for d, signals in _DOMAIN_SIGNALS:
        if any(s in top_tags for s in signals):
            domain = d
            break

    skip = {"backend", "frontend", "full-stack", "fullstack", "api", "server-side",
            "senior", "lead", "sre", "platform", "devops", "infrastructure"}
    skills = [t.title() for t in top_tags if t.lower() not in skip][:3]
    skills_str = ", ".join(skills) if skills else "distributed systems and cloud infrastructure"

    role    = job_info.get("title", "Software Engineer")
    company = job_info.get("company", "your team")

    return (
        f"{years_exp}+ year {domain} engineer with expertise in {skills_str}, "
        f"seeking to contribute to {role} at {company}."
    )


# ─── Resume selection ─────────────────────────────────────────────────────────

def select_resume(master: dict, freq: dict[str, int], high_priority: set[str], job_info: dict) -> dict:
    """Pick the best bullets from master_resume.json for this JD."""

    # Experience: select bullets per role
    experience = []
    for exp in master.get("experience", []):
        selected = _select_bullets(exp["bullets"], freq, high_priority)
        experience.append({**exp, "bullets": selected})

    # Projects: score each project by aggregate tag frequency, keep top 3
    project_scores = []
    for proj in master.get("projects", []):
        proj_score = sum(
            freq.get(t.lower(), 0) + (2 if t.lower() in high_priority else 0)
            for t in proj.get("tags", [])
        )
        selected = _select_bullets(proj.get("bullets", []), freq, high_priority,
                                   min_bullets=1, max_bullets=3)
        project_scores.append((proj, proj_score, selected))

    project_scores.sort(key=lambda x: x[1], reverse=True)
    projects = [{**p, "bullets": sb} for p, _, sb in project_scores[:3]]

    # Derive top tags for summary (rank by freq of all bullet tags across experience)
    tag_freq: dict[str, float] = {}
    for exp in master.get("experience", []):
        for b in exp["bullets"]:
            for tag in b.get("tags", []):
                tag_lower = tag.lower()
                tag_freq[tag_lower] = tag_freq.get(tag_lower, 0) + freq.get(tag_lower, 0)
    top_tags = sorted(tag_freq, key=tag_freq.get, reverse=True)  # type: ignore[arg-type]

    summary = master.get("summary_override") or _build_summary(top_tags, job_info, master.get("years_experience", 4))

    return {
        "name":       master["name"],
        "email":      master["email"],
        "phone":      master["phone"],
        "linkedin":   master.get("linkedin", ""),
        "github":     master.get("github", ""),
        "portfolio":  master.get("portfolio", ""),
        "location":   master.get("location", ""),
        "summary":    summary,
        "education":  master.get("education", []),
        "experience": experience,
        "projects":   projects,
        "skills":     master.get("skills", {}),
    }


# ─── Rendering & compilation ──────────────────────────────────────────────────

def render_tex(tailored: dict) -> str:
    """Render the Jinja2 LaTeX template with the tailored resume data."""
    env = _jinja_env()
    template = env.get_template("resume_template.tex.j2")
    return template.render(**tailored)


def compile_pdf(tex_content: str, output_path: Path) -> None:
    """Write tex_content to a temp dir, compile with pdflatex, copy PDF out."""
    _MIKTEX_PATH = r"C:\Users\saiku\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"
    pdflatex_cmd = shutil.which("pdflatex") or (
        _MIKTEX_PATH if Path(_MIKTEX_PATH).exists() else None
    )
    if not pdflatex_cmd:
        raise RuntimeError(
            "pdflatex not found — install TeX Live: "
            "sudo apt-get install texlive-latex-base texlive-latex-recommended "
            "texlive-latex-extra texlive-fonts-recommended"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_file = Path(tmpdir) / "resume.tex"
        tex_file.write_text(tex_content, encoding="utf-8")

        result = subprocess.run(
            [pdflatex_cmd, "-interaction=nonstopmode", "resume.tex"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        pdf_src = Path(tmpdir) / "resume.pdf"
        if not pdf_src.exists():
            # Surface the first ERROR line from the log to ease debugging
            log = result.stdout or ""
            error_line = next(
                (ln for ln in log.splitlines() if ln.startswith("!")), ""
            )
            raise RuntimeError(f"pdflatex failed. {error_line}\n{log[-1000:]}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(pdf_src), str(output_path))


# ─── Google Drive upload ──────────────────────────────────────────────────────

_DRIVE_FOLDER_ID_CACHE: str | None = None   # module-level cache for the run


def _get_drive_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build  # type: ignore

    creds = Credentials.from_service_account_file(
        str(CREDENTIALS_FILE),
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    return build("drive", "v3", credentials=creds)


def _get_or_create_folder(service) -> str:
    global _DRIVE_FOLDER_ID_CACHE
    if _DRIVE_FOLDER_ID_CACHE:
        return _DRIVE_FOLDER_ID_CACHE

    q = (
        f"name='{DRIVE_FOLDER_NAME}' "
        "and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    results = service.files().list(q=q, fields="files(id)").execute()
    files = results.get("files", [])

    if files:
        _DRIVE_FOLDER_ID_CACHE = files[0]["id"]
    else:
        folder = service.files().create(
            body={
                "name": DRIVE_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        ).execute()
        _DRIVE_FOLDER_ID_CACHE = folder["id"]

    return _DRIVE_FOLDER_ID_CACHE


def upload_to_drive(pdf_path: Path, filename: str) -> str:
    """Upload PDF to Drive folder, return the webViewLink."""
    from googleapiclient.http import MediaFileUpload  # type: ignore

    service   = _get_drive_service()
    folder_id = _get_or_create_folder(service)

    media = MediaFileUpload(str(pdf_path), mimetype="application/pdf")
    uploaded = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()

    # Make the file readable by anyone with the link
    service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return uploaded.get("webViewLink", "")


# ─── Public entry point ───────────────────────────────────────────────────────

def build_resume(jd_text: str, job_info: dict) -> tuple[str, Path]:
    """
    Build a tailored resume PDF for one passing job.

    Args:
        jd_text:  Full text of the job description page.
        job_info: Dict with at least "title", "company", "date_found".

    Returns:
        (drive_link, local_pdf_path)
        drive_link is "" when credentials.json is absent (local dev without Drive).

    Raises:
        FileNotFoundError  — master_resume.json missing
        RuntimeError       — pdflatex missing or compile failure
    """
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError(
            "master_resume.json not found — create it from the template in the repo root."
        )

    master = json.loads(MASTER_RESUME_PATH.read_text(encoding="utf-8"))
    freq, high_priority = extract_jd_keywords(jd_text)
    tailored = select_resume(master, freq, high_priority, job_info)
    tex_content = render_tex(tailored)

    # Build a filesystem-safe filename
    safe = lambda s: re.sub(r"[^\w]", "_", s or "")
    company  = safe(job_info.get("company", "unknown"))[:30]
    role     = safe(job_info.get("title", "role"))[:30]
    date_str = (job_info.get("date_found") or "")[:10].replace("-", "")
    filename = f"{company}_{role}_{date_str}.pdf"

    pdf_path = OUTPUT_DIR / filename
    compile_pdf(tex_content, pdf_path)

    drive_link = ""
    if CREDENTIALS_FILE.exists():
        drive_link = upload_to_drive(pdf_path, filename)
    else:
        print("    [INFO] credentials.json not found — skipping Drive upload (local dev).")

    return drive_link, pdf_path


# ─── CLI usage ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python resume_builder.py <jd_text_file> [--company NAME] [--title TITLE]")
        sys.exit(1)

    jd_file = Path(sys.argv[1])
    jd_text = jd_file.read_text(encoding="utf-8")

    info = {
        "title":      "Software Engineer",
        "company":    "Test Company",
        "date_found": "2025-01-01",
    }
    args = sys.argv[2:]
    for flag, key in [("--company", "company"), ("--title", "title")]:
        if flag in args:
            info[key] = args[args.index(flag) + 1]

    link, path = build_resume(jd_text, info)
    print(f"PDF: {path}")
    if link:
        print(f"Drive: {link}")
