#!/usr/bin/env python3
"""
Local end-to-end book generation pipeline (BATCH API variant).
Uses the OpenAI Batch API for chapter text via /v1/responses (GPT-5.5 reasoning + verbosity;
see repo gpt-5.5-doc.txt). Images use Batch /v1/images/generations (gpt-image-2).

Pipeline steps:
  1. Fetch astrology data (astrologyapi.com)
  2. Architect book structure (OpenAI Responses API, GPT-5.5) with validation + retry
  3a. Submit chapter + section text as one Batch job (/v1/responses)
  3b. Poll until batch completes, validate outputs, retry failures, collect results
  3c. Generate chapter images via Batch API (with validation + retry)
  4. Generate PDF (book_pdf_exporter.py)

Usage:
  docker compose run --rm pipeline              # full pipeline (APIs + PDF)
  docker compose run --rm pdf-from-artifacts    # PDF only from output/artifacts (no API calls)
"""
import sys
import os
import json
import asyncio
import time
import re
from datetime import datetime

import base64
import requests
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI

sys.path.insert(0, "/app/generate_pdf")
from book_pdf_exporter import save_book_as_pdf

load_dotenv("/app/.env")

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ASTRO_WESTERN_UID = os.environ["ASTROLOGY_WESTERN_USER_ID"]
ASTRO_WESTERN_KEY = os.environ["ASTROLOGY_WESTERN_API_KEY"]
ASTRO_VEDIC_UID = os.environ["ASTROLOGY_VEDIC_USER_ID"]
ASTRO_VEDIC_KEY = os.environ["ASTROLOGY_VEDIC_API_KEY"]

# GPT-5.5 for local batch test (Responses API; reasoning + text.verbosity per gpt-5.5-doc.txt)
MODEL_CONTENT = "gpt-5.5"
MODEL_IMAGE = "gpt-image-2"

# Chapter batch: high reasoning + high verbosity; reserve headroom for reasoning + ~10k-word prose
REASONING_EFFORT_CHAPTER = "high"
TEXT_VERBOSITY_CHAPTER = "high"
CHAPTER_MAX_OUTPUT_TOKENS = 48000

# Architect / long prose (sync Responses)
REASONING_EFFORT_ARCHITECT = "high"
TEXT_VERBOSITY_ARCHITECT = "high"
ARCHITECT_MAX_OUTPUT_TOKENS = 24000
ARCHITECT_EXPECTED_CHAPTERS = 7
ARCHITECT_MAX_RETRIES = 2

# Style profile (structured JSON — keep reasoning moderate, text less verbose)
REASONING_EFFORT_STYLE = "medium"
TEXT_VERBOSITY_STYLE = "low"
STYLE_MAX_OUTPUT_TOKENS = 600

# Preface / prologue / epilogue (batched with chapters)
SECTION_MAX_OUTPUT_TOKENS = 4000
SECTION_WORD_TARGET = 550
SECTION_WORD_MIN = 500
SECTION_WORD_MAX = 600
IMAGE_SUMMARY_MAX_OUTPUT_TOKENS = 200
IMAGE_MIN_BYTES = 50000

BATCH_POLL_INTERVAL = 15  # seconds between status checks
BOOK_WORD_TARGET = 50000
CHAPTER_WORD_TARGET = 7750
CHAPTER_WORD_MIN = 7500
CHAPTER_WORD_MAX = 8000
MAX_BATCH_RETRIES = 3

BATCH_ENDPOINT_RESPONSES = "/v1/responses"

OUTPUT_DIR = "/app/output"
ARTIFACTS_DIR = os.path.join(OUTPUT_DIR, "artifacts")
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")

# ---------------------------------------------------------------------------
# SSM prompts (hardcoded from Terraform)
# ---------------------------------------------------------------------------

ARCHITECT_SYSTEM_PROMPT = """You are an ASI (Artificial Superintelligence) acting as a master psychological interpreter and book architect.
Your persona is wise, insightful, and empathetic.
**CRITICAL INSTRUCTION:** You MUST output your response in **__LANGUAGE__**."""

ARCHITECT_USER_PROMPT = """**CRITICAL LANGUAGE REQUIREMENT:**
The Book Title, Chapter Titles, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

**TASK:**
Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

    **RULES FOR THE MAIN BOOK TITLE AND CHAPTER TITLES:**
    - Maximum 70 total characters INCLUDING spaces.
    - Prefer Maximum 10-11 words total for the book title.

**STRUCTURE RULES:**
You must generate a book outline with EXACTLY 7 CHAPTERS.
Each chapter must be thematically distinct and explore a specific facet of "__FOCUS__".

**TECHNICAL MANDATE: JSON OUTPUT**
Your entire response MUST be a single, valid JSON object.
{
  "metadata": {
    "title": "Book Title (in __LANGUAGE__)",
    "subtitle": "Subtitle (in __LANGUAGE__)",
    "footer_text": "Footer in __LANGUAGE__",
    "preface_title": "Preface Title in __LANGUAGE__",
    "prologue_title": "Prologue Title in __LANGUAGE__",
    "epilogue_title": "Epilogue Title in __LANGUAGE__",
    "dedication_title": "Career by Design or similar (in __LANGUAGE__)"
  },
  "ui_labels": {
    "toc_title": "Contents (in __LANGUAGE__)",
    "chapter_prefix": "Chapter (in __LANGUAGE__)"
  },
  "structure": {
    "preface_description": "...",
    "prologue_description": "...",
    "epilogue_description": "...",
    "chapters": [
      { "title": "Chapter Title (in __LANGUAGE__)", "description": "A detailed summary (in __LANGUAGE__)." }
    ]
  }
}

**Comprehensive Astrological Data:**
__ASTROLOGY_DATA__"""

IMAGE_PROMPT_TEMPLATE = "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. Style: ethereal, cosmic, rich colors. CRITICAL: NO text, letters, or figures."

METADATA_KEYS = (
    "title",
    "subtitle",
    "footer_text",
    "preface_title",
    "prologue_title",
    "epilogue_title",
    "dedication_title",
)
UI_LABEL_KEYS = ("toc_title", "chapter_prefix")
SECTION_DESC_KEYS = ("preface_description", "prologue_description", "epilogue_description")


# ---------------------------------------------------------------------------
# Validation helpers (aligned with src/write_chapters/app.py)
# ---------------------------------------------------------------------------

