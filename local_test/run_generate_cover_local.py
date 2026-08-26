#!/usr/bin/env python3
"""
Render cover locally using src/generate_cover/app.py (same as Lambda).

Optional dynamic front cover art (local preview only):
  - GPT image of sky from birth_data lat/lon + datetime (no geocoding API)
  - Same white title/focus/birth text as production black cover
  - Falls back to plain black if art generation fails

Usage (from local_test/):
  docker compose run --rm generate-cover

Env:
  OPENAI_API_KEY          required when COVER_DYNAMIC_ENABLED=1 (default)
  COVER_DYNAMIC_ENABLED   1|0 (default 1)
  COVER_PAYLOAD_JSON      optional full payload JSON path
  PAGE_COUNT              default 220
  USE_LEGACY_COVER_LAYOUT optional offline size estimate (paperback refuses this in prod)
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

sys.path.insert(0, HERE)

_GEN_COVER_DIR = os.path.join(HERE, "..", "src", "generate_cover")
if os.path.isfile(os.path.join(HERE, "generate_cover", "app.py")):
    _GEN_COVER_DIR = os.path.join(HERE, "generate_cover")
sys.path.insert(0, _GEN_COVER_DIR)
from app import generate_cover_artifact  # noqa: E402

from cover_art_local import generate_cover_background_from_birth_data  # noqa: E402

OUTPUT_DIR = os.path.join(HERE, "output")
COVER_OUT_DIR = os.path.join(OUTPUT_DIR, "book-covers")
PIPELINE_CONFIG_PATH = os.path.join(HERE, "pipeline_config.json")

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
        "hour": 22,
        "min": 25,
        "lat": 40.7469,
        "lon": -73.9718,
        "tzone": -5,
    },
    "full_book_structure": {
        "metadata": {
            "title": "Your Next Role: Build Work That Fits Your Real Life for You and Your Family",
        }
    },
}


def _cover_dynamic_enabled() -> bool:
    return os.environ.get("COVER_DYNAMIC_ENABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _load_payload() -> dict:
    payload_path = os.environ.get("COVER_PAYLOAD_JSON", "").strip()
    if payload_path and os.path.isfile(payload_path):
        with open(payload_path, encoding="utf-8") as f:
            return json.load(f)

    payload = json.loads(json.dumps(DEFAULT_PAYLOAD))
    if os.path.isfile(PIPELINE_CONFIG_PATH):
        with open(PIPELINE_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("birth_data"):
            payload["birth_data"] = cfg["birth_data"]
        if cfg.get("focus"):
            payload["focus"] = cfg["focus"]
        if cfg.get("language"):
            payload["language"] = cfg["language"]
        if cfg.get("order_id"):
            payload["order_id"] = cfg["order_id"]
        if cfg.get("line_item_id"):
            payload["line_item_id"] = cfg["line_item_id"]
    return payload


def _try_generate_front_background(birth_data: dict, line_item_id: str) -> object | None:
    if not _cover_dynamic_enabled():
        print("Dynamic cover art disabled (COVER_DYNAMIC_ENABLED=0). Using black background.")
        return None

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OpenAIKey")
    if not api_key:
        print("No OPENAI_API_KEY; using black background fallback.")
        return None

    try:
        background = generate_cover_background_from_birth_data(birth_data, api_key)
        os.makedirs(COVER_OUT_DIR, exist_ok=True)
        art_path = os.path.join(COVER_OUT_DIR, f"{line_item_id}_cover_art_source.png")
        background.save(art_path, format="PNG")
        print(f"Saved GPT cover art source -> {art_path}")
        return background
    except Exception as exc:
        print(f"Dynamic cover art failed ({exc}); using black background fallback.")
        return None


def main() -> None:
    payload = _load_payload()
    os.makedirs(COVER_OUT_DIR, exist_ok=True)

    print("page_count:", payload["page_count"])
    print("title:", payload["full_book_structure"]["metadata"]["title"])
    print("focus:", payload["focus"])
    print("birth_data:", payload.get("birth_data"))

    front_background = _try_generate_front_background(
        payload["birth_data"],
        payload["line_item_id"],
    )

    result = generate_cover_artifact(
        payload,
        output_dir=COVER_OUT_DIR,
        upload_s3=False,
        front_background=front_background,
    )
    print("PDF:", result.get("local_cover_pdf_path"))
    print("JPG:", result.get("local_front_cover_jpg"))


if __name__ == "__main__":
    main()
