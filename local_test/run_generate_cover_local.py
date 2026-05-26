#!/usr/bin/env python3
"""
Render cover locally using src/generate_cover/app.py (same as Lambda).

Usage (from local_test/):
  docker compose run --rm generate-cover

Set PAGE_COUNT (default 220). LULU_CLIENT_KEY / LULU_CLIENT_SECRET in .env.
Optional: COVER_PAYLOAD_JSON path to a full Step Functions-style payload file.
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

_GEN_COVER_DIR = os.path.join(HERE, "..", "src", "generate_cover")
if os.path.isfile(os.path.join(HERE, "generate_cover", "app.py")):
    _GEN_COVER_DIR = os.path.join(HERE, "generate_cover")
sys.path.insert(0, _GEN_COVER_DIR)
from app import generate_cover_artifact  # noqa: E402

OUTPUT_DIR = os.path.join(HERE, "output")
COVER_OUT_DIR = os.path.join(OUTPUT_DIR, "book-covers")

# Minimal payload — same fields the Lambda uses from Step Functions
DEFAULT_PAYLOAD = {
    "order_id": "LOCAL_COVER_TEST",
    "line_item_id": "LOCAL_COVER_001",
    "language": "english",
    "focus": "Job & Vocation",
    "page_count": int(os.environ.get("PAGE_COUNT", "220")),
    "cover_title": "Ollie Trisdale",
    "birth_data": {
        "day": 14,
        "month": 2,
        "year": 1990,
        "hour": 14,
        "min": 25,
        "lat": 40.7469,
        "lon": -73.9718,
        "tzone": -5,
    },
    "full_book_structure": {
        "metadata": {
            "title": "Your Next Role: Build Work That Fits Your Real Life",
        }
    },
}


def main() -> None:
    payload_path = os.environ.get("COVER_PAYLOAD_JSON", "").strip()
    if payload_path and os.path.isfile(payload_path):
        with open(payload_path, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = DEFAULT_PAYLOAD

    os.makedirs(COVER_OUT_DIR, exist_ok=True)
    print("page_count:", payload["page_count"])
    print("title:", payload["full_book_structure"]["metadata"]["title"])
    print("focus:", payload["focus"])

    result = generate_cover_artifact(payload, output_dir=COVER_OUT_DIR, upload_s3=False)
    print("PDF:", result.get("local_cover_pdf_path"))
    print("JPG:", result.get("local_front_cover_jpg"))


if __name__ == "__main__":
    main()
