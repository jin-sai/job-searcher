"""
Resume Builder - Step 3 (optional): Tailored PDF resume per passing job.

Given a job's description text and basic info (title, company), this module:
  1. Extracts keywords from the JD (frequency + curated tech list)
  2. Scores every bullet in master_resume.json by tag overlap
  3. Selects the best bullets per experience entry and top 3 projects
  4. Renders a .tex file via Jinja2 (Jake's Resume template)
  5. Compiles to PDF with pdflatex
  6. Saves to output/resumes/<Company>_<Role>_<Date>.pdf

Called from filter_jobs.py after a job passes all filters.
Fails gracefully - a resume build failure never crashes the filter run.

Prerequisites:
  - master_resume.json in project root (fill in your data + tags)
  - pdflatex on PATH  (apt-get install texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended)
  - pip: jinja2
"""

import json
import re
import shutil
from datetime import datetime
import subprocess
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR             = Path(__file__).parent.parent
MASTER_RESUME_PATH   = BASE_DIR / "master_resume.json"
TECH_KEYWORDS_PATH   = BASE_DIR / "config" / "tech_keywords.json"
TEMPLATE_DIR         = BASE_DIR / "resume"
OUTPUT_DIR           = BASE_DIR / "output" / "resumes"

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
        # Custom delimiters - none of these appear in LaTeX source
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
        freq          - {word: count} for all non-stop words in the JD
        high_priority - words appearing 3+ times OR in the curated tech list
    """
    tech_set = _load_tech_keywords()

    # Tokenise: keep alphanumeric runs plus common tech chars (+, #, .)
    # Strip trailing punctuation (., -, _) that appears at sentence/clause boundaries
    tokens = [
        t.rstrip("._-")
        for t in re.findall(r"[a-zA-Z][a-zA-Z0-9+#._-]*", jd_text.lower())
    ]

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

def _score_tags(
    tags: list[str], freq: dict[str, int], high_priority: set[str]
) -> tuple[float, list[tuple[str, int, bool]]]:
    """
    Score a list of tags against the JD.
    Returns (score, hits) where hits = [(tag, base_freq, is_high_priority), ...]
    """
    score = 0.0
    hits: list[tuple[str, int, bool]] = []
    for tag in tags:
        tag_lower = tag.lower()
        if tag_lower in freq:
            base = freq[tag_lower]
            hp   = tag_lower in high_priority
            score += base + (2.0 if hp else 0)
            hits.append((tag, base, hp))
    return score, hits


def _fmt_hits(hits: list[tuple[str, int, bool]]) -> str:
    if not hits:
        return "no tag matches"
    return ", ".join(
        f"{tag}({base}{'+2hp' if hp else ''})" for tag, base, hp in hits
    )


def _score_bullet(
    bullet: dict, freq: dict[str, int], high_priority: set[str]
) -> tuple[float, list[tuple[str, int, bool]]]:
    """
    Score = sum of JD frequencies for each tag that appears in the JD,
    plus a bonus for high-priority (common or tech-list) matches.
    Returns (score, hits).
    """
    tag_score, hits = _score_tags(bullet.get("tags", []), freq, high_priority)
    return max(float(bullet.get("default_score", 0)), tag_score), hits


def _select_bullets(
    bullets: list[dict],
    freq: dict[str, int],
    high_priority: set[str],
    min_bullets: int = 2,
    max_bullets: int = 5,
    label: str = "",
    verbose: bool = False,
) -> list[dict]:
    scored = [(b, *_score_bullet(b, freq, high_priority)) for b in bullets]

    # Select which bullets to include (by score), then restore original JSON order
    positive_idx = {i for i, (_, s, _h) in enumerate(scored) if s > 0}
    if len(positive_idx) > max_bullets:
        # Keep only the top max_bullets by score, ties broken by original order
        top = sorted(positive_idx, key=lambda i: scored[i][1], reverse=True)[:max_bullets]
        positive_idx = set(top)
    if len(positive_idx) < min_bullets:
        # Not enough positive-scoring bullets - pad with next best in original order
        all_idx = sorted(range(len(scored)), key=lambda i: scored[i][1], reverse=True)
        positive_idx = set(all_idx[:min_bullets])

    if verbose:
        for i, (b, score, hits) in enumerate(scored):
            selected  = i in positive_idx
            marker    = "+" if selected else "-"
            reason    = "(padded - below min)" if (i in positive_idx and score == 0) else ""
            text_snippet = (b.get("text", "") or "")[:90].replace("\n", " ")
            print(f"      {marker} score={score:.1f}  [{_fmt_hits(hits)}]  {reason}")
            print(f"        \"{text_snippet}\"")

    return [b for i, (b, _s, _h) in enumerate(scored) if i in positive_idx]


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


def _build_summary_from_structured(
    summary_obj: dict, freq: dict[str, int], high_priority: set[str],
    job_info: dict | None = None, years_exp: int = 4,
    verbose: bool = False,
) -> str:
    """Compose a summary string from the structured summary object in master_resume.json.

    Each variant list (specialization, core) is scored by JD tag overlap; the
    highest-scoring variant wins.  If no variant has any score, the one marked
    ``"default": true`` is used as a fallback.

    The specialization part is prefixed with:
      "Backend Engineer with N+ years of experience in ..."   - if job title contains "backend engineer"
      "Backend Software Engineer with N+ years of experience in ..."  - otherwise (default)
    """

    def best_variant(variants: list, section: str) -> str:
        default_text = next((v["text"] for v in variants if v.get("default")), None)
        best_text  = default_text or (variants[0]["text"] if variants else "")
        best_score = -1
        for v in variants:
            _, hits = _score_tags(v.get("tags", []), freq, high_priority)
            score   = sum(base + (2 if hp else 0) for _, base, hp in hits)
            if verbose:
                marker = ">" if score > best_score else " "
                print(f"      {marker} score={score:.1f}  [{_fmt_hits(hits)}]  \"{v['text'][:70]}\"")
            if score > best_score:
                best_score = score
                best_text  = v["text"]
        return best_text

    title = (job_info or {}).get("title", "")
    if "backend engineer" in title.lower():
        role_prefix = "Backend Engineer"
    else:
        role_prefix = "Backend Software Engineer"

    parts: list[str] = []
    if "specialization" in summary_obj:
        if verbose:
            print(f"    specialization variants:")
        spec = best_variant(summary_obj["specialization"], "specialization")
        parts.append(f"{role_prefix} with {years_exp}+ years of experience building {spec}")
    if "core" in summary_obj:
        if verbose:
            print(f"    core variants:")
        parts.append(best_variant(summary_obj["core"], "core"))
    if "differentiator" in summary_obj:
        parts.append(str(summary_obj["differentiator"]))
    if "reputation" in summary_obj:
        parts.append(str(summary_obj["reputation"]))
    return " ".join(parts)


# ─── Skills selection ─────────────────────────────────────────────────────────

def _select_skills(
    skills_dict: dict, freq: dict[str, int], high_priority: set[str],
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Return {category: [skill_name, ...]} filtered by mandatory flag and JD relevance."""
    result: dict[str, list[str]] = {}
    for category, items in skills_dict.items():
        names: list[str] = []
        log_lines: list[str] = []
        for item in items:
            if item.get("mandatory"):
                names.append(item["name"])
                if verbose:
                    log_lines.append(f"+ {item['name']} (mandatory)")
            else:
                matched_tags = [t for t in item.get("tags", []) if t.lower() in freq]
                if matched_tags:
                    names.append(item["name"])
                    if verbose:
                        log_lines.append(f"+ {item['name']} (tags: {', '.join(matched_tags)})")
                elif verbose:
                    log_lines.append(f"- {item['name']}")
        if names:
            result[category] = names
        if verbose and log_lines:
            print(f"    {category}: " + "  ".join(log_lines))
    return result