_CJK_CHAR_RE = re.compile(
    r"[\u3040-\u30ff"
    r"\u3400-\u4dbf"
    r"\u4e00-\u9fff"
    r"\uf900-\ufaff"
    r"\uac00-\ud7af"
    r"\u0900-\u097f"
    r"\u0e00-\u0e7f"
    r"]"
)
_CLOSING_WRAPPERS = '"\'""''」』)》）】'
_SENTENCE_END_CHARS = frozenset(".!?。！？।॥")
CJK_MIN_LENGTH_RATIO = 0.62


def _cjk_char_count(text: str) -> int:
    return len(_CJK_CHAR_RE.findall(str(text or "")))


def _latin_word_count(text: str) -> int:
    remainder = _CJK_CHAR_RE.sub(" ", str(text or ""))
    return len([w for w in remainder.split() if w.strip() and any(c.isalnum() for c in w)])


def _content_unit_count(text: str) -> int:
    return _cjk_char_count(text) + _latin_word_count(text)


def _is_cjk_heavy(text: str) -> bool:
    cjk = _cjk_char_count(text)
    if cjk < 80:
        return False
    units = _content_unit_count(text)
    return cjk >= units * 0.35


def _min_content_units(min_val: int, text: str) -> int:
    if _is_cjk_heavy(text):
        return max(1, int(min_val * CJK_MIN_LENGTH_RATIO))
    return min_val


def _word_count(text: str) -> int:
    return _content_unit_count(text)


def _text_ends_complete_sentence(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    while stripped and stripped[-1] in _CLOSING_WRAPPERS:
        stripped = stripped[:-1].rstrip()
    return bool(stripped) and stripped[-1] in _SENTENCE_END_CHARS


def _batch_body_is_incomplete(body: dict) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("status") == "incomplete":
        return True
    return bool(body.get("incomplete_details"))


def _validate_chapter_text(text) -> tuple[bool, str]:
    if not text or not str(text).strip():
        return False, "empty text"
    wc = _word_count(text)
    min_units = _min_content_units(CHAPTER_WORD_MIN, text)
    if wc < min_units:
        return False, f"content length {wc} below minimum {min_units}"
    if not _text_ends_complete_sentence(text):
        return False, "text does not end with a complete sentence"
    return True, ""


def _validate_section_text(text) -> tuple[bool, str]:
    if not text or not str(text).strip():
        return False, "empty text"
    wc = _word_count(text)
    min_units = _min_content_units(SECTION_WORD_MIN, text)
    if wc < min_units:
        return False, f"content length {wc} below minimum {min_units}"
    if not _text_ends_complete_sentence(text):
        return False, "text does not end with a complete sentence"
    return True, ""


def _validate_image_bytes(raw: bytes) -> tuple[bool, str]:
    size = len(raw or b"")
    if size < IMAGE_MIN_BYTES:
        return False, f"image too small ({size} bytes, minimum {IMAGE_MIN_BYTES})"
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return False, "not a valid PNG file"
    return True, ""


def filter_valid_text_results(merged, chapter_manifest, section_manifest):
    invalid_ids = set()
    for custom_id in list(merged.keys()):
        text = merged.get(custom_id)
        if custom_id in chapter_manifest:
            ok, reason = _validate_chapter_text(text)
        elif custom_id in section_manifest:
            ok, reason = _validate_section_text(text)
        else:
            continue
        if not ok:
            print(f"  VALIDATION FAIL {custom_id}: {reason}")
            invalid_ids.add(custom_id)
            merged.pop(custom_id, None)
    return merged, invalid_ids


def _chapters_from_structure(data: dict) -> list:
    struct = data.get("structure") if isinstance(data.get("structure"), dict) else {}
    chapters = struct.get("chapters")
    if isinstance(chapters, list):
        return chapters
    top = data.get("chapters")
    return top if isinstance(top, list) else []


def validate_book_structure(data: dict) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(data, dict):
        return False, ["root is not a JSON object"]

    metadata = data.get("metadata") or data.get("book_metadata")
    if not isinstance(metadata, dict):
        errors.append("missing metadata object")
    else:
        for key in METADATA_KEYS:
            if not str(metadata.get(key, "")).strip():
                errors.append(f"metadata.{key} missing or empty")

    ui_labels = data.get("ui_labels")
    if not isinstance(ui_labels, dict):
        errors.append("missing ui_labels object")
    else:
        for key in UI_LABEL_KEYS:
            if not str(ui_labels.get(key, "")).strip():
                errors.append(f"ui_labels.{key} missing or empty")

    struct = data.get("structure")
    if not isinstance(struct, dict):
        errors.append("missing structure object")
        struct = {}

    for key in SECTION_DESC_KEYS:
        if not str(struct.get(key, "")).strip():
            errors.append(f"structure.{key} missing or empty")

    chapters = _chapters_from_structure(data)
    if len(chapters) != ARCHITECT_EXPECTED_CHAPTERS:
        errors.append(f"expected {ARCHITECT_EXPECTED_CHAPTERS} chapters, got {len(chapters)}")
    for idx, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            errors.append(f"chapter {idx} is not an object")
            continue
        if not str(chapter.get("title", "")).strip():
            errors.append(f"chapter {idx} title missing or empty")
        if not str(chapter.get("description", "")).strip():
            errors.append(f"chapter {idx} description missing or empty")

    return len(errors) == 0, errors


def _response_is_incomplete(resp) -> bool:
    if getattr(resp, "status", None) == "incomplete":
        return True
    md = getattr(resp, "model_dump", None)
    if callable(md):
        d = md()
        if isinstance(d, dict) and (d.get("status") == "incomplete" or d.get("incomplete_details")):
            return True
    return False


def default_style(focus: str, language: str) -> str:
    return (
        f"Language: {language}. "
        f"Focus: {focus}. "
        "Tone: Warm, psychologically precise, compassionate, direct second-person. "
        "Voice rules: concrete language; grounded interpretation; practical guidance; "
        "emotionally honest pacing; no fluff. "
        "Avoid: generic filler; moralizing; vague advice; melodrama."
    )


def _section_batch_prompt(name, description, style, language):
    return f"""Generate narrative prose content for a personal astrology book section.
Section Type: {name}
Language: {language}
Style: {style}
Context: {description}
Word Contract: Target {SECTION_WORD_TARGET} words. Mandatory range {SECTION_WORD_MIN}-{SECTION_WORD_MAX} words.
Length Rule: Write until you satisfy the mandatory range, then stop. Do not exceed {SECTION_WORD_MAX} words.
Layout Rule: This section must fit on two printed pages. End with a complete sentence.
Paragraphing: Plain paragraphs only. Use at most 3-4 paragraph breaks.
STRICT RULES:
- Output only body text.
- No headings or titles.
- No markdown.
- No labels.
- Start directly with prose.
- Use second person POV."""


def build_section_batch_tasks(structure, style, language):
    struct_inner = structure.get("structure", {})
    preface_desc = structure.get("preface_description") or struct_inner.get("preface_description") or (
        "Write a warm, welcoming preface setting the stage for a journey of self-discovery based on the user's astrology."
    )
    prologue_desc = structure.get("prologue_description") or struct_inner.get("prologue_description") or (
        "Write an introduction that explains the core themes of the book and invites the reader to explore their inner world."
    )
    epilogue_desc = structure.get("epilogue_description") or struct_inner.get("epilogue_description") or (
        "Write a concluding chapter that synthesizes the journey, offering encouragement and a call to action for the future."
    )
    section_specs = [
        ("section-preface", "Preface", preface_desc),
        ("section-prologue", "Prologue", prologue_desc),
        ("section-epilogue", "Epilogue", epilogue_desc),
    ]
    tasks = []
    manifest = {}
    for custom_id, name, description in section_specs:
        prompt = _section_batch_prompt(name, description, style, language)
        tasks.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": BATCH_ENDPOINT_RESPONSES,
                "body": {
                    "model": MODEL_CONTENT,
                    "input": [{"role": "user", "content": prompt}],
                    "text": {
                        "format": {"type": "text"},
                        "verbosity": TEXT_VERBOSITY_ARCHITECT,
                    },
                    "reasoning": {"effort": REASONING_EFFORT_ARCHITECT},
                    "max_output_tokens": SECTION_MAX_OUTPUT_TOKENS,
                },
            }
        )
        manifest[custom_id] = {"section_name": name.lower(), "description": description}
        print(f"    Task '{custom_id}': {name}")
    return tasks, manifest


