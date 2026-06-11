#!/usr/bin/env python3
"""
Smoke-test gpt-5.4-mini-2026-03-17 with the OpenAI Python client.

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
    """Minimal JSON completion — fastest sanity check."""
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {"role": "user", "content": 'Reply with JSON: {"ok": true, "model": "<your model id>"}'},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    return json.loads(response.choices[0].message.content)


def run_architect(client: OpenAI) -> dict:
    """Same API shape as src/architect_book/app.py."""
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {
                "role": "system",
                "content": "You are a book architect. Output valid JSON only.",
            },
            {
                "role": "user",
                "content": (
                    'Return a tiny book outline JSON with keys "metadata" (title, subtitle) '
                    'and "structure" (chapters: list of 2 items with title, description).'
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return json.loads(response.choices[0].message.content)


def run_birth(client: OpenAI) -> dict:
    """Same API shape as src/start_execution/app.py parse_birth_data_with_ai."""
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": (
                    'Parse birth data. USER PROMPT: "March 15, 1990 at 2:30 PM in Austin, Texas". '
                    'Return JSON: "day", "month", "year", "hour", "min", "lat", "lon", "tzone".'
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    return json.loads(response.choices[0].message.content)


MODES = {
    "ping": run_ping,
    "architect": run_architect,
    "birth": run_birth,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Smoke-test {MODEL_ID}")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="ping",
        help="ping (default), architect, or birth",
    )
    args = parser.parse_args()

    client = OpenAI(api_key=_api_key())
    print(f"Testing {MODEL_ID} (mode={args.mode})...")

    try:
        result = MODES[args.mode](client)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("OK")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
