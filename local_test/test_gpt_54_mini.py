#!/usr/bin/env python3
"""
Smoke-test gpt-5.4-mini with OpenAI Structured Outputs (json_schema).

Set OPENAI_API_KEY in local_test/.env (or repo-root .env), then:

  python local_test/test_gpt_54_mini.py
  python local_test/test_gpt_54_mini.py --mode architect
  python local_test/test_gpt_54_mini.py --mode birth
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from structured_schemas import (
    BIRTH_DATA_SCHEMA,
    book_structure_schema,
    chat_response_format,
)

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")

MODEL_ID = "gpt-5.4-mini-2026-03-17"


def _api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OpenAIKey")
    if not key:
        print("Missing OPENAI_API_KEY (or OpenAIKey) in environment.", file=sys.stderr)
        sys.exit(1)
    return key


def run_ping(client: OpenAI) -> dict:
    """Minimal structured JSON — fastest sanity check."""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ok": {"type": "boolean"},
            "model": {"type": "string"},
        },
        "required": ["ok", "model"],
    }
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": 'Reply with ok=true and model set to your model id.',
            },
        ],
        response_format=chat_response_format("ping", schema),
        temperature=0.0,
    )
    return json.loads(response.choices[0].message.content)


def run_architect(client: OpenAI) -> dict:
    """Structured Outputs shape for Architect (tiny 2-chapter outline)."""
    schema = book_structure_schema(2, 2)
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {
                "role": "system",
                "content": "You are a book architect. Follow the JSON schema exactly.",
            },
            {
                "role": "user",
                "content": (
                    "Return a tiny book outline for focus Personality in English. "
                    "Exactly 2 chapters. Each chapter needs distinct title, theme, "
                    "description, and chapter_input_material_used.chapter_focus (chart cues). "
                    "Chapter titles max 70 characters. "
                    "Fill all metadata and ui_labels strings; keep them short."
                ),
            },
        ],
        response_format=chat_response_format("book_structure", schema),
        temperature=0.3,
    )
    return json.loads(response.choices[0].message.content)


def run_birth(client: OpenAI) -> dict:
    """Structured Outputs shape for birth parse (start_execution)."""
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": (
                    'Parse birth data. USER PROMPT: "March 15, 1990 at 2:30 PM in Austin, Texas". '
                    "Return day, month, year, hour (0-23), min, lat, lon, tzone."
                ),
            },
        ],
        response_format=chat_response_format("birth_data", BIRTH_DATA_SCHEMA),
        temperature=0.0,
    )
    return json.loads(response.choices[0].message.content)


MODES = {
    "ping": run_ping,
    "architect": run_architect,
    "birth": run_birth,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Smoke-test {MODEL_ID} structured outputs")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="ping",
        help="ping (default), architect, or birth",
    )
    args = parser.parse_args()

    client = OpenAI(api_key=_api_key())
    print(f"Testing {MODEL_ID} structured outputs (mode={args.mode})...")

    try:
        result = MODES[args.mode](client)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("OK")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
