#!/usr/bin/env python3
"""
Build a PDF from cached pipeline output only (no Astrology API, no OpenAI).

Expects under output/:
  artifacts/astrology_data.json      — birth data in META.Input_Parameters
  artifacts/book_structure.json      — metadata + chapter titles (from architect)
  artifacts/chapter_N.json           — chapter_title, chapter_text
  artifacts/generated_sections.json  — preface_text, prologue_text, epilogue_text
                                       (written automatically after a full pipeline run)
  images/chapter_N.png               — optional chapter art

If generated_sections.json is missing (older runs), preface/prologue/epilogue render empty.

Usage: docker compose run --rm pdf-from-artifacts
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, "/app/generate_pdf")
from book_pdf_exporter import save_book_as_pdf

OUTPUT_DIR = "/app/output"
ARTIFACTS_DIR = os.path.join(OUTPUT_DIR, "artifacts")
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")


def _chapter_json_sort_key(path: str) -> int:
    m = re.search(r"chapter_(\d+)\.json$", path, re.I)
    return int(m.group(1)) if m else 0


def load_birth_data(astrology: dict) -> dict:
    params = (astrology.get("META") or {}).get("Input_Parameters") or {}
    bd = dict(params)
    if "minute" in bd and "min" not in bd:
        bd["min"] = bd["minute"]
    return bd


def main():
    print()
    print("=" * 64)
    print("  PDF FROM LOCAL ARTIFACTS (offline)")
    print("  - No json.astrologyapi.com or other astrology HTTP calls")
    print("  - No OpenAI calls (foreword stays English unless you pass a key)")
    print(f"  - Data source: {ARTIFACTS_DIR}/")
    print("=" * 64)
    print()

    config_path = "/app/pipeline_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    language = config.get("language", "English")
    focus = config.get("focus", "Personality")
    print(f"[local artifacts] Language from pipeline_config.json: {language}")
    print(f"[local artifacts] Focus from pipeline_config.json: {focus}")

    astro_path = os.path.join(ARTIFACTS_DIR, "astrology_data.json")
    struct_path = os.path.join(ARTIFACTS_DIR, "book_structure.json")
    sections_path = os.path.join(ARTIFACTS_DIR, "generated_sections.json")

    for path, label in (
        (astro_path, "astrology_data.json"),
        (struct_path, "book_structure.json"),
    ):
        if not os.path.isfile(path):
            print(f"ERROR: Missing {label} at {path}. Run the full pipeline first.")
            sys.exit(1)

    with open(astro_path, "r", encoding="utf-8") as f:
        astrology_data = json.load(f)
    print(f"[local artifacts] Loaded birth/footer inputs from disk: {astro_path}")

    with open(struct_path, "r", encoding="utf-8") as f:
        structure = json.load(f)
    print(f"[local artifacts] Loaded book title & metadata from disk: {struct_path}")

    birth_data = load_birth_data(astrology_data)

    struct_inner = structure.get("structure") or {}
    metadata = structure.get("metadata") or struct_inner.get("metadata") or {}
    title = metadata.get("title", "The Architecture of You")

    preface_text = prologue_text = epilogue_text = ""
    if os.path.isfile(sections_path):
        with open(sections_path, "r", encoding="utf-8") as f:
            sections = json.load(f)
        preface_text = sections.get("preface_text") or ""
        prologue_text = sections.get("prologue_text") or ""
        epilogue_text = sections.get("epilogue_text") or ""
        print(f"[local artifacts] Loaded preface/prologue/epilogue from disk: {sections_path}")
    else:
        print(
            "[local artifacts] WARNING: generated_sections.json not found — "
            "preface/prologue/epilogue will be empty. "
            "Run `docker compose run --rm pipeline` once to create it."
        )

    chapter_files = [
        os.path.join(ARTIFACTS_DIR, fn)
        for fn in os.listdir(ARTIFACTS_DIR)
        if re.match(r"chapter_\d+\.json$", fn, re.I)
    ]
    chapter_files.sort(key=_chapter_json_sort_key)

    if not chapter_files:
        print(f"ERROR: No chapter_*.json files in {ARTIFACTS_DIR}.")
        sys.exit(1)

    print(f"[local artifacts] Found {len(chapter_files)} chapter JSON file(s) under artifacts/")

    chapters = []
    with_image = 0
    for ch_path in chapter_files:
        with open(ch_path, "r", encoding="utf-8") as f:
            ch = json.load(f)
        idx = _chapter_json_sort_key(ch_path)
        img_path = os.path.join(IMAGES_DIR, f"chapter_{idx}.png")
        if not os.path.isfile(img_path):
            img_path = None
        else:
            with_image += 1
        chapters.append(
            {
                "heading": ch.get("chapter_title", ""),
                "content": ch.get("chapter_text", ""),
                "image_path": img_path,
            }
        )
    print(
        f"[local artifacts] Chapter images from disk: {with_image}/{len(chapters)} "
        f"(folder {IMAGES_DIR}/)"
    )

    book_data = {
        "metadata": metadata,
        "birth_data": birth_data,
        "preface_text": preface_text,
        "prologue_text": prologue_text,
        "epilogue_text": epilogue_text,
        "focus": (focus or "").strip(),
        "chapters": chapters,
    }

    timestamp = int(time.time())
    filename = f"book_{timestamp}_from_artifacts.pdf"

    print()
    print("[local artifacts] Rendering PDF with WeasyPrint only (openai_api_key=None → no OpenAI).")
    print(f"[local artifacts] Foreword/acknowledgments load from bundled assets under generate_pdf/.")
    print(f"[local artifacts] Output file: {filename}")
    output_path, page_count = save_book_as_pdf(
        title=title,
        book_data=book_data,
        filename=filename,
        output_dir=OUTPUT_DIR,
        language=language,
        openai_api_key=None,
    )
    print()
    print(f"[local artifacts] Done. PDF path: {output_path}")
    print(f"[local artifacts] Page count: {page_count}")
    print("[local artifacts] Reminder: this run used only local files + WeasyPrint; no astrology/OpenAI APIs.")


if __name__ == "__main__":
    main()