def _sections_from_batch_results(merged, section_manifest):
    section_results = {}
    for custom_id, meta in section_manifest.items():
        section_name = meta["section_name"]
        text = merged.get(custom_id, "")
        if text:
            section_results[f"{section_name}_text"] = text
    return section_results


# ---------------------------------------------------------------------------
# GPT-5.5 /v1/responses helpers (sync, async, and Batch output bodies)
# ---------------------------------------------------------------------------

def _extract_text_from_responses_body_dict(body: dict) -> str:
    """Parse visible text from a serialized Response object (e.g. Batch output `body`)."""
    if not isinstance(body, dict):
        return ""
    ot = body.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot.strip()
    chunks = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                t = part.get("text") or ""
                if t:
                    chunks.append(t)
    return "".join(chunks).strip()


def _response_text_from_obj(resp) -> str:
    """Readable text from a Responses API result object (OpenAI Python SDK)."""
    tx = getattr(resp, "output_text", None)
    if isinstance(tx, str) and tx.strip():
        return tx.strip()
    md = getattr(resp, "model_dump", None)
    if callable(md):
        d = md()
        if isinstance(d, dict):
            return _extract_text_from_responses_body_dict(d)
    return ""


# ===========================================================================
# STEP 1: Fetch Astrology Data  (unchanged)
# ===========================================================================

