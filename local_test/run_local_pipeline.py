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
import re
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
from structured_schemas import (
    STYLE_FIELD_MAX_LENGTH,
    WRITING_STYLE_FIELDS,
    book_structure_schema,
    book_structure_schema_strict,
    chat_response_format,
    flatten_writing_style,
    normalize_writing_style,
    style_json_for_writer,
    validate_writing_style_object,
    writing_style_schema,
)
from chart_material import (
    chapter_material_mode,
    chapter_material_preview,
    enrich_structure_with_chart_snapshots,
    is_freeform_chapter_material,
    prepare_chapter_input_material,
    validate_astrology_artifact,
    validate_book_chart_coverage,
    validate_chapter_input_material_used,
    writer_chart_material_rule,
)

load_dotenv("/app/.env")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

_CHAPTER_NUM_HEADER_RE = re.compile(
    r"^\s*Chapter\s+\d+\s*:\s*[^\n]+(?:\n+|$)",
    re.IGNORECASE,
)


def _strip_echoed_chapter_header(text: str, chapter_title: str | None = None) -> str:
    """Remove a leading 'Chapter N: …' (or bare title) echo from model output."""
    cleaned = str(text or "")
    if not cleaned.strip():
        return cleaned

    cleaned, n = _CHAPTER_NUM_HEADER_RE.subn("", cleaned, count=1)
    if n:
        cleaned = cleaned.lstrip("\n\r ")

    title = str(chapter_title or "").strip()
    if title:
        parts = cleaned.split("\n", 1)
        first = parts[0].strip().strip("*#_ ").strip("\"'")
        if first.casefold() == title.casefold():
            cleaned = (parts[1] if len(parts) > 1 else "").lstrip("\n\r ")

    return cleaned

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ASTRO_WESTERN_UID = os.environ["ASTROLOGY_WESTERN_USER_ID"]
ASTRO_WESTERN_KEY = os.environ["ASTROLOGY_WESTERN_API_KEY"]
ASTRO_VEDIC_UID = os.environ["ASTROLOGY_VEDIC_USER_ID"]
ASTRO_VEDIC_KEY = os.environ["ASTROLOGY_VEDIC_API_KEY"]

MODEL_TEXT = "gpt-5.6-sol"
SECTION_WORD_TARGET = 550
SECTION_WORD_MIN = 500
SECTION_WORD_MAX = 600
SECTION_MAX_TOKENS = 2000
MODEL_IMAGE = "gpt-image-1-mini"
MODEL_STABLE = "gpt-5.6-sol"

OUTPUT_DIR = "/app/output"
ARTIFACTS_DIR = os.path.join(OUTPUT_DIR, "artifacts")
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")

# ---------------------------------------------------------------------------
# SSM prompts (hardcoded from Terraform)
# ---------------------------------------------------------------------------

ARCHITECT_SYSTEM_PROMPT = """You are an ASI (Artificial Superintelligence) acting as a master psychological interpreter and book architect.
Your persona is wise, insightful, and empathetic.
**CRITICAL INSTRUCTION:** You MUST output your response in **__LANGUAGE__**.
You MUST design the book through the lens of "__FOCUS__"."""

