#!/usr/bin/env python3
"""
Local end-to-end book generation pipeline (BATCH API variant).
Uses the OpenAI Batch API for chapter text generation (50% cheaper, higher rate limits).
Images are still generated synchronously after batch text results arrive.

Pipeline steps:
  1. Fetch astrology data (astrologyapi.com)
  2. Architect book structure (OpenAI)
  3a. Submit chapter text requests as a Batch job (OpenAI Batch API)
  3b. Poll until batch completes, then collect chapter texts
  3c. Generate chapter images synchronously (gpt-image-1-mini)
  4. Generate PDF (book_pdf_exporter.py)

Usage:
  docker compose run --rm pipeline              # full pipeline (APIs + PDF)
  docker compose run --rm pdf-from-artifacts    # PDF only from output/artifacts (no API calls)
"""
import sys
import os
import io
import json
import asyncio
import time
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

MODEL_TEXT = "gpt-5.2-2025-12-11"
MODEL_IMAGE = "gpt-image-1.5"
MODEL_STABLE = "gpt-4o"

BATCH_POLL_INTERVAL = 15  # seconds between status checks
CHAPTER_WORD_TARGET = 10000
CHAPTER_WORD_MIN = 9000
CHAPTER_WORD_MAX = 10500
CHAPTER_MAX_COMPLETION_TOKENS = 12000
MAX_BATCH_RETRIES = 1

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
# STEP 2: Architect Book Structure  (unchanged)
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
    print("  Calling OpenAI to architect book structure...")
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    structure = json.loads(resp.choices[0].message.content)
    chapters = structure.get("structure", {}).get("chapters", [])
    print(f"  Generated structure with {len(chapters)} chapters.")

    out_path = os.path.join(ARTIFACTS_DIR, "book_structure.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    print(f"  Saved -> {out_path}")
    return structure


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
            f"**Word Contract:** Target {word_target} words. Mandatory range {CHAPTER_WORD_MIN}-{CHAPTER_WORD_MAX} words.\n"
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
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL_TEXT,
                "temperature": 0.5,
                "max_completion_tokens": CHAPTER_MAX_COMPLETION_TOKENS,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
            },
        }
        tasks.append(task)
        manifest[custom_id] = {"chapter_index": chapter_num, "chapter_title": title}

        print(f"    Task '{custom_id}': Chapter {chapter_num} - {title}")
        print(f"      Prompt length: {len(prompt):,} chars")
        print(f"      Completion budget: {CHAPTER_MAX_COMPLETION_TOKENS} max tokens")

    print(f"\n  BATCH: {len(tasks)} chapter tasks built.")
    return tasks, manifest


def build_style_chart_snapshot(astrology_data):
    """Build a compact chart snapshot so style generation stays chart-aware but concise."""
    charts = astrology_data.get("CHARTS", {})
    western = charts.get("WESTERN_HOROSCOPE", {}).get("Data", {}) or {}
    planets = western.get("planets", []) or []
    aspects = western.get("aspects", []) or []

    top_planets = []
    for p in planets[:8]:
        name = p.get("name")
        sign = p.get("sign")
        house = p.get("house")
        if name and sign is not None and house is not None:
            top_planets.append(f"{name} in {sign} (house {house})")

    top_aspects = []
    for a in aspects[:8]:
        ap = a.get("aspecting_planet")
        bp = a.get("aspected_planet")
        at = a.get("type")
        if ap and bp and at:
            top_aspects.append(f"{ap} {at} {bp}")

    return {
        "ascendant": western.get("ascendant"),
        "top_planets": top_planets,
        "top_aspects": top_aspects,
    }


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
        print(f"    Model:         {body.get('model')}")
        print(f"    Finish reason: {body['choices'][0].get('finish_reason')}")

        usage = body.get("usage", {})
        print(f"    Tokens -> prompt: {usage.get('prompt_tokens', '?')}  "
              f"completion: {usage.get('completion_tokens', '?')}  "
              f"total: {usage.get('total_tokens', '?')}")

        chapter_text = body["choices"][0]["message"]["content"].strip()
        print(f"    Text length:   {len(chapter_text):,} chars")

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
            sum_resp = await async_client.chat.completions.create(
                model=MODEL_TEXT,
                messages=[{"role": "user", "content": f"Summarize text for image: {text[:1200]}"}],
                max_completion_tokens=120,
            )
            summary = sum_resp.choices[0].message.content.strip()
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

    if image_batch.status != "completed":
        if image_batch.status == "expired" and image_batch.output_file_id:
            print("  WARNING: Image batch expired but has partial results — collecting what's available.")
        else:
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

        if retry_batch.status != "completed":
            if retry_batch.status == "expired" and retry_batch.output_file_id:
                print(f"  WARNING: Image retry batch {retry_num} expired with partial output. Collecting partial results.")
            else:
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
# STEP 3: Sections helper  (unchanged)
# ===========================================================================

