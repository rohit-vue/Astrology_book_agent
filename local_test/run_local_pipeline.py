#!/usr/bin/env python3
"""
Local end-to-end book generation pipeline.
Replicates the full production Lambda pipeline locally:
  1. Fetch astrology data (astrologyapi.com)
  2. Architect book structure (OpenAI)
  3. Write chapters + generate images (OpenAI + gpt-image-1-mini)
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
from datetime import datetime

import base64
import requests
import httpx  # pyright: ignore[reportMissingImports]
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI  # pyright: ignore[reportMissingImports]

sys.path.insert(0, "/app/generate_pdf")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from book_pdf_exporter import save_book_as_pdf # pyright: ignore[reportMissingImports]
from structured_schemas import book_structure_schema, chat_response_format

load_dotenv("/app/.env")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ASTRO_WESTERN_UID = os.environ["ASTROLOGY_WESTERN_USER_ID"]
ASTRO_WESTERN_KEY = os.environ["ASTROLOGY_WESTERN_API_KEY"]
ASTRO_VEDIC_UID = os.environ["ASTROLOGY_VEDIC_USER_ID"]
ASTRO_VEDIC_KEY = os.environ["ASTROLOGY_VEDIC_API_KEY"]

MODEL_TEXT = "gpt-5.4-mini-2026-03-17"
SECTION_WORD_TARGET = 550
SECTION_WORD_MIN = 500
SECTION_WORD_MAX = 600
SECTION_MAX_TOKENS = 2000
MODEL_IMAGE = "gpt-image-1-mini"
MODEL_STABLE = "gpt-4o"

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
The Book Title, Chapter Titles, Themes, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

**TASK:**
Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

**CHAPTER OBJECT RULES (CRITICAL):**
Each chapter object MUST contain exactly these three fields:
- "title": a poetic, publishable chapter heading (what appears in the table of contents).
- "theme": a short conceptual topic / lens for the chapter (what the chapter is about).
- "description": a detailed writing brief that expands on the theme for the chapter writer.
"title" and "theme" MUST be meaningfully different. Never copy the title into theme, and never paraphrase the title as the theme.
Good: title="Begin Where Your Nervous System Feels Safe", theme="Inner safety before outer expansion"
Bad: title="Begin Where Your Nervous System Feels Safe", theme="Begin Where Your Nervous System Feels Safe"

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
      {
        "title": "Chapter Title (in __LANGUAGE__)",
        "theme": "Short conceptual theme distinct from title (in __LANGUAGE__)",
        "description": "A detailed summary (in __LANGUAGE__)."
      }
    ]
  }
}

**Comprehensive Astrological Data:**
__ASTROLOGY_DATA__"""

IMAGE_PROMPT_TEMPLATE = "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. Style: ethereal, cosmic, rich colors. CRITICAL: NO text, letters, or figures."

# Same template as terraform SSM /AstrologyBookFactory/prompts/writer/style_analysis
STYLE_PROMPT_TEMPLATE = (
    "Analyze the following astrological data. Based on its core energies, describe the ideal "
    "writing tone and style for a personal book about '__FOCUS__' in **__LANGUAGE__**. "
    "Keep it concise.\n\nDATA:\n__ASTROLOGY_DATA__"
)


