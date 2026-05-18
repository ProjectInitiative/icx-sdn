"""Document ingestion pipeline: PDF → Markdown → RAG chunks.

Usage:
    python scripts/ingest_docs.py <pdf_filename>

Requires:
    marker-pdf, torch (installed via `nix develop .#agent`)
"""

import os
import re
import json
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT_DIR / "docs" / "raw_pdfs"
MD_DIR = ROOT_DIR / "docs" / "markdown_out"
CHUNK_DIR = ROOT_DIR / "docs" / "agent_chunks"


def run_marker(pdf_path):
    out_dir = MD_DIR / pdf_path.stem
    print(f"Converting {pdf_path.name} to Markdown...")
    subprocess.run(
        ["marker_single", str(pdf_path), str(MD_DIR)],
        check=True,
    )
    md_file = out_dir / f"{pdf_path.stem}.md"
    if not md_file.exists():
        md_file = out_dir / f"{pdf_path.stem}.md"
    print(f"Markdown written to {md_file}")
    return md_file


def chunk_markdown(md_path):
    print(f"Chunking {md_path}...")
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    chunks = re.split(r"\n(?=#{2,3} )", content)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)

    structured = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        lines = chunk.strip().split("\n")
        title = lines[0].replace("#", "").strip()
        entry = {"chunk_id": f"chunk_{i}", "title": title, "content": chunk.strip()}
        structured.append(entry)

        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", title).strip("_").lower()
        out_path = CHUNK_DIR / f"{slug}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2)

    print(f"Created {len(structured)} chunks in {CHUNK_DIR}")
    return structured


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pdf_filename>", file=sys.stderr)
        print(f"  PDFs in: {PDF_DIR}", file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1]
    pdf_path = PDF_DIR / target

    if not pdf_path.exists():
        pdfs = sorted(PDF_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs found in {PDF_DIR}", file=sys.stderr)
            sys.exit(1)
        print(f"Available PDFs:", file=sys.stderr)
        for p in pdfs:
            print(f"  {p.name}", file=sys.stderr)
        sys.exit(1)

    md_file = run_marker(pdf_path)
    chunk_markdown(md_file)
    print("Done.")


if __name__ == "__main__":
    main()