def call_astrology_api(endpoint, auth, payload):
    url = f"https://json.astrologyapi.com/v1/{endpoint}"
    print(f"  Calling {endpoint}...")
    try:
        resp = requests.post(url, auth=auth, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Error on {endpoint}: {e}")
        return None


def fetch_astrology(birth_data, order_id):
    print("\n" + "=" * 60)
    print("STEP 1: Fetching Astrology Data")
    print("=" * 60)

    western_auth = (ASTRO_WESTERN_UID, ASTRO_WESTERN_KEY)
    vedic_auth = (ASTRO_VEDIC_UID, ASTRO_VEDIC_KEY)

    today = datetime.now()
    sr_payload = {**birth_data, "sr_year": today.year}
    transit_payload = {**birth_data, "trans_date": today.strftime("%d-%m-%Y")}

    charts = {
        "AYANAMSHA": ("ayanamsha", vedic_auth, birth_data),
        "PLANETS_EXTENDED": ("planets/extended", western_auth, birth_data),
        "BHAV_MADHYA": ("astro_details", vedic_auth, birth_data),
        "WESTERN_HOROSCOPE": ("western_horoscope", western_auth, birth_data),
        "VDASHA": ("current_vdasha", vedic_auth, birth_data),
        "CHARDASHA": ("current_chardasha", vedic_auth, birth_data),
        "SOLAR_RETURN_HOUSES": ("solar_return_house_cusps", western_auth, sr_payload),
        "SOLAR_RETURN_PLANETS": ("solar_return_planets", western_auth, sr_payload),
        "SOLAR_RETURN_ASPECTS": ("solar_return_planet_aspects", western_auth, sr_payload),
        "TRANSITS": ("tropical_transits/daily", western_auth, transit_payload),
    }

    comprehensive_data = {
        "META": {
            "Order_ID": order_id,
            "Request_Date": today.isoformat(),
            "Input_Parameters": birth_data,
        },
        "CHARTS": {},
    }

    for key, (endpoint, auth, payload) in charts.items():
        comprehensive_data["CHARTS"][key] = {
            "Description": key,
            "Endpoint": endpoint,
            "Data": call_astrology_api(endpoint, auth, payload),
        }

    out_path = os.path.join(ARTIFACTS_DIR, "astrology_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(comprehensive_data, f, indent=2, ensure_ascii=False)

    print(f"  Saved -> {out_path}")
    return comprehensive_data


# ===========================================================================
# STEP 2: Architect Book Structure (GPT-5.5 Responses API)
# ===========================================================================

def architect_book(astrology_data, focus, language):
    print("\n" + "=" * 60)
    print("STEP 2: Architecting Book Structure for the focus: ", focus, " in language: ", language)
    print("=" * 60)

    system_prompt = ARCHITECT_SYSTEM_PROMPT.replace("__LANGUAGE__", language)
    user_prompt = (
        ARCHITECT_USER_PROMPT
        .replace("__FOCUS__", focus)
        .replace("__LANGUAGE__", language)
        .replace("__ASTROLOGY_DATA__", json.dumps(astrology_data, indent=2))
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    max_attempts = ARCHITECT_MAX_RETRIES + 1
    last_errors = []

    for attempt in range(1, max_attempts + 1):
        print(f"  Calling OpenAI Responses API (attempt {attempt}/{max_attempts})...")
        resp = client.responses.create(
            model=MODEL_CONTENT,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text={
                "format": {"type": "json_object"},
                "verbosity": TEXT_VERBOSITY_ARCHITECT,
            },
            reasoning={"effort": REASONING_EFFORT_ARCHITECT},
            max_output_tokens=ARCHITECT_MAX_OUTPUT_TOKENS,
        )

        if _response_is_incomplete(resp):
            last_errors = [f"response incomplete: {getattr(resp, 'incomplete_details', None)}"]
            print(f"  VALIDATION FAIL attempt {attempt}: {last_errors[0]}")
            continue

        raw = _response_text_from_obj(resp)
        if not raw:
            last_errors = ["empty model response"]
            print(f"  VALIDATION FAIL attempt {attempt}: empty model response")
            continue

        try:
            structure = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_errors = [f"invalid JSON: {exc}"]
            print(f"  VALIDATION FAIL attempt {attempt}: {last_errors[0]}")
            continue

        ok, errors = validate_book_structure(structure)
        if ok:
            chapters = _chapters_from_structure(structure)
            print(f"  Generated valid structure with {len(chapters)} chapters.")
            out_path = os.path.join(ARTIFACTS_DIR, "book_structure.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(structure, f, indent=2, ensure_ascii=False)
            print(f"  Saved -> {out_path}")
            return structure

        last_errors = errors
        print(f"  VALIDATION FAIL attempt {attempt}: {errors}")

    raise ValueError(
        f"Book structure invalid after {max_attempts} attempt(s): " + "; ".join(last_errors)
    )


# ===========================================================================
# STEP 3a: Build + Submit Chapter Text Batch
# ===========================================================================

def build_chapter_batch_tasks(chapters_list, astrology_data, focus, style, language, word_target):
    """Build a list of Batch API task dicts, one per chapter."""
    print("\n" + "-" * 50)
    print("  BATCH: Building chapter text tasks...")
    print("-" * 50)

    tasks = []
    manifest = {}

    for idx, ch in enumerate(chapters_list):
        chapter_num = idx + 1
        title = ch["title"]
        description = ch["description"]
        custom_id = f"chapter-{chapter_num}"

        prompt = (
            f'Write Chapter {chapter_num}: "{title}".\n'
            f"**Language:** {language}\n"
            f"**Style:** {style}\n"
            f"**Focus:** {focus}\n"
            f"**Summary:** {description}\n"
            f"**Book Contract:** The complete book targets ~{BOOK_WORD_TARGET} words total across all chapters.\n"
            f"**Word Contract:** Target {word_target} words for this chapter. Mandatory range {CHAPTER_WORD_MIN}-{CHAPTER_WORD_MAX} words.\n"
            f"**Length Rule:** Keep writing until you satisfy the mandatory range. Do not stop early.\n"
            f"**Depth Rule:** Cover (1) core pattern, (2) roots, (3) present-day behavior, (4) relationship dynamics, "
            f"(5) shadow expression, (6) reframing, (7) practical integration prompts.\n"
            f"**Formatting:** Plain paragraphs. No bold. No headers.\n"
            f"**Paragraphing (critical for layout):** Write like a printed book chapter, not chat.\n"
            f"- **Vary paragraph length deliberately.** Mix shorter paragraphs (often **3-5 sentences**, about **2–3 printed lines**) with medium and longer ones. "
            f"Do **not** settle into a steady rhythm where every paragraph is the same size (e.g. always four or five lines).\n"
            f"- **Short paragraphs are allowed** for emphasis, a turn in thought, or a breath between ideas—use them **sometimes**, not after every sentence.\n"
            f"- Longer paragraphs are fine when the idea needs room; neighbor paragraphs may be much shorter so the page does not look like uniform blocks.\n"
            f"- Use **single newlines** only when you must break a long paragraph; prefer joining sentences in the same paragraph with spaces.\n"
            f"- Use **double newlines (blank line)** ONLY between **major sections** (e.g. opening, each big thematic turn, closing). "
            f"**At most 8–10 double-newlines in the whole chapter.** Never put a double-newline after every sentence or quoted line.\n"
            f"- For examples or quoted phrases, weave them into prose or use **one** short block; do not stack many one-line blocks separated by blank lines.\n"
            f"**Output Rule:** Return only final chapter prose.\n"
            f"**Data:** {json.dumps(astrology_data)}"
        )

        task = {
            "custom_id": custom_id,
            "method": "POST",
            "url": BATCH_ENDPOINT_RESPONSES,
            "body": {
                "model": MODEL_CONTENT,
                "input": [{"role": "user", "content": prompt}],
                "reasoning": {"effort": REASONING_EFFORT_CHAPTER},
                "text": {
                    "format": {"type": "text"},
                    "verbosity": TEXT_VERBOSITY_CHAPTER,
                },
                "max_output_tokens": CHAPTER_MAX_OUTPUT_TOKENS,
            },
        }
        tasks.append(task)
        manifest[custom_id] = {"chapter_index": chapter_num, "chapter_title": title}

        print(f"    Task '{custom_id}': Chapter {chapter_num} - {title}")
        print(f"      Prompt length: {len(prompt):,} chars")
        print(f"      max_output_tokens: {CHAPTER_MAX_OUTPUT_TOKENS} (reasoning={REASONING_EFFORT_CHAPTER}, verbosity={TEXT_VERBOSITY_CHAPTER})")

    print(f"\n  BATCH: {len(tasks)} chapter tasks built.")
    return tasks, manifest


def submit_chapter_batch(
    client,
    tasks,
    manifest,
    artifact_prefix="chapter_text",
    endpoint="/v1/chat/completions",
):
    """Write JSONL, upload to OpenAI Files API, and create a Batch job."""
    print("\n" + "-" * 50)
    print("  BATCH: Writing JSONL input file...")
    print("-" * 50)

    jsonl_path = os.path.join(ARTIFACTS_DIR, f"{artifact_prefix}_batch_input.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")

    file_size = os.path.getsize(jsonl_path)
    print(f"    Saved -> {jsonl_path}")
    print(f"    File size: {file_size:,} bytes  ({file_size / 1024 / 1024:.2f} MB)")
    print(f"    Lines: {len(tasks)}")

    print("\n  BATCH: Uploading JSONL to OpenAI Files API (purpose='batch')...")
    with open(jsonl_path, "rb") as f:
        file_obj = client.files.create(file=f, purpose="batch")

    print(f"    Upload complete!")
    print(f"    File ID:  {file_obj.id}")
    print(f"    Filename: {file_obj.filename}")
    print(f"    Bytes:    {file_obj.bytes}")
    print(f"    Status:   {file_obj.status}")

    print(f"\n  BATCH: Creating batch job (endpoint={endpoint}, window=24h)...")
    batch_job = client.batches.create(
        input_file_id=file_obj.id,
        endpoint=endpoint,
        completion_window="24h",
        metadata={"description": f"{artifact_prefix}_generation"},
    )

    print(f"    Batch created!")
    print(f"    Batch ID:        {batch_job.id}")
    print(f"    Status:          {batch_job.status}")
    print(f"    Input file ID:   {batch_job.input_file_id}")
    print(f"    Created at:      {datetime.fromtimestamp(batch_job.created_at).isoformat()}")
    print(f"    Expires at:      {datetime.fromtimestamp(batch_job.expires_at).isoformat()}")

    manifest_data = {
        "batch_id": batch_job.id,
        "input_file_id": file_obj.id,
        "input_jsonl_path": jsonl_path,
        "created_at": batch_job.created_at,
        "artifact_prefix": artifact_prefix,
        "chapter_mapping": manifest,
    }
    manifest_path = os.path.join(ARTIFACTS_DIR, f"{artifact_prefix}_batch_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    print(f"    Manifest saved -> {manifest_path}")

    return batch_job.id


def poll_batch_until_done(client, batch_id):
    """Poll batch status until terminal state. Returns the final Batch object."""
    print("\n" + "-" * 50)
    print(f"  BATCH POLL: Waiting for batch {batch_id} ...")
    print("-" * 50)

    terminal_states = {"completed", "failed", "expired", "cancelled"}
    poll_count = 0
    start_time = time.time()

    while True:
        batch = client.batches.retrieve(batch_id)
        poll_count += 1
        elapsed = time.time() - start_time
        counts = batch.request_counts

        print(
            f"    [{poll_count:>3}] {elapsed:>6.0f}s | "
            f"Status: {batch.status:<14} | "
            f"Total: {counts.total}  Completed: {counts.completed}  Failed: {counts.failed}"
        )

        if batch.status in terminal_states:
            print(f"\n  BATCH POLL: Reached terminal state -> {batch.status}")
            if batch.status == "completed":
                print(f"    Output file ID:  {batch.output_file_id}")
                if batch.completed_at:
                    print(f"    Completed at:    {datetime.fromtimestamp(batch.completed_at).isoformat()}")
            elif batch.status == "failed":
                print(f"    Error file ID:   {batch.error_file_id}")
                if hasattr(batch, "errors") and batch.errors:
                    print(f"    Errors:          {batch.errors}")
            elif batch.status == "expired":
                print(f"    Output file ID:  {batch.output_file_id}  (partial results)")
                print(f"    Error file ID:   {batch.error_file_id}")
            return batch

        time.sleep(BATCH_POLL_INTERVAL)


def collect_chapter_batch_results(client, batch, manifest, artifact_prefix="chapter_text"):
    """Download batch output, parse JSONL, and return result/failure maps by custom_id."""
    print("\n" + "-" * 50)
    print("  BATCH RESULTS: Downloading output file...")
    print("-" * 50)

    if not batch.output_file_id:
        raise RuntimeError(f"Batch {batch.id} has no output_file_id (status={batch.status})")

    result_content = client.files.content(batch.output_file_id).content
    output_path = os.path.join(ARTIFACTS_DIR, f"{artifact_prefix}_batch_output.jsonl")
    with open(output_path, "wb") as f:
        f.write(result_content)
    print(f"    Saved -> {output_path}  ({len(result_content):,} bytes)")

    print("\n  BATCH RESULTS: Parsing response lines...")
    results_by_id = {}
    failed_ids = set()
    for line_num, line in enumerate(result_content.decode("utf-8").strip().split("\n"), 1):
        entry = json.loads(line)
        custom_id = entry["custom_id"]
        response = entry.get("response")
        error = entry.get("error")

        print(f"\n    --- Line {line_num}: custom_id={custom_id} ---")

        if error:
            print(f"    ERROR: {json.dumps(error, indent=2)}")
            failed_ids.add(custom_id)
            continue

        status_code = response.get("status_code")
        print(f"    HTTP status: {status_code}")

        if status_code != 200:
            print(f"    Non-200 response body: {json.dumps(response.get('body', {}), indent=2)[:500]}")
            failed_ids.add(custom_id)
            continue

        body = response["body"]
        print(f"    Model:           {body.get('model')}")
        print(f"    Response status: {body.get('status')}")
        if _batch_body_is_incomplete(body):
            print(f"    Incomplete:      {body.get('incomplete_details')}")
            failed_ids.add(custom_id)
            continue

        usage = body.get("usage", {}) or {}
        if "input_tokens" in usage or "output_tokens" in usage:
            print(
                f"    Tokens -> input: {usage.get('input_tokens', '?')}  "
                f"output: {usage.get('output_tokens', '?')}  "
                f"total: {usage.get('total_tokens', '?')}"
            )
            otd = usage.get("output_tokens_details") or {}
            if otd.get("reasoning_tokens") is not None:
                print(f"              reasoning (output token detail): {otd.get('reasoning_tokens')}")
        else:
            print(
                f"    Tokens -> prompt: {usage.get('prompt_tokens', '?')}  "
                f"completion: {usage.get('completion_tokens', '?')}  "
                f"total: {usage.get('total_tokens', '?')}"
            )

        chapter_text = _extract_text_from_responses_body_dict(body)
        if not chapter_text and isinstance(body, dict) and body.get("choices"):
            # Fallback: legacy chat.completions batch line shape
            try:
                chapter_text = (body["choices"][0]["message"]["content"] or "").strip()
            except (KeyError, IndexError, TypeError):
                chapter_text = ""

        print(f"    Text length:     {len(chapter_text):,} chars")

        if not chapter_text:
            print("    ERROR: Empty chapter text")
            failed_ids.add(custom_id)
            continue

        results_by_id[custom_id] = chapter_text

    print(f"\n  BATCH RESULTS: {len(results_by_id)}/{len(manifest)} chapters received successfully.")
    # Mark manifest IDs not returned in output as failed for retry.
    missing_ids = set(manifest.keys()) - set(results_by_id.keys())
    failed_ids |= missing_ids
    if missing_ids:
        print(f"  BATCH RESULTS: Missing IDs in output -> {sorted(missing_ids)}")

    return results_by_id, failed_ids


def build_chapters_data_from_results(results_by_id, manifest):
    """Build chapter list from merged batch results and write per-chapter artifacts."""
    chapters_data = []
    missing_ids = []

    for custom_id, info in manifest.items():
        idx = info["chapter_index"]
        title = info["chapter_title"]
        text = results_by_id.get(custom_id)

        if text is None:
            print(f"  WARNING: No final result for {custom_id} (Chapter {idx}: {title}) - skipping")
            missing_ids.append(custom_id)
            continue

        chapter_json = {"chapter_title": title, "chapter_text": text}
        ch_path = os.path.join(ARTIFACTS_DIR, f"chapter_{idx}.json")
        with open(ch_path, "w", encoding="utf-8") as f:
            json.dump(chapter_json, f, indent=2, ensure_ascii=False)
        print(f"    Saved artifact -> {ch_path}")

        chapters_data.append({
            "chapter_index": idx,
            "chapter_title": title,
            "chapter_text": text,
            "image_path": None,
        })

    chapters_data.sort(key=lambda x: x["chapter_index"])
    return chapters_data, missing_ids


# ===========================================================================
# STEP 3b: Generate Images (Batch API)
# ===========================================================================

async def build_image_batch_tasks(async_client, chapters_data):
    """Build image-generation batch tasks after creating a short summary per chapter."""
    print("\n" + "-" * 50)
    print("  IMAGE BATCH: Building image tasks...")
    print("-" * 50)

    tasks = []
    manifest = {}

    for ch in chapters_data:
        idx = ch["chapter_index"]
        title = ch["chapter_title"]
        text = ch["chapter_text"]
        custom_id = f"image-{idx}"

        summary = text[:700]
        try:
            sum_resp = await async_client.responses.create(
                model=MODEL_CONTENT,
                input=[{"role": "user", "content": f"Summarize text for image: {text[:1200]}"}],
                text={"format": {"type": "text"}, "verbosity": "low"},
                reasoning={"effort": "low"},
                max_output_tokens=IMAGE_SUMMARY_MAX_OUTPUT_TOKENS,
            )
            summary = _response_text_from_obj(sum_resp).strip()
        except Exception as e:
            print(f"    WARNING: Summary generation failed for chapter {idx}, using fallback excerpt. Error: {e}")

        img_prompt = (
            IMAGE_PROMPT_TEMPLATE
            .replace("__CHAPTER_TITLE__", title)
            .replace("__SUMMARY__", summary)
        )

        task = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/images/generations",
            "body": {
                "model": MODEL_IMAGE,
                "prompt": img_prompt,
                "n": 1,
                "size": "1024x1536",
            },
        }
        tasks.append(task)
        manifest[custom_id] = {"chapter_index": idx, "chapter_title": title}

        print(f"    Task '{custom_id}': Chapter {idx} - {title}")
        print(f"      Image prompt length: {len(img_prompt):,} chars")

    print(f"\n  IMAGE BATCH: {len(tasks)} image tasks built.")
    return tasks, manifest


def collect_image_batch_results(client, batch, manifest, artifact_prefix="chapter_image"):
    """Download image batch output JSONL and decode b64 images by custom_id."""
    print("\n" + "-" * 50)
    print("  IMAGE BATCH RESULTS: Downloading output file...")
    print("-" * 50)

    if not batch.output_file_id:
        raise RuntimeError(f"Batch {batch.id} has no output_file_id (status={batch.status})")

    result_content = client.files.content(batch.output_file_id).content
    output_path = os.path.join(ARTIFACTS_DIR, f"{artifact_prefix}_batch_output.jsonl")
    with open(output_path, "wb") as f:
        f.write(result_content)
    print(f"    Saved -> {output_path}  ({len(result_content):,} bytes)")

    image_bytes_by_id = {}
    failed_ids = set()
    print("\n  IMAGE BATCH RESULTS: Parsing response lines...")
    for line_num, line in enumerate(result_content.decode("utf-8").strip().split("\n"), 1):
        entry = json.loads(line)
        custom_id = entry["custom_id"]
        response = entry.get("response")
        error = entry.get("error")

        print(f"\n    --- Line {line_num}: custom_id={custom_id} ---")

        if error:
            print(f"    ERROR: {json.dumps(error, indent=2)}")
            failed_ids.add(custom_id)
            continue

        status_code = response.get("status_code")
        print(f"    HTTP status: {status_code}")
        if status_code != 200:
            print(f"    Non-200 response body: {json.dumps(response.get('body', {}), indent=2)[:500]}")
            failed_ids.add(custom_id)
            continue

        body = response.get("body", {})
        data_items = body.get("data", [])
        if not data_items:
            print("    ERROR: Empty image data array")
            failed_ids.add(custom_id)
            continue

        b64 = data_items[0].get("b64_json")
        if not b64:
            print("    ERROR: Missing b64_json in image response")
            failed_ids.add(custom_id)
            continue

        raw = base64.b64decode(b64)
        ok, reason = _validate_image_bytes(raw)
        if not ok:
            print(f"    VALIDATION FAIL {custom_id}: {reason}")
            failed_ids.add(custom_id)
            continue
        image_bytes_by_id[custom_id] = raw
        print(f"    Decoded image bytes: {len(raw):,}")

    missing_ids = set(manifest.keys()) - set(image_bytes_by_id.keys())
    failed_ids |= missing_ids
    if missing_ids:
        print(f"  IMAGE BATCH RESULTS: Missing IDs in output -> {sorted(missing_ids)}")

    print(f"\n  IMAGE BATCH RESULTS: {len(image_bytes_by_id)}/{len(manifest)} images received successfully.")
    return image_bytes_by_id, failed_ids


def apply_images_to_chapters(chapters_data, image_manifest, image_bytes_by_id):
    """Attach decoded images to chapter objects and save PNG files."""
    for ch in chapters_data:
        ch["image_path"] = None

    by_idx = {ch["chapter_index"]: ch for ch in chapters_data}
    missing_ids = []
    for custom_id, info in image_manifest.items():
        idx = info["chapter_index"]
        raw = image_bytes_by_id.get(custom_id)
        if raw is None:
            missing_ids.append(custom_id)
            continue

        img_path = os.path.join(IMAGES_DIR, f"chapter_{idx}.png")
        with open(img_path, "wb") as f:
            f.write(raw)
        print(f"    Saved image -> {img_path}  ({len(raw):,} bytes)")

        if idx in by_idx:
            by_idx[idx]["image_path"] = img_path

    return chapters_data, missing_ids


async def generate_chapter_images_batch(sync_client, async_client, chapters_data):
    """Run image generation via Batch API with targeted retry for failed IDs."""
    image_tasks, image_manifest = await build_image_batch_tasks(async_client, chapters_data)
    image_batch_id = submit_chapter_batch(
        sync_client,
        image_tasks,
        image_manifest,
        artifact_prefix="chapter_image",
        endpoint="/v1/images/generations",
    )
    image_batch = poll_batch_until_done(sync_client, image_batch_id)

    if image_batch.status not in {"completed", "expired"}:
        print(f"  WARNING: Image batch ended with status={image_batch.status}.")
        return chapters_data

    merged_images_by_id, failed_image_ids = collect_image_batch_results(
        sync_client, image_batch, image_manifest, artifact_prefix="chapter_image",
    )

    for retry_num in range(1, MAX_BATCH_RETRIES + 1):
        if not failed_image_ids:
            break

        retry_ids = sorted(failed_image_ids)
        print("\n" + "-" * 50)
        print(f"  IMAGE BATCH RETRY {retry_num}: Retrying {len(retry_ids)} failed/missing images...")
        print("-" * 50)
        print(f"    Retry IDs: {retry_ids}")

        retry_tasks = [task for task in image_tasks if task["custom_id"] in failed_image_ids]
        if not retry_tasks:
            print("    No retry image tasks found. Stopping retry loop.")
            break

        retry_batch_id = submit_chapter_batch(
            sync_client,
            retry_tasks,
            image_manifest,
            artifact_prefix=f"chapter_image_retry_{retry_num}",
            endpoint="/v1/images/generations",
        )
        retry_batch = poll_batch_until_done(sync_client, retry_batch_id)

        if retry_batch.status not in {"completed", "expired"}:
            print(f"  WARNING: Image retry batch {retry_num} ended with status={retry_batch.status}.")
            break

        retry_images_by_id, retry_failed_ids = collect_image_batch_results(
            sync_client,
            retry_batch,
            image_manifest,
            artifact_prefix=f"chapter_image_retry_{retry_num}",
        )
        merged_images_by_id.update(retry_images_by_id)
        failed_image_ids = set(retry_ids) - set(retry_images_by_id.keys())
        failed_image_ids |= (set(retry_failed_ids) & set(retry_ids))
        print(f"  IMAGE BATCH RETRY {retry_num}: Recovered {len(retry_images_by_id)} images.")
        if failed_image_ids:
            print(f"  IMAGE BATCH RETRY {retry_num}: Still missing -> {sorted(failed_image_ids)}")

    chapters_data, missing_images = apply_images_to_chapters(chapters_data, image_manifest, merged_images_by_id)
    if missing_images:
        print(f"  WARNING: Final missing image IDs after retries: {missing_images}")
    return chapters_data


# ===========================================================================
# STEP 3 orchestrator: Write Chapters (Batch) + Images (Batch)
# ===========================================================================

async def write_chapters(astrology_data, structure, focus, language):
    print("\n" + "=" * 60)
    print("STEP 3: Writing Chapters + Sections (Batch) + Images (Batch)")
    print("=" * 60)

    async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    sync_client = OpenAI(api_key=OPENAI_API_KEY)

    style = default_style(focus, language)
    print(f"  Style: {style[:80]}...")

    struct_inner = structure.get("structure", {})

    foreword_path = os.path.join(os.environ.get("LAMBDA_TASK_ROOT", "/app/generate_pdf"), "assets", "foreword.txt")
    try:
        with open(foreword_path, "r", encoding="utf-8") as f:
            foreword_text = f.read().strip()
    except FileNotFoundError:
        foreword_text = "Welcome to your Blueprint."
    print("  Foreword language fixed to English (translation skipped).")

    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    word_target = CHAPTER_WORD_TARGET

    print("\n" + "-" * 50)
    print("  BATCH: Building chapter + section text tasks...")
    print("-" * 50)
    chapter_tasks, chapter_manifest = build_chapter_batch_tasks(
        chapters_list, astrology_data, focus, style, language, word_target,
    )
    section_tasks, section_manifest = build_section_batch_tasks(structure, style, language)
    tasks = chapter_tasks + section_tasks
    text_manifest = {**chapter_manifest, **section_manifest}
    print(f"\n  BATCH: {len(tasks)} total text tasks ({len(chapter_tasks)} chapters + {len(section_tasks)} sections).")

    with open(os.path.join(ARTIFACTS_DIR, "text_chapter_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(chapter_manifest, f, indent=2, ensure_ascii=False)
    with open(os.path.join(ARTIFACTS_DIR, "text_section_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(section_manifest, f, indent=2, ensure_ascii=False)

    batch_id = submit_chapter_batch(
        sync_client,
        tasks,
        text_manifest,
        artifact_prefix="chapter_text",
        endpoint=BATCH_ENDPOINT_RESPONSES,
    )
    batch = poll_batch_until_done(sync_client, batch_id)

    if batch.status not in {"completed", "expired"}:
        raise RuntimeError(f"Chapter text batch ended with status: {batch.status}")

    merged_results_by_id, failed_ids = collect_chapter_batch_results(
        sync_client, batch, text_manifest, artifact_prefix="chapter_text",
    )
    merged_results_by_id, invalid_ids = filter_valid_text_results(
        merged_results_by_id, chapter_manifest, section_manifest,
    )
    failed_ids |= invalid_ids
    expected_ids = set(chapter_manifest.keys()) | set(section_manifest.keys())
    missing_ids = expected_ids - set(merged_results_by_id.keys())
    failed_ids |= missing_ids

    for retry_num in range(1, MAX_BATCH_RETRIES + 1):
        if not failed_ids:
            break

        retry_ids = sorted(failed_ids)
        print("\n" + "-" * 50)
        print(f"  BATCH RETRY {retry_num}: Retrying {len(retry_ids)} failed/missing/invalid items...")
        print("-" * 50)
        print(f"    Retry IDs: {retry_ids}")

        retry_tasks = [task for task in tasks if task["custom_id"] in failed_ids]
        if not retry_tasks:
            print("    No retry tasks found. Stopping retry loop.")
            break

        retry_batch_id = submit_chapter_batch(
            sync_client,
            retry_tasks,
            text_manifest,
            artifact_prefix=f"chapter_text_retry_{retry_num}",
            endpoint=BATCH_ENDPOINT_RESPONSES,
        )
        retry_batch = poll_batch_until_done(sync_client, retry_batch_id)

        if retry_batch.status not in {"completed", "expired"}:
            print(f"  WARNING: Retry batch {retry_num} ended with status={retry_batch.status}.")
            break

        retry_results_by_id, retry_failed_ids = collect_chapter_batch_results(
            sync_client,
            retry_batch,
            text_manifest,
            artifact_prefix=f"chapter_text_retry_{retry_num}",
        )
        merged_results_by_id.update(retry_results_by_id)
        merged_results_by_id, retry_invalid_ids = filter_valid_text_results(
            merged_results_by_id, chapter_manifest, section_manifest,
        )
        failed_ids = (set(retry_ids) - set(merged_results_by_id.keys())) | retry_invalid_ids
        failed_ids |= (set(retry_failed_ids) & set(retry_ids))
        print(f"  BATCH RETRY {retry_num}: Recovered {len(retry_results_by_id)} items.")
        if failed_ids:
            print(f"  BATCH RETRY {retry_num}: Still missing/invalid -> {sorted(failed_ids)}")

    chapter_results = {
        custom_id: text for custom_id, text in merged_results_by_id.items() if custom_id in chapter_manifest
    }
    missing_chapters = set(chapter_manifest.keys()) - set(chapter_results.keys())
    if missing_chapters:
        raise ValueError(
            "Chapter text validation failed after retries; missing or invalid: "
            + ", ".join(sorted(missing_chapters))
        )

    chapters_data, _ = build_chapters_data_from_results(chapter_results, chapter_manifest)
    if not chapters_data:
        raise ValueError("No chapter texts were produced by the batch job.")

    section_texts = _sections_from_batch_results(merged_results_by_id, section_manifest)
    preface_text = section_texts.get("preface_text", "")
    prologue_text = section_texts.get("prologue_text", "")
    epilogue_text = section_texts.get("epilogue_text", "")
    missing_sections = [
        name for name, text in (
            ("Preface", preface_text),
            ("Prologue", prologue_text),
            ("Epilogue", epilogue_text),
        )
        if not _validate_section_text(text)[0]
    ]
    if missing_sections:
        raise ValueError(
            "Section text validation failed after retries; missing or invalid: "
            + ", ".join(missing_sections)
        )

    for key, text in (
        ("preface", preface_text),
        ("prologue", prologue_text),
        ("epilogue", epilogue_text),
    ):
        sec_path = os.path.join(ARTIFACTS_DIR, f"section_{key}.json")
        with open(sec_path, "w", encoding="utf-8") as f:
            json.dump({"section_name": key, "section_text": text}, f, indent=2, ensure_ascii=False)
        print(f"    Saved section artifact -> {sec_path}")

    chapters_data = await generate_chapter_images_batch(sync_client, async_client, chapters_data)

    metadata = structure.get("metadata", struct_inner.get("metadata", {}))

    return {
        "metadata": metadata,
        "preface_text": preface_text,
        "prologue_text": prologue_text,
        "epilogue_text": epilogue_text,
        "foreword_text": foreword_text,
        "chapters_data": chapters_data,
    }


# ===========================================================================
# STEP 4: Generate PDF  (unchanged)
# ===========================================================================

def generate_pdf(write_result, birth_data, language, focus=None):
    print("\n" + "=" * 60)
    print("STEP 4: Generating PDF")
    print("=" * 60)

    metadata = write_result["metadata"]
    title = metadata.get("title", "The Architecture of You")

    book_data = {
        "metadata": metadata,
        "birth_data": birth_data,
        "preface_text": write_result["preface_text"],
        "prologue_text": write_result["prologue_text"],
        "epilogue_text": write_result["epilogue_text"],
        "focus": (focus or "").strip(),
        "chapters": [],
    }

    for ch in write_result["chapters_data"]:
        book_data["chapters"].append({
            "heading": ch["chapter_title"],
            "content": ch["chapter_text"],
            "image_path": ch.get("image_path"),
        })

    timestamp = int(time.time())
    filename = f"book_{timestamp}.pdf"

    print(f"  Rendering PDF: {filename}")
    output_path, page_count = save_book_as_pdf(
        title=title,
        book_data=book_data,
        filename=filename,
        output_dir=OUTPUT_DIR,
        language=language,
        openai_api_key=None,
    )

    print(f"  PDF written -> {output_path}")
    print(f"  Page count:  {page_count}")
    return output_path, page_count


# ===========================================================================
# Main
# ===========================================================================

def main():
    config_path = "/app/pipeline_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    birth_data = config["birth_data"]
    order_id = config.get("order_id", "LOCAL_TEST")
    focus = config.get("focus", "Personality")
    language = config.get("language", "English")

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    print(f"Pipeline (BATCH) started at {datetime.now().isoformat()}")
    print(f"  Focus:    {focus}")
    print(f"  Language: {language}")
    print(f"  Birth:    {json.dumps(birth_data)}")
    start = time.time()

    astrology_data = fetch_astrology(birth_data, order_id)
    structure = architect_book(astrology_data, focus, language)
    write_result = asyncio.run(write_chapters(astrology_data, structure, focus, language))

    sections_path = os.path.join(ARTIFACTS_DIR, "generated_sections.json")
    with open(sections_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "preface_text": write_result["preface_text"],
                "prologue_text": write_result["prologue_text"],
                "epilogue_text": write_result["epilogue_text"],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Saved -> {sections_path}")

    pdf_path, page_count = generate_pdf(write_result, birth_data, language, focus=focus)

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE (BATCH)")
    print("=" * 60)
    print(f"  PDF:        {pdf_path}")
    print(f"  Pages:      {page_count}")
    print(f"  Elapsed:    {elapsed:.1f}s")
    print(f"  Artifacts:  {ARTIFACTS_DIR}")
    print(f"  Images:     {IMAGES_DIR}")


if __name__ == "__main__":
    main()