# ─── Resume selection ─────────────────────────────────────────────────────────

def select_resume(
    master: dict, freq: dict[str, int], high_priority: set[str],
    job_info: dict, verbose: bool = False,
) -> dict:
    """Pick the best bullets from master_resume.json for this JD."""

    if verbose:
        print("\n-- Resume selection ----------------------------------------------------------")

    # ── Experience ────────────────────────────────────────────────────────────
    experience = []
    for exp in master.get("experience", []):
        if verbose:
            print(f"\n  EXPERIENCE  {exp.get('company', '')} | {exp.get('role', '')}")
        selected = _select_bullets(
            exp["bullets"], freq, high_priority,
            min_bullets=exp.get("min_bullets", 2),
            max_bullets=exp.get("max_bullets", 5),
            label=exp.get("company", ""),
            verbose=verbose,
        )
        experience.append({**exp, "bullets": selected})

    # ── Projects ──────────────────────────────────────────────────────────────
    all_projects = master.get("projects", {}).get("items", [])
    max_projects = master.get("projects", {}).get("max", 3)

    project_scores = []
    for proj in all_projects:
        proj_tag_score, proj_hits = _score_tags(proj.get("tags", []), freq, high_priority)
        proj_score = max(proj.get("default_score", 0), proj_tag_score)
        selected = _select_bullets(proj.get("bullets", []), freq, high_priority,
                                   min_bullets=1, max_bullets=3)
        project_scores.append((proj, proj_score, proj_hits, selected))

    # Select top N by score, then restore original JSON order
    top_projects = sorted(
        range(len(project_scores)), key=lambda i: project_scores[i][1], reverse=True
    )[:max_projects]
    top_idx = set(top_projects)
    projects = [{**p, "bullets": sb} for i, (p, _, _h, sb) in enumerate(project_scores) if i in top_idx]

    if verbose:
        print(f"\n  PROJECTS  (top {max_projects} of {len(all_projects)} by score)")
        for i, (proj, score, hits, _sb) in enumerate(project_scores):
            marker = "+" if i in top_idx else "-"
            ds = proj.get("default_score", 0)
            ds_note = f"  default_score={ds}" if score == ds and ds > 0 else ""
            print(f"    {marker} score={score:.1f}  {proj.get('name', '')}  [{_fmt_hits(hits)}]{ds_note}")
            if i in top_idx:
                # log bullets for selected projects
                for b in _select_bullets(proj.get("bullets", []), freq, high_priority,
                                         min_bullets=1, max_bullets=3, verbose=False):
                    _, b_hits = _score_bullet(b, freq, high_priority)
                    text = (b.get("text", "") or "")[:80].replace("\n", " ")
                    print(f"        + [{_fmt_hits(b_hits)}]  \"{text}\"")

    # ── Skills ────────────────────────────────────────────────────────────────
    if verbose:
        print(f"\n  SKILLS")
    skills = _select_skills(master.get("skills", {}), freq, high_priority, verbose=verbose)

    # ── Education ─────────────────────────────────────────────────────────────
    if verbose:
        print(f"\n  EDUCATION")
    education = []
    for edu in master.get("education", []):
        cw_obj = edu.get("coursework")
        if cw_obj:
            matched = [t for t in cw_obj.get("tags", []) if t.lower() in freq]
            if matched:
                coursework = cw_obj["text"]
                if verbose:
                    print(f"    + {edu.get('institution', '')}  coursework included  (tags: {', '.join(matched)})")
            else:
                coursework = None
                if verbose:
                    print(f"    - {edu.get('institution', '')}  coursework excluded  (no tag matches; tags: {', '.join(cw_obj.get('tags', []))})")
        else:
            coursework = None
            if verbose:
                print(f"    - {edu.get('institution', '')}  no coursework defined")
        education.append({**edu, "coursework": coursework})

    # ── Summary ───────────────────────────────────────────────────────────────
    # Derive top tags for summary (rank by freq of all bullet tags across experience)
    tag_freq: dict[str, float] = {}
    for exp in master.get("experience", []):
        for b in exp["bullets"]:
            for tag in b.get("tags", []):
                tag_lower = tag.lower()
                tag_freq[tag_lower] = tag_freq.get(tag_lower, 0) + freq.get(tag_lower, 0)
    top_tags = sorted(tag_freq, key=tag_freq.get, reverse=True)  # type: ignore[arg-type]

    summary_raw = master.get("summary")
    if isinstance(summary_raw, dict):
        if verbose:
            print(f"\n  SUMMARY  (structured - scoring variants)")
        summary = _build_summary_from_structured(
            summary_raw, freq, high_priority,
            job_info=job_info, years_exp=master.get("years_experience", 4),
            verbose=verbose,
        )
    elif isinstance(summary_raw, str):
        if verbose:
            print(f"\n  SUMMARY  (static string - used as-is)")
        summary = summary_raw
    else:
        if verbose:
            print(f"\n  SUMMARY  (auto-generated from top tags: {', '.join(top_tags[:5])})")
        summary = _build_summary(top_tags, job_info, master.get("years_experience", 4))

    if verbose:
        print("\n" + "-" * 72 + "\n")

    return {
        "name":       master["name"],
        "email":      master["email"],
        "phone":      master["phone"],
        "linkedin":   master.get("linkedin", ""),
        "github":     master.get("github", ""),
        "portfolio":  master.get("portfolio", ""),
        "location":   master.get("location", ""),
        "summary":    summary,
        "education":  education,
        "experience": experience,
        "projects":   projects,
        "skills":     skills,
        "achievements": [],   # filled greedily in build_resume after 1-page fit
    }


