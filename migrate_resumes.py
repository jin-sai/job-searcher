"""
migrate_resumes.py — One-time migration for existing resumes.

For each PDF in output/resumes/ (old naming scheme):
  1. Hash the PDF bytes to detect duplicates
  2. Rename unique files to SaiKumar_Resume_<N>.pdf
  3. Delete duplicate PDFs (same content, different name)
  4. Build manifest.json with pdf_hash → filename
  5. Print a mapping of old name → new name for manual sheet updates

Run from the project root:
    python migrate_resumes.py
"""

import hashlib
import json
from pathlib import Path

OUTPUT_DIR    = Path(__file__).parent / "output" / "resumes"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"


def pdf_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if not OUTPUT_DIR.exists():
        print("output/resumes/ does not exist — nothing to migrate.")
        return

    pdfs = sorted(
        [f for f in OUTPUT_DIR.glob("*.pdf") if f.name != "_tmp_resume.pdf"],
        key=lambda f: f.stat().st_mtime,  # oldest first → lowest IDs
    )

    if not pdfs:
        print("No PDFs found in output/resumes/ — nothing to migrate.")
        return

    print(f"Found {len(pdfs)} PDF(s) in output/resumes/\n")

    # Load existing manifest if present (from new-scheme runs that may have happened)
    manifest: dict = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {}
    existing_names = {v: k for k, v in manifest.items()}  # filename → hash

    seen_hashes: dict[str, Path] = {}  # hash → canonical path
    renames:     dict[str, str]  = {}  # old_name → new_name
    deletions:   list[Path]      = []

    for pdf in pdfs:
        # Already migrated in a prior run
        if pdf.name in existing_names:
            h = existing_names[pdf.name]
            seen_hashes[h] = pdf
            continue

        h = pdf_hash(pdf)

        if h in manifest:
            # Duplicate of a file already in the manifest (from new-scheme builds)
            canonical_name = manifest[h]
            deletions.append(pdf)
            renames[pdf.name] = canonical_name
            continue

        if h in seen_hashes:
            # Duplicate of another old file processed this run
            canonical_name = seen_hashes[h].name
            deletions.append(pdf)
            renames[pdf.name] = canonical_name
            continue

        # Unique content — assign next ID and rename
        next_id      = len(manifest) + 1
        new_name     = f"SaiKumar_Resume_{next_id}.pdf"
        new_path     = OUTPUT_DIR / new_name

        pdf.rename(new_path)
        manifest[h]       = new_name
        seen_hashes[h]    = new_path
        renames[pdf.name] = new_name

    # Write updated manifest
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Delete duplicates
    for path in deletions:
        if path.exists():
            path.unlink()

    # Summary
    print("=" * 60)
    print(f"{'OLD NAME':<45}  NEW NAME")
    print("=" * 60)
    for old, new in renames.items():
        marker = "  [DUPLICATE → deleted]" if old in [p.name for p in deletions] else ""
        print(f"{old:<45}  {new}{marker}")

    print()
    print(f"manifest.json written  : {len(manifest)} unique resume(s)")
    if deletions:
        print(f"Duplicates deleted     : {len(deletions)}")
    print("\nUpdate 'Resume Version' in Google Sheets using the mapping above.")


if __name__ == "__main__":
    main()