# ===========================================================================
# STEP 1: Fetch Astrology Data
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
    transit_payload = {**birth_data, "trans_date": today.strftime("%d-%m-%Y")}

    charts = {
        "WESTERN_HOROSCOPE": ("western_horoscope", western_auth, birth_data),
        "NATAL_TRANSITS": ("natal_transits/daily", western_auth, transit_payload),
        "PLANETS": ("planets", western_auth, birth_data),
        "SHADBALA": ("shadbala", vedic_auth, birth_data),
        "BHAVABALA": ("bhavabala", vedic_auth, birth_data),
        "VDASHA": ("current_vdasha", vedic_auth, birth_data),
    }

    comprehensive_data = {
        "META": {
            "Request_Date": today.isoformat(),
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
# STEP 2: Architect Book Structure
# ===========================================================================

def architect_book(astrology_data, focus, language):
    print("\n" + "=" * 60)
    print("STEP 2: Architecting Book Structure")
    print("=" * 60)

    system_prompt = ARCHITECT_SYSTEM_PROMPT.replace("__LANGUAGE__", language)
    user_prompt = (
        ARCHITECT_USER_PROMPT
        .replace("__FOCUS__", focus)
        .replace("__LANGUAGE__", language)
        .replace("__ASTROLOGY_DATA__", json.dumps(astrology_data, indent=2))
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    print("  Calling OpenAI to architect book structure (json_schema)...")
    # Non-batch local script still uses fixed 7 chapters in its prompt.
    schema = book_structure_schema(7, 7)
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=chat_response_format("book_structure", schema),
        temperature=0.3,
    )

    structure = json.loads(resp.choices[0].message.content)
    chapters = structure.get("structure", {}).get("chapters", [])
    print(f"  Generated structure with {len(chapters)} chapters.")
    for i, ch in enumerate(chapters, start=1):
        title = str(ch.get("title", "")).strip()
        theme = str(ch.get("theme", "")).strip()
        if not theme:
            raise ValueError(f"chapter {i} missing theme")
        if title.casefold().strip() == theme.casefold().strip():
            raise ValueError(f"chapter {i} theme must differ from title: {title!r}")
        print(f"    Chapter {i}: {title} | theme: {theme}")

    out_path = os.path.join(ARTIFACTS_DIR, "book_structure.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    print(f"  Saved -> {out_path}")
    return structure


# ===========================================================================
# STEP 3: Write Chapters + Images
# ===========================================================================

async def generate_section(client, name, description, style, language):
    if not description:
        return ""
    print(f"  Generating {name}...")
    prompt = f"""
    Write the {name} for a personal astrology book.
    Language: {language}
    Style: {style}
    Context: {description}
    **Word Contract:** Target {SECTION_WORD_TARGET} words. Mandatory range {SECTION_WORD_MIN}-{SECTION_WORD_MAX} words.
    **Length Rule:** Write until you satisfy the mandatory range, then stop. Do not exceed {SECTION_WORD_MAX} words.
    **Layout Rule:** This section must fit on two printed pages. End with a complete sentence.
    **Paragraphing:** Plain paragraphs only. Use at most 3-4 paragraph breaks in the whole section.
    Directive: Write in second person ("You"). Start directly. Plain text only.
    """
    try:
        resp = await client.chat.completions.create(
            model=MODEL_STABLE,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=SECTION_MAX_TOKENS,
        )
        text = resp.choices[0].message.content.strip()
        word_count = len(text.split())
        print(f"  {name} done: {word_count} words, {len(text)} chars")
        return text
    except Exception as e:
        print(f"  Error generating {name}: {e}")
        return ""


async def write_single_chapter(client, idx, details, chart, target, focus, style, language):
    title = str(details.get("title", "")).strip()
    theme = str(details.get("theme", "")).strip()
    description = str(details.get("description", "")).strip()
    if not theme:
        raise ValueError(f"chapter {idx} missing theme (refusing to default theme to title)")
    if title.casefold() == theme.casefold():
        raise ValueError(f"chapter {idx} theme equals title; refusing to proceed")
    print(f"  Starting Chapter {idx}: {title} | theme: {theme}")

    prompt = f"""
    Write Chapter {idx}: "{title}".
    **Language:** {language}
    **Style:** {style}
    **Focus:** {focus}
    **Theme:** {theme}
    **Summary:** {description}
    **Word Target:** {target}
    **Formatting:** Plain paragraphs. No bold. No headers.
    **Data:** {json.dumps(chart)}
    """
    text_resp = await client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )
    text = text_resp.choices[0].message.content.strip()

    img_path = None
    try:
        sum_resp = await client.chat.completions.create(
            model=MODEL_TEXT,
            messages=[{"role": "user", "content": f"Summarize text for image: {text[:1000]}"}],
            max_completion_tokens=100,
        )
        summary = sum_resp.choices[0].message.content.strip()
        img_prompt = IMAGE_PROMPT_TEMPLATE.replace("__CHAPTER_TITLE__", title).replace("__SUMMARY__", summary)
        img_resp = await client.images.generate(
            model=MODEL_IMAGE,
            prompt=img_prompt,
            n=1,
            size="1024x1536",
            quality="medium",
            background="opaque",
        )
        img_item = img_resp.data[0]
        img_path = os.path.join(IMAGES_DIR, f"chapter_{idx}.png")
        if getattr(img_item, "b64_json", None):
            raw = base64.b64decode(img_item.b64_json)
            with open(img_path, "wb") as f:
                f.write(raw)
        elif getattr(img_item, "url", None):
            async with httpx.AsyncClient() as http:
                dl = await http.get(img_item.url)
                dl.raise_for_status()
                with open(img_path, "wb") as f:
                    f.write(dl.content)
        else:
            raise RuntimeError("Image response has neither b64_json nor url")
        print(f"  Chapter {idx} image saved -> {img_path}")
    except Exception as e:
        print(f"  Chapter {idx} image generation failed: {e}")

    chapter_json = {"chapter_title": title, "chapter_text": text}
    ch_path = os.path.join(ARTIFACTS_DIR, f"chapter_{idx}.json")
    with open(ch_path, "w", encoding="utf-8") as f:
        json.dump(chapter_json, f, indent=2, ensure_ascii=False)

    print(f"  Chapter {idx} text saved -> {ch_path}")
    return {
        "chapter_index": idx,
        "chapter_title": title,
        "chapter_text": text,
        "image_path": img_path,
    }


async def write_chapters(astrology_data, structure, focus, language):
    print("\n" + "=" * 60)
    print("STEP 3: Writing Chapters + Generating Images")
    print("=" * 60)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    # Style analysis (same prompt shape as prod generate_writing_style)
    style_prompt = (
        STYLE_PROMPT_TEMPLATE
        .replace("__FOCUS__", focus)
        .replace("__LANGUAGE__", language)
        .replace("__ASTROLOGY_DATA__", json.dumps(astrology_data, ensure_ascii=False))
    )
    print("  Generating writing style profile...")
    try:
        style_resp = await client.chat.completions.create(
            model=MODEL_TEXT,
            messages=[{"role": "user", "content": style_prompt}],
            max_completion_tokens=600,
        )
        style = (style_resp.choices[0].message.content or "").strip()
        if not style:
            raise ValueError("empty style analysis response")
    except Exception as e:
        raise RuntimeError(f"Style generation failed; refusing default_style fallback: {e}") from e
    print(f"  Style: {style[:120]}...")
    style_path = os.path.join(ARTIFACTS_DIR, "writing_style.txt")
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(style)
    print(f"  Saved -> {style_path}")

    # Resolve descriptions from structure
    struct_inner = structure.get("structure", {})

    preface_desc = structure.get("preface_description") or struct_inner.get("preface_description") or \
        "Write a warm, welcoming preface setting the stage for a journey of self-discovery based on the user's astrology."
    prologue_desc = structure.get("prologue_description") or struct_inner.get("prologue_description") or \
        "Write an introduction that explains the core themes of the book and invites the reader to explore their inner world."
    epilogue_desc = structure.get("epilogue_description") or struct_inner.get("epilogue_description") or \
        "Write a concluding chapter that synthesizes the journey, offering encouragement and a call to action for the future."

    # Foreword (from file, same as prod)
    foreword_path = os.path.join(os.environ.get("LAMBDA_TASK_ROOT", "/app/generate_pdf"), "assets", "foreword.txt")
    try:
        with open(foreword_path, "r", encoding="utf-8") as f:
            foreword_text = f.read().strip()
    except FileNotFoundError:
        foreword_text = "Welcome to your Blueprint."

    if language.lower() != "english":
        print(f"  Translating foreword to {language}...")
        try:
            trans_resp = await client.chat.completions.create(
                model=MODEL_STABLE,
                messages=[{"role": "user", "content": f"Translate the following text into {language}. Maintain the poetic, warm, and serious tone. Do not add commentary.\n\nTEXT:\n{foreword_text}"}],
                temperature=0.3,
            )
            foreword_text = trans_resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"  Foreword translation failed: {e}")

    # Generate preface + prologue
    preface_text = await generate_section(client, "Preface", preface_desc, style, language)
    prologue_text = await generate_section(client, "Prologue", prologue_desc, style, language)

    # Write all chapters in parallel
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    word_target = 7750

    tasks = [
        write_single_chapter(client, idx + 1, ch, astrology_data, word_target, focus, style, language)
        for idx, ch in enumerate(chapters_list)
    ]
    results = await asyncio.gather(*tasks)
    chapters_data = sorted([r for r in results if r is not None], key=lambda x: x["chapter_index"])

    if not chapters_data:
        raise ValueError("All chapter writing tasks failed.")

    # Generate epilogue
    epilogue_text = await generate_section(client, "Epilogue", epilogue_desc, style, language)

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
# STEP 4: Generate PDF
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

    print(f"Pipeline started at {datetime.now().isoformat()}")
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
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  PDF:        {pdf_path}")
    print(f"  Pages:      {page_count}")
    print(f"  Elapsed:    {elapsed:.1f}s")
    print(f"  Artifacts:  {ARTIFACTS_DIR}")
    print(f"  Images:     {IMAGES_DIR}")


if __name__ == "__main__":
    main()