# ─── Rendering & compilation ──────────────────────────────────────────────────

def render_tex(tailored: dict) -> str:
    """Render the Jinja2 LaTeX template with the tailored resume data."""
    env = _jinja_env()
    template = env.get_template("resume_template.tex.j2")
    return template.render(**tailored)


def compile_pdf(tex_content: str, output_path: Path) -> int:
    """Write tex_content to a temp dir, compile with pdflatex, copy PDF out.
    Returns the page count of the generated PDF.
    """
    _MIKTEX_PATH = r"C:\Users\saiku\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"
    pdflatex_cmd = shutil.which("pdflatex") or (
        _MIKTEX_PATH if Path(_MIKTEX_PATH).exists() else None
    )
    if not pdflatex_cmd:
        raise RuntimeError(
            "pdflatex not found - install TeX Live: "
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

        # Parse page count from pdflatex output:
        # "Output written on resume.pdf (2 pages, 12345 bytes)."
        pages = 1
        log = result.stdout or ""
        for ln in log.splitlines():
            m = re.search(r"Output written on .+\((\d+) page", ln)
            if m:
                pages = int(m.group(1))
                break

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(pdf_src), str(output_path))
        return pages


def _drop_lowest_item(
    tailored: dict, freq: dict[str, int], high_priority: set[str]
) -> bool:
    """Drop the single lowest-scoring droppable item across experience bullets and projects.

    Candidates:
      - Non-mandatory experience bullets from entries that still have more than
        their configured min_bullets (from master_resume.json, default 2)
      - Non-mandatory projects (scored by default_score + tag overlap)

    The lowest-scoring candidate across both pools is dropped.
    Returns True if something was dropped, False if nothing is left to drop.
    """
    # (score, drop_fn) - collect all candidates into one pool
    candidates: list[tuple[float, object]] = []

    # Experience bullets
    for ei, exp in enumerate(tailored.get("experience", [])):
        bullets = exp["bullets"]
        min_keep = exp.get("min_bullets", 2)
        if len(bullets) <= min_keep:
            continue
        for bi, bullet in enumerate(bullets):
            if bullet.get("mandatory"):
                continue
            score, _ = _score_bullet(bullet, freq, high_priority)
            label = f"bullet \"{(bullet.get('text') or '')[:60]}\" ({exp.get('company', '')})"
            # Capture ei/bi by value via default args
            def _drop_bullet(t=tailored, e=ei, b=bi):
                t["experience"][e]["bullets"].pop(b)
            candidates.append((score, label, _drop_bullet))

    # Projects
    for pi, proj in enumerate(tailored.get("projects", [])):
        if proj.get("mandatory"):
            continue
        proj_tag_score, _ = _score_tags(proj.get("tags", []), freq, high_priority)
        proj_score = max(proj.get("default_score", 0), proj_tag_score)
        label = f"project \"{proj.get('name', '')}\""
        def _drop_project(t=tailored, p=pi):
            t["projects"].pop(p)
        candidates.append((proj_score, label, _drop_project))

    if not candidates:
        return False

    candidates.sort(key=lambda x: x[0])
    score, label, drop_fn = candidates[0]
    print(f"    [trim] dropping {label}  (score={score:.1f})")
    drop_fn()
    return True