ARCHITECT_USER_PROMPT = """**CRITICAL LANGUAGE REQUIREMENT:**
The Book Title, Chapter Titles, Themes, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

**TASK:**
Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

**CHAPTER OBJECT RULES (CRITICAL):**
Each chapter object MUST contain exactly these four fields:
- "title": a poetic, publishable chapter heading (what appears in the table of contents). Max 70 characters including spaces.
- "theme": a short conceptual topic / lens for the chapter (what the chapter is about).
- "description": a detailed writing brief that expands on the theme for the chapter writer.
- "chapter_input_material_used": a JSON object with "chapter_focus" (structured chart cues) and "notes" (free-form architect blob).
  Do NOT invent chart facts. Copy/condense real factors from the Comprehensive Astrological Data.
  IMPORTANT: chapter_input_material_used is the ONLY chart material the chapter writer will receive (no chart_snapshot, no full chart dump).
  chapter_focus MUST include:
  - "rationale": why these cues are primary for this chapter
  - "western_cues": dense strings from WESTERN_HOROSCOPE. Prefix EVERY cue with "western". Use WESTERN_HOROSCOPE signs/houses only. For planets include name, sign, house, norm_degree, full_degree, is_retro. For aspects include type and orb. For angles include degree when present.
  - "planets_cues": dense strings from PLANETS. Prefix EVERY cue with "vedic". Include sign, nakshatra, house, awastha, isRetro, normDegree, fullDegree when present. Vedic houses/signs will not match western houses.
  - "shadbala_cues": array of compact strings from SHADBALA strengths
  - "bhavabala_cues": array of compact strings from BHAVABALA / house strengths. Prefix EVERY cue with "bhavabala" and the Sanskrit name (Sukha, Putra, Dhana, etc.). Never treat these as western houses.
  - "vdasha_cues": copy planet + period dates only from VDASHA. Do not add psychological meanings.
  - "transit_cues": dense strings from NATAL_TRANSITS for today (transit planet, aspect, natal point, signs/houses, retro when available)
  "notes" MUST be a string (use "" if nothing extra): free-form guidance for the writer—narrative angle, emotional emphasis, connections between cues, what to foreground or avoid. Do NOT repeat chart facts already in chapter_focus; add craft/direction the cues alone cannot carry.
  HOUSE SYSTEM RULE (CRITICAL):
  Western houses, Vedic PLANETS houses, and Bhavabala houses are different maps. Never merge them (e.g. do not imply western house 4 and bhavabala house 4 Sukha are the same sign).
  BOOK COVERAGE + UNIQUENESS (CRITICAL):
  - Coverage: across ALL chapters combined, significant chart factors must appear at least once: every WESTERN planet (including Node, Chiron, Part of Fortune, Lilith if present), Ascendant, Midheaven, every natal aspect with orb <= 2.0, every PLANETS body (including Rahu, Ketu, Ascendant), SHADBALA strongest planet and any not-strong planet, BHAVABALA strongest and weakest houses (use Sanskrit names), the full current VDASHA stack, every outer-planet transit (Jupiter/Saturn/Uranus/Neptune/Pluto) that is present, and any transit to ASC/MC/IC/DC.
  - Primary home: assign each significant factor to ONE primary chapter (the theme it actually serves).
  - Reuse: overlap is allowed only for book-level anchors (the current dasha stack, and at most 1-2 signature transits for the day). Other cues must not be copied into more than 2-3 chapters.
  - Soft caps per family when that family matters: western_cues 6-10, planets_cues 4-8, transit_cues 4-8, shadbala_cues 2-6, bhavabala_cues 2-6, vdasha_cues 2-5.
  - Empty arrays only when that family truly has nothing relevant. Prefer at least one cue in western_cues.
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
        "title": "Chapter Title (in __LANGUAGE__, max 70 chars)",
        "theme": "Short conceptual theme distinct from title (in __LANGUAGE__)",
        "description": "A detailed summary (in __LANGUAGE__).",
        "chapter_input_material_used": {
          "chapter_focus": {
            "rationale": "Why these chart factors matter for this chapter (in __LANGUAGE__).",
            "western_cues": ["western Sun Aquarius house 10 norm_degree 6.12 full_degree 306.12 is_retro=false", "western Sun Conjunction Midheaven orb 0.8"],
            "planets_cues": ["vedic Sun Capricorn nakshatra Shravan house 10 awastha Yuva normDegree 6.12 fullDegree 306.12 isRetro=false"],
            "shadbala_cues": ["Sun strong 118% of minimum"],
            "bhavabala_cues": ["bhavabala house 4 Sukha Aries 46% of baseline", "bhavabala house 5 Putra Taurus weakest"],
            "vdasha_cues": ["Current major Jupiter 22-8-2024 to 23-8-2031"],
            "transit_cues": ["Transit Uranus Gemini Conjunction natal IC house 4 retro=false"]
          },
          "notes": "Free-form writer guidance: narrative hook, emotional through-line, what to emphasize beyond the cues (in __LANGUAGE__)."
        }
      }
    ]
  }
}

**Comprehensive Astrological Data:**
__ASTROLOGY_DATA__"""