async def generate_section(client, name, description, style, language):
    if not description:
        return ""
    print(f"  Generating {name}...")
    prompt = f"""
    Generate narrative prose content for a personal astrology book section.

    Section Type: {name}
    Language: {language}
    Style: {style}
    Context: {description}

    STRICT RULES:
    - Output ONLY body text.
    - No headings or titles.
    - No markdown.
    - No labels.
    - Start directly with prose.
    - Second person POV ("You").
    """
    try:
        resp = await client.chat.completions.create(
            model=MODEL_STABLE,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800,
        )
        text = resp.choices[0].message.content.strip()
        print(f"  {name} done: {len(text)} chars")
        return text
    except Exception as e:
        print(f"  Error generating {name}: {e}")
        return ""


# ===========================================================================
# STEP 3 orchestrator: Write Chapters (Batch) + Images (sync)
# ===========================================================================

async def write_chapters(astrology_data, structure, focus, language):
    print("\n" + "=" * 60)
    print("STEP 3: Writing Chapters (Batch API) + Generating Images")
    print("=" * 60)

    async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    sync_client = OpenAI(api_key=OPENAI_API_KEY)

    # --- Style analysis (short + chart-aware + structured output) ---
    style_chart = build_style_chart_snapshot(astrology_data)
    try:
        style_resp = await async_client.chat.completions.create(
            model=MODEL_TEXT,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a concise writing style profile for a personal astrology book.\n"
                    f"Focus: {focus}\n"
                    f"Language: {language}\n"
                    f"Chart snapshot: {json.dumps(style_chart, ensure_ascii=False)}\n\n"
                    "Return strict JSON with keys:\n"
                    "{"
                    "\"tone\":\"max 20 words\","
                    "\"voice_rules\":[\"max 5 short rules\"],"
                    "\"avoid\":[\"max 4 short anti-patterns\"]"
                    "}\n"
                    "Do not add any text outside JSON."
                )
            }],
            response_format={"type": "json_object"},
            max_completion_tokens=300,
        )
        style_json = json.loads(style_resp.choices[0].message.content)
        tone = (style_json.get("tone") or "").strip()
        rules = [r.strip() for r in style_json.get("voice_rules", []) if isinstance(r, str) and r.strip()]
        avoids = [a.strip() for a in style_json.get("avoid", []) if isinstance(a, str) and a.strip()]

        style = (
            f"Tone: {tone}. "
            f"Voice rules: {'; '.join(rules[:5])}. "
            f"Avoid: {'; '.join(avoids[:4])}."
        )
    except Exception as e:
        print(f"  Style generation failed, using fallback style: {e}")
        style = "Tone: Warm, psychologically precise, compassionate, direct second-person. Voice rules: concrete language; grounded interpretation; practical guidance; emotionally honest pacing; no fluff. Avoid: generic filler; moralizing; vague advice; melodrama."
    print(f"  Style: {style[:80]}...")

    # --- Resolve descriptions from structure ---
    struct_inner = structure.get("structure", {})

    preface_desc = structure.get("preface_description") or struct_inner.get("preface_description") or \
        "Write a warm, welcoming preface setting the stage for a journey of self-discovery based on the user's astrology."
    prologue_desc = structure.get("prologue_description") or struct_inner.get("prologue_description") or \
        "Write an introduction that explains the core themes of the book and invites the reader to explore their inner world."
    epilogue_desc = structure.get("epilogue_description") or struct_inner.get("epilogue_description") or \
        "Write a concluding chapter that synthesizes the journey, offering encouragement and a call to action for the future."

    # --- Foreword ---
    foreword_path = os.path.join(os.environ.get("LAMBDA_TASK_ROOT", "/app/generate_pdf"), "assets", "foreword.txt")
    try:
        with open(foreword_path, "r", encoding="utf-8") as f:
            foreword_text = f.read().strip()
    except FileNotFoundError:
        foreword_text = "Welcome to your Blueprint."

    print("  Foreword language fixed to English (translation skipped).")

    # --- Generate preface + prologue (still async, small sections) ---
    preface_text = await generate_section(async_client, "Preface", preface_desc, style, language)
    prologue_text = await generate_section(async_client, "Prologue", prologue_desc, style, language)

    # --- BATCH: chapter text generation ---
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    word_target = CHAPTER_WORD_TARGET

    tasks, manifest = build_chapter_batch_tasks(
        chapters_list, astrology_data, focus, style, language, word_target,
    )

    batch_id = submit_chapter_batch(sync_client, tasks, manifest, artifact_prefix="chapter_text")
    batch = poll_batch_until_done(sync_client, batch_id)

    if batch.status != "completed":
        if batch.status == "expired" and batch.output_file_id:
            print("  WARNING: Batch expired but has partial results — collecting what's available.")
        else:
            raise RuntimeError(f"Chapter text batch ended with status: {batch.status}")

    merged_results_by_id, failed_ids = collect_chapter_batch_results(
        sync_client, batch, manifest, artifact_prefix="chapter_text",
    )

    # Retry only failed/missing custom_ids once.
    for retry_num in range(1, MAX_BATCH_RETRIES + 1):
        if not failed_ids:
            break

        retry_ids = sorted(failed_ids)
        print("\n" + "-" * 50)
        print(f"  BATCH RETRY {retry_num}: Retrying {len(retry_ids)} failed/missing chapters...")
        print("-" * 50)
        print(f"    Retry IDs: {retry_ids}")

        retry_tasks = [task for task in tasks if task["custom_id"] in failed_ids]
        if not retry_tasks:
            print("    No retry tasks found. Stopping retry loop.")
            break

        retry_batch_id = submit_chapter_batch(
            sync_client,
            retry_tasks,
            manifest,
            artifact_prefix=f"chapter_text_retry_{retry_num}",
        )
        retry_batch = poll_batch_until_done(sync_client, retry_batch_id)

        if retry_batch.status != "completed":
            if retry_batch.status == "expired" and retry_batch.output_file_id:
                print(f"  WARNING: Retry batch {retry_num} expired with partial output. Collecting partial results.")
            else:
                print(f"  WARNING: Retry batch {retry_num} ended with status={retry_batch.status}.")
                break

        retry_results_by_id, retry_failed_ids = collect_chapter_batch_results(
            sync_client,
            retry_batch,
            manifest,
            artifact_prefix=f"chapter_text_retry_{retry_num}",
        )

        # Merge successful retry outputs; keep tracking only still-failed IDs.
        merged_results_by_id.update(retry_results_by_id)
        failed_ids = set(retry_ids) - set(retry_results_by_id.keys())
        failed_ids |= (set(retry_failed_ids) & set(retry_ids))
        print(f"  BATCH RETRY {retry_num}: Recovered {len(retry_results_by_id)} chapters.")
        if failed_ids:
            print(f"  BATCH RETRY {retry_num}: Still missing -> {sorted(failed_ids)}")

    chapters_data, missing_ids = build_chapters_data_from_results(merged_results_by_id, manifest)
    if missing_ids:
        print(f"  WARNING: Final missing chapter IDs after retries: {missing_ids}")

    if not chapters_data:
        raise ValueError("No chapter texts were produced by the batch job.")

    # --- Generate images via Batch API ---
    chapters_data = await generate_chapter_images_batch(sync_client, async_client, chapters_data)

    # --- Generate epilogue ---
    epilogue_text = await generate_section(async_client, "Epilogue", epilogue_desc, style, language)

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

def generate_pdf(write_result, birth_data, language):
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

    pdf_path, page_count = generate_pdf(write_result, birth_data, language)

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