# ─── Public entry point ───────────────────────────────────────────────────────

def build_resume(jd_text: str, job_info: dict) -> Path:
    """
    Build a tailored resume PDF for one passing job.

    Args:
        jd_text:  Full text of the job description page.
        job_info: Dict with at least "title", "company", "date_found".

    Returns:
        local_pdf_path

    Raises:
        FileNotFoundError  - master_resume.json missing
        RuntimeError       - pdflatex missing or compile failure
    """
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError(
            "master_resume.json not found - create it from the template in the repo root."
        )

    master = json.loads(MASTER_RESUME_PATH.read_text(encoding="utf-8"))
    freq, high_priority = extract_jd_keywords(jd_text)
    tailored = select_resume(master, freq, high_priority, job_info, verbose=True)

    # Build a filesystem-safe filename
    safe = lambda s: re.sub(r"[^\w]", "_", s or "")
    company  = safe(job_info.get("company", "unknown"))[:30]
    role     = safe(job_info.get("title", "role"))[:30]
    date_str = (job_info.get("date_found") or "")[:10].replace("-", "")
    job_id   = safe(job_info.get("job_id") or "") or datetime.now().strftime("%H%M%S")
    filename = f"{company}_{role}_{date_str}_{job_id}.pdf"
    pdf_path = OUTPUT_DIR / filename

    # Trim-until-fit: drop lowest-scoring non-mandatory bullets until 1 page
    MAX_TRIM_ITERS = 20
    pages = 1
    for iteration in range(MAX_TRIM_ITERS):
        tex_content = render_tex(tailored)
        pages = compile_pdf(tex_content, pdf_path)
        if pages <= 1:
            if iteration > 0:
                print(f"    Trimmed to 1 page after {iteration} drop(s).")
            break
        if not _drop_lowest_item(tailored, freq, high_priority):
            print(f"    [WARN] Cannot trim further - {pages} page(s), nothing left to drop.")
            break
    else:
        print(f"    [WARN] Still {pages} page(s) after {MAX_TRIM_ITERS} trim iterations.")

    # Greedy achievements: add bullets one by one in master order, stop on overflow
    achievement_items = master.get("achievements", {}).get("items", [])
    if achievement_items:
        last_good_tex = render_tex(tailored)
        for item in achievement_items:
            tailored["achievements"].append(item)
            tex_content = render_tex(tailored)
            pages = compile_pdf(tex_content, pdf_path)
            if pages > 1:
                tailored["achievements"].pop()
                compile_pdf(last_good_tex, pdf_path)  # restore last good
                break
            last_good_tex = tex_content
        added = len(tailored["achievements"])
        if added:
            print(f"    Added {added}/{len(achievement_items)} achievement(s).")

    return pdf_path


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

    path = build_resume(jd_text, info)
    print(f"PDF: {path}")