ARCHITECT_USER_PROMPT_FREEFORM = """**CRITICAL LANGUAGE REQUIREMENT:**
The Book Title, Chapter Titles, Themes, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

**TASK:**
Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

**CHAPTER OBJECT RULES (CRITICAL):**
Each chapter object MUST contain exactly these four fields:
- "title": a poetic, publishable chapter heading (what appears in the table of contents). Max 70 characters including spaces.
- "theme": a short conceptual topic / lens for the chapter (what the chapter is about).
- "description": a detailed writing brief that expands on the theme for the chapter writer.
- "chapter_input_material_used": an open JSON object (additionalProperties allowed).
  CRITICAL — SELECT SOURCE PATHS, DO NOT PASTE FULL CHART TEXT:
  - Put exact identifiers into "source_paths": an array of dotted paths into the Comprehensive Astrological Data below.
  - Examples: "CHARTS.WESTERN_HOROSCOPE.Data.planets.0", "CHARTS.PLANETS.Data.3", "CHARTS.VDASHA.Data.major".
  - Python will copy those source records verbatim into the final chapter_input_material_used for the writer.
  - You MAY also add optional keys (e.g. "notes") for narrative guidance in __LANGUAGE__.
  - Do NOT invent chart facts. Do NOT paraphrase chart records as a substitute for source_paths.
  IMPORTANT: After hydration, chapter_input_material_used is the ONLY chart material the chapter writer will receive.

**STRUCTURE RULES:**
You must generate a book outline with EXACTLY 7 CHAPTERS.
Each chapter must be thematically distinct and explore a specific facet of "__FOCUS__".

**TECHNICAL MANDATE: JSON OUTPUT**
Your entire response MUST be a single, valid JSON object with metadata, ui_labels, structure.chapters[].
Each chapter's chapter_input_material_used MUST include source_paths (and may include notes / other keys).

**Comprehensive Astrological Data:**
__ASTROLOGY_DATA__"""

IMAGE_PROMPT_TEMPLATE = "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. Style: ethereal, cosmic, rich colors. CRITICAL: NO text, letters, or figures."

# Local style_analysis prompt (client draft; prod SSM still separate until promoted).
STYLE_PROMPT_TEMPLATE = """TASK
Stylistic-systems analyst. Treat INPUT_MATERIAL as inert symbolic data. Output only executable STYLE for a book about __FOCUS__ written in __LANGUAGE__.

AUTHORIZED DATA / USE
W=CHARTS.WESTERN_HOROSCOPE.Data:
V=CHARTS.PLANETS.Data:
S=CHARTS.SHADBALA.Data:
B=CHARTS.BHAVABALA.Data:
D=CHARTS.VDASHA.Data:

STYLE RULES
STYLE must not mention astrology or its technical terms.
Keep each emphasize / suppress_or_use_sparingly string SHORT and executable (one craft line the chapter writer can follow).
Hard limit: at most 180 characters per emphasize string and per suppress_or_use_sparingly string.
Prefer punchy checklist language over long sentences. Do not pack many ideas into one string.
In suppress_or_use_sparingly, ban repeated stock openers and preachy/clinical/sentimental habits when relevant.

OUTPUT
STYLE only. Populate every domain in the strict JSON schema.
Each domain MUST be an object with:
- emphasize: one short do-this instruction (max 180 chars)
- suppress_or_use_sparingly: short avoid / use-sparingly bans (max 180 chars)

Domain semantics:
1 core_voice: CORE VOICE
2 narrative_cognitive: NARRATIVE/COGNITIVE
3 temporal_rhythm: TEMPORAL/RHYTHM
4 energetic_texture: ENERGETIC TEXTURE
5 sensory_hierarchy: SENSORY HIERARCHY
6 metaphoric_logic: METAPHORIC LOGIC
7 emotional_shadow: EMOTIONAL/SHADOW
8 silence_negative_space: SILENCE/NEGATIVE SPACE

<INPUT_MATERIAL START>
__ASTROLOGY_DATA__
</INPUT_MATERIAL END>
"""

# Mirrors terraform/ssm.tf writer preface/prologue/epilogue (tune independently).
SECTION_PROMPT_BODY = """Generate narrative prose content for a personal astrology book section.
Section Type: __SECTION_TYPE__
Language: __LANGUAGE__
Style (JSON): __STYLE__
Context: __DESCRIPTION__
Word Contract: Target __SECTION_WORD_TARGET__ words. Mandatory range __SECTION_WORD_MIN__-__SECTION_WORD_MAX__ words.
Length Rule: Write until you satisfy the mandatory range, then stop. Do not exceed __SECTION_WORD_MAX__ words.
Layout Rule: This section must fit on two printed pages. End with a complete sentence.
Paragraphing: Plain paragraphs only. Use at most 3-4 paragraph breaks.
STRICT RULES:
- Output only body text.
- No headings or titles.
- No markdown.
- No labels.
- Start directly with prose.
- Use second person POV."""

SECTION_PROMPT_TEMPLATES = {
    "preface": SECTION_PROMPT_BODY,
    "prologue": SECTION_PROMPT_BODY,
    "epilogue": SECTION_PROMPT_BODY,
}


def render_section_prompt(name, description, style, language):
    key = str(name or "").strip().lower()
    template = SECTION_PROMPT_TEMPLATES.get(key)
    if not template:
        raise ValueError(f"unknown section type for prompt template: {name!r}")
    return (
        template.replace("__SECTION_TYPE__", name)
        .replace("__LANGUAGE__", language)
        .replace("__STYLE__", style)
        .replace("__DYNAMIC_STYLE__", style)
        .replace("__DESCRIPTION__", description)
        .replace("__CONTEXT__", description)
        .replace("__SECTION_WORD_TARGET__", str(SECTION_WORD_TARGET))
        .replace("__SECTION_WORD_MIN__", str(SECTION_WORD_MIN))
        .replace("__SECTION_WORD_MAX__", str(SECTION_WORD_MAX))
        .replace("__WORD_TARGET__", str(SECTION_WORD_TARGET))
    )


STYLE_AUTHORIZED_CHART_KEYS = (
    "WESTERN_HOROSCOPE",
    "PLANETS",
    "SHADBALA",
    "BHAVABALA",
    "VDASHA",
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

    validate_astrology_artifact(comprehensive_data)

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

    system_prompt = (
        ARCHITECT_SYSTEM_PROMPT
        .replace("__LANGUAGE__", language)
        .replace("__FOCUS__", focus)
    )
    user_prompt_template = (
        ARCHITECT_USER_PROMPT_FREEFORM
        if is_freeform_chapter_material()
        else ARCHITECT_USER_PROMPT
    )
    user_prompt = (
        user_prompt_template
        .replace("__FOCUS__", focus)
        .replace("__LANGUAGE__", language)
        .replace("__ASTROLOGY_DATA__", json.dumps(astrology_data, indent=2))
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    material_mode = chapter_material_mode()
    print(f"  Calling OpenAI to architect book structure (json_schema, mode={material_mode})...")
    schema = book_structure_schema(7, 7, chapter_material_mode=material_mode)
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=chat_response_format(
            "book_structure",
            schema,
            strict=book_structure_schema_strict(material_mode),
        ),
        temperature=0.3,
    )

    structure = json.loads(resp.choices[0].message.content)
    # Freeform: hydrate source_paths → source_records before validation.
    structure = enrich_structure_with_chart_snapshots(structure, astrology_data)
    chapters = structure.get("structure", {}).get("chapters", [])
    for i, ch in enumerate(chapters, start=1):
        title = str(ch.get("title", "")).strip()
        theme = str(ch.get("theme", "")).strip()
        if not theme:
            raise ValueError(f"chapter {i} missing theme")
        if title.casefold().strip() == theme.casefold().strip():
            raise ValueError(f"chapter {i} theme must differ from title: {title!r}")
        material_errors = validate_chapter_input_material_used(
            ch.get("chapter_input_material_used"), i
        )
        if material_errors:
            raise ValueError("; ".join(material_errors))
    if is_freeform_chapter_material():
        print("  Cue coverage checks skipped (freeform mode).")
    else:
        coverage_warnings = validate_book_chart_coverage(chapters, astrology_data)
        if not coverage_warnings:
            print("  Cue coverage checks: ok (no soft warnings).")
        else:
            print(f"  Cue coverage soft warnings ({len(coverage_warnings)}):")
            for w in coverage_warnings:
                print(f"    - {w}")
    print(f"  Generated structure with {len(chapters)} chapters.")
    for i, ch in enumerate(chapters, start=1):
        title = str(ch.get("title", "")).strip()
        theme = str(ch.get("theme", "")).strip()
        material = prepare_chapter_input_material(
            ch.get("chapter_input_material_used"),
            astrology_data,
        )
        ch["chapter_input_material_used"] = material
        print(
            f"    Chapter {i}: {title} | theme: {theme} | {chapter_material_preview(material)}"
        )

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
    prompt = render_section_prompt(name, description, style, language)
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
    material = details.get("chapter_input_material_used")
    if not theme:
        raise ValueError(f"chapter {idx} missing theme (refusing to default theme to title)")
    if title.casefold() == theme.casefold():
        raise ValueError(f"chapter {idx} theme equals title; refusing to proceed")
    material_errors = validate_chapter_input_material_used(material, idx)
    if material_errors:
        raise ValueError("; ".join(material_errors))
    if not isinstance(material, dict):
        raise ValueError(
            f"chapter {idx} chapter_input_material_used missing or not an object"
        )
    material = prepare_chapter_input_material(material, astrology_data)
    material_json = json.dumps(material, ensure_ascii=False)
    full_chart_chars = len(json.dumps(chart, ensure_ascii=False))
    print(f"  Starting Chapter {idx}: {title} | theme: {theme}")
    print(f"    Material: {chapter_material_preview(material)}")
    print(
        f"    chapter_input_material_used: {len(material_json):,} chars "
        f"(mode={chapter_material_mode()}; full chart dump removed: ~{full_chart_chars:,} chars saved)"
    )

    prompt = f"""
    Write Chapter {idx}: "{title}".
    **Language:** {language}
    **Style (JSON):** {style}
    **Focus:** {focus}
    **Theme:** {theme}
    **Summary:** {description}
    **Chapter input material used:** {material_json}
    {writer_chart_material_rule()}
    **House system rule:** Western houses, Vedic planet houses, and Bhavabala houses are different maps. Translate each family into lived language separately. Do not merge them into one house story (do not write as if house 4 is both Gemini and Aries).
    **Language rule (critical):** Translate chart factors into clear lived language (feelings, patterns, choices, relationships, habits). Do NOT write like a chart reading. Avoid or minimize astrology jargon (planet names, houses, aspects, signs, Midheaven, bhava, natal/transit labels) unless a term is briefly useful; prefer everyday wording.
    **Word Target:** {target}
    **Formatting:** Plain paragraphs. No bold. No headers.
    **Output Rule:** Return only final chapter prose. Do not begin with "Chapter {idx}:" or the chapter title. Start directly with body prose.
    """
    text_resp = await client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )
    text = _strip_echoed_chapter_header(
        (text_resp.choices[0].message.content or "").strip(),
        title,
    )

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

    # Style analysis (8 domains x emphasize/suppress; local client prompt)
    charts = (astrology_data or {}).get("CHARTS") or {}
    style_material = {"CHARTS": {}}
    for key in STYLE_AUTHORIZED_CHART_KEYS:
        block = charts.get(key)
        if isinstance(block, dict) and block.get("Data") is not None:
            style_material["CHARTS"][key] = block["Data"]
    style_prompt = (
        STYLE_PROMPT_TEMPLATE
        .replace("__FOCUS__", focus)
        .replace("__LANGUAGE__", language)
        .replace("__ASTROLOGY_DATA__", json.dumps(style_material, ensure_ascii=False))
    )
    print("  Generating writing style profile (json_schema, emphasize/suppress)...")
    try:
        style_resp = await client.chat.completions.create(
            model=MODEL_TEXT,
            messages=[{"role": "user", "content": style_prompt}],
            response_format=chat_response_format("writing_style", writing_style_schema()),
            max_completion_tokens=4000,
        )
        raw_style = (style_resp.choices[0].message.content or "").strip()
        if not raw_style:
            raise ValueError("empty style analysis response")
        style_obj = json.loads(raw_style)
        errors = validate_writing_style_object(style_obj)
        if errors:
            raise ValueError("style analysis validation failed: " + "; ".join(errors))
        normalized = normalize_writing_style(style_obj)
        style = style_json_for_writer(normalized)
        style_readable = flatten_writing_style(normalized)
    except Exception as e:
        raise RuntimeError(f"Style generation failed; refusing default_style fallback: {e}") from e
    print(f"  Style (JSON) for writer: {style[:160]}...")
    style_path = os.path.join(ARTIFACTS_DIR, "writing_style.txt")
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(style_readable)
    print(f"  Saved -> {style_path}")
    style_json_path = os.path.join(ARTIFACTS_DIR, "writing_style.json")
    with open(style_json_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    print(f"  Saved -> {style_json_path}")
    print(
        f"  Generated writing style profile (writer JSON {len(style)} chars, "
        f"{len(WRITING_STYLE_FIELDS)} domains, max {STYLE_FIELD_MAX_LENGTH} chars/field)"
    )

    # Resolve descriptions from structure (no hardcoded fallbacks).
    struct_inner = structure.get("structure", {}) if isinstance(structure.get("structure"), dict) else {}
    missing_section_descs = []
    preface_desc = str(structure.get("preface_description") or struct_inner.get("preface_description") or "").strip()
    prologue_desc = str(structure.get("prologue_description") or struct_inner.get("prologue_description") or "").strip()
    epilogue_desc = str(structure.get("epilogue_description") or struct_inner.get("epilogue_description") or "").strip()
    if not preface_desc:
        missing_section_descs.append("preface_description")
    if not prologue_desc:
        missing_section_descs.append("prologue_description")
    if not epilogue_desc:
        missing_section_descs.append("epilogue_description")
    if missing_section_descs:
        raise ValueError(
            "missing section descriptions (no fallbacks): " + ", ".join(missing_section_descs)
        )

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
    print(f"  chapter_input_material_used mode: {chapter_material_mode()}")
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
