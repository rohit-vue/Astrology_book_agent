#!/usr/bin/env python3
"""
Local end-to-end book generation pipeline (BATCH API variant).
Uses the OpenAI Batch API for chapter text via /v1/responses (GPT-5.6 Sol).
Images use Batch /v1/images/generations (gpt-image-2).

Pipeline steps:
  1. Fetch astrology data (astrologyapi.com)
  2. Architect book structure (OpenAI Responses API, GPT-5.6 Sol, json_schema) with validation + retry
  3a. Style analysis (Responses API json_schema, 8-field STYLE) then submit chapter + section text as one Batch job
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from book_pdf_exporter import save_book_as_pdf
except ModuleNotFoundError:
    from book_pdf_exporter_local import save_book_as_pdf
from structured_schemas import (
    CHAPTER_TITLE_MAX_LENGTH,
    STYLE_FIELD_MAX_LENGTH,
    WRITING_STYLE_FIELDS,
    book_structure_schema,
    book_structure_schema_strict,
    flatten_writing_style,
    normalize_writing_style,
    responses_text_format,
    style_json_for_writer,
    validate_writing_style_object,
    writing_style_schema,
)
from chart_material import (
    build_architect_model_input,
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


def _env_flag(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in ("1", "true", "yes", "on")


def _architect_forced_retry_errors() -> list[str]:
    """Local smoke-test hook: force one guided Architect retry with realistic errors."""
    if not _env_flag("ARCHITECT_FORCE_RETRY_GUIDE_ONCE"):
        return []

    custom = os.environ.get("ARCHITECT_FORCE_RETRY_GUIDE_ERROR", "").strip()
    if custom:
        return [line.strip() for line in custom.splitlines() if line.strip()]

    return [
        "chapter 1 mixes conflicting Ascendant signs without system-separation "
        "notes (western Aquarius vs vedic Capricorn). Keep them in different "
        "chapters, or add notes that name Western and Vedic as distinct maps "
        "and forbid reconciling them.",
        "chapter 5 mixes western and vedic house number(s) [10] without "
        "system-separation notes. Split systems across chapters, or add notes "
        "that keep Western and Vedic house maps distinct (do not merge).",
    ]


OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ASTRO_WESTERN_UID = os.environ["ASTROLOGY_WESTERN_USER_ID"]
ASTRO_WESTERN_KEY = os.environ["ASTROLOGY_WESTERN_API_KEY"]
ASTRO_VEDIC_UID = os.environ["ASTROLOGY_VEDIC_USER_ID"]
ASTRO_VEDIC_KEY = os.environ["ASTROLOGY_VEDIC_API_KEY"]

# GPT-5.6 Sol for local batch test (Architect, style, chapters/sections). Images unchanged.
MODEL_CONTENT = "gpt-5.6-sol"
MODEL_IMAGE = "gpt-image-2"

# Chapter batch: medium reasoning + medium verbosity; reserve headroom for reasoning + ~10k-word prose
REASONING_EFFORT_CHAPTER = "medium"
TEXT_VERBOSITY_CHAPTER = "medium"
CHAPTER_MAX_OUTPUT_TOKENS = 25000

# Architect / long prose (sync Responses)
REASONING_EFFORT_ARCHITECT = "high"
TEXT_VERBOSITY_ARCHITECT = "high"
ARCHITECT_MAX_OUTPUT_TOKENS = 100000
ARCHITECT_MIN_CHAPTERS = 1
ARCHITECT_MAX_CHAPTERS = 14
ARCHITECT_MAX_RETRIES = 2

# Style profile (structured JSON — keep reasoning moderate, text less verbose)
REASONING_EFFORT_STYLE = "medium"
TEXT_VERBOSITY_STYLE = "low"
# Nested emphasize/suppress per domain needs more headroom than flat strings.
STYLE_MAX_OUTPUT_TOKENS = 4000

# Chart slices the style model is authorized to use (client style_analysis prompt).
STYLE_AUTHORIZED_CHART_KEYS = (
    "WESTERN_HOROSCOPE",
    "PLANETS",
    "SHADBALA",
    "BHAVABALA",
    "VDASHA",
)

# Preface / prologue / epilogue (batched with chapters).
# Separate from Architect: short sections do not need high reasoning (burns max_output_tokens).
REASONING_EFFORT_SECTION = "medium"
TEXT_VERBOSITY_SECTION = "medium"
SECTION_MAX_OUTPUT_TOKENS = 6000
SECTION_WORD_TARGET = 550
SECTION_WORD_MIN = 500
SECTION_WORD_MAX = 600
IMAGE_SUMMARY_MAX_OUTPUT_TOKENS = 200
IMAGE_MIN_BYTES = 50000

BATCH_POLL_INTERVAL = 30  # seconds between status checks
# Fixed per-chapter length; total book length scales with chapter count (5–14).
CHAPTER_WORD_TARGET = 4000
CHAPTER_WORD_MIN = 1000
CHAPTER_WORD_MAX = 5000
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
**CRITICAL INSTRUCTION:** You MUST output your response in **__LANGUAGE__**.
You MUST design the book through the lens of "__FOCUS__"."""

ARCHITECT_USER_PROMPT = """**CRITICAL LANGUAGE REQUIREMENT:**
The Book Title, Chapter Titles, Themes, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

**TASK:**
Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

    **RULES FOR THE MAIN BOOK TITLE AND CHAPTER TITLES:**
    - Maximum 70 total characters INCLUDING spaces.
    - Prefer Maximum 10-11 words total for the book title.

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
Choose how many chapters the book needs based on the astrological data and "__FOCUS__".
You MUST output between __MIN_CHAPTERS__ and __MAX_CHAPTERS__ chapter objects (inclusive).
Do not output fewer than __MIN_CHAPTERS__ or more than __MAX_CHAPTERS__.
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

    **RULES FOR THE MAIN BOOK TITLE AND CHAPTER TITLES:**
    - Maximum 70 total characters INCLUDING spaces.
    - Prefer Maximum 10-11 words total for the book title.

**CHAPTER OBJECT RULES (CRITICAL):**
Each chapter object MUST contain exactly these four fields:
- "title": a poetic, publishable chapter heading (what appears in the table of contents). Max 70 characters including spaces.
- "theme": a short conceptual topic / lens for the chapter (what the chapter is about).
- "description": a detailed writing brief that expands on the theme for the chapter writer.
- "chapter_input_material_used": an open JSON object (additionalProperties allowed).
  CRITICAL — SELECT SOURCE PATHS, DO NOT PASTE FULL CHART TEXT:
  - Put exact identifiers into "source_paths": an array of dotted paths into the Comprehensive Astrological Data below.
  - Examples: "CHARTS.WESTERN_HOROSCOPE.Data.planets.0", "CHARTS.PLANETS.Data.3", "CHARTS.VDASHA.Data.major", "CHARTS.BHAVABALA.Data.houses.1", "CHARTS.NATAL_TRANSITS.Data.transit_relation.2".
  - Python will copy those source records verbatim into the final chapter_input_material_used for the writer.
  - You MAY also add optional keys (e.g. "notes") for narrative guidance in __LANGUAGE__.
  - Do NOT invent chart facts. Do NOT paraphrase chart records as a substitute for source_paths.
  - Prefer several precise paths per chapter over one huge branch.
  - Western houses, Vedic planet houses, and Bhavabala houses are different maps — select paths from the correct branch; never merge house systems in notes.
  - HARD VALIDATION: If one chapter includes both a Western Ascendant/1st-house cusp and a Vedic Ascendant with different signs, notes MUST name Western and Vedic as distinct maps and forbid reconciling them (or put them in different chapters).
  - HARD VALIDATION: If one chapter uses the same house number from both Western and Vedic records, notes MUST keep those house maps distinct (e.g. name Western and Vedic, or say do not merge / remain distinct). Unguided mixes are rejected.
  IMPORTANT: After hydration, chapter_input_material_used is the ONLY chart material the chapter writer will receive.
"title" and "theme" MUST be meaningfully different. Never copy the title into theme, and never paraphrase the title as the theme.

**STRUCTURE RULES:**
Choose how many chapters the book needs based on the astrological data and "__FOCUS__".
You MUST output between __MIN_CHAPTERS__ and __MAX_CHAPTERS__ chapter objects (inclusive).
Do not output fewer than __MIN_CHAPTERS__ or more than __MAX_CHAPTERS__.
Each chapter must be thematically distinct and explore a specific facet of "__FOCUS__".

**TECHNICAL MANDATE: JSON OUTPUT**
Your entire response MUST be a single, valid JSON object.
{
  "metadata": { "...": "..." },
  "ui_labels": { "...": "..." },
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
          "source_paths": [
            "CHARTS.WESTERN_HOROSCOPE.Data.planets.0",
            "CHARTS.PLANETS.Data.1",
            "CHARTS.VDASHA.Data.major"
          ],
          "notes": "Optional writer guidance in __LANGUAGE__."
        }
      }
    ]
  }
}

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


def _normalize_chapter_label(value: str) -> str:
    """Normalize title/theme for equality checks (case/punct/whitespace insensitive)."""
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def validate_book_structure(data: dict, astrology_data=None) -> tuple[bool, list[str]]:
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
    n = len(chapters)
    if n < ARCHITECT_MIN_CHAPTERS or n > ARCHITECT_MAX_CHAPTERS:
        errors.append(
            f"expected {ARCHITECT_MIN_CHAPTERS}-{ARCHITECT_MAX_CHAPTERS} chapters, got {n}"
        )
    for idx, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            errors.append(f"chapter {idx} is not an object")
            continue
        title = str(chapter.get("title", "")).strip()
        theme = str(chapter.get("theme", "")).strip()
        description = str(chapter.get("description", "")).strip()
        material = chapter.get("chapter_input_material_used")
        if not title:
            errors.append(f"chapter {idx} title missing or empty")
        elif len(title) > CHAPTER_TITLE_MAX_LENGTH:
            errors.append(
                f"chapter {idx} title exceeds {CHAPTER_TITLE_MAX_LENGTH} chars "
                f"(got {len(title)})"
            )
        if not theme:
            errors.append(f"chapter {idx} theme missing or empty")
        if not description:
            errors.append(f"chapter {idx} description missing or empty")
        errors.extend(validate_chapter_input_material_used(material, idx))
        if title and theme and _normalize_chapter_label(title) == _normalize_chapter_label(theme):
            errors.append(
                f"chapter {idx} theme must differ from title "
                f"(got theme equal to title: {title!r})"
            )

    return len(errors) == 0, errors


def _log_cue_coverage_warnings(chapters, astrology_data) -> None:
    """Structured: soft coverage/reuse warnings. Freeform: skip (no chapter_focus)."""
    if is_freeform_chapter_material():
        print("  Cue coverage checks skipped (freeform mode).")
        return
    warnings = validate_book_chart_coverage(chapters, astrology_data)
    if not warnings:
        print("  Cue coverage checks: ok (no soft warnings).")
        return
    print(f"  Cue coverage soft warnings ({len(warnings)}):")
    for w in warnings:
        print(f"    - {w}")


def _is_soft_architect_validation_errors(errors: list[str]) -> bool:
    """Return true when errors are limited to chart-material selection guidance."""
    if not errors:
        return False
    soft_markers = (
        "chapter_input_material_used",
        "source_paths",
        "source_records",
        "source_paths_unresolved",
        "unresolved source_paths",
        "mixes conflicting ascendant",
        "mixes western and vedic house",
        "system-separation notes",
    )
    return all(
        any(marker in str(error).casefold() for marker in soft_markers)
        for error in errors
    )


def _finish_architect_structure(
    structure: dict,
    astrology_data: dict,
    material_mode: str,
    *,
    warning_errors: list[str] | None = None,
) -> dict:
    chapters = _chapters_from_structure(structure)
    if warning_errors:
        print(
            "  WARNING: proceeding with architect structure after one guided "
            "chart-material retry; remaining validation issue(s):"
        )
        for error in warning_errors:
            print(f"    - {error}")
    else:
        print(f"  Generated valid structure with {len(chapters)} chapters.")
    _log_cue_coverage_warnings(chapters, astrology_data)
    for i, ch in enumerate(chapters, start=1):
        title = str(ch.get("title", "") or "")
        material = prepare_chapter_input_material(
            ch.get("chapter_input_material_used"),
            astrology_data,
        )
        ch["chapter_input_material_used"] = material
        preview = chapter_material_preview(material)
        print(
            f"    Chapter {i}: {title} ({len(title)} chars) | "
            f"theme: {ch.get('theme', '')} | "
            f"{preview}"
        )
    out_path = os.path.join(ARTIFACTS_DIR, "book_structure.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)
    print(f"  Saved -> {out_path}")
    if chapters:
        mat0 = prepare_chapter_input_material(
            chapters[0].get("chapter_input_material_used"),
            astrology_data,
        )
        mat0_json = json.dumps(mat0, ensure_ascii=False)
        print(
            f"  Writer material mode={material_mode}: chapter 1 "
            f"chapter_input_material_used ~{len(mat0_json):,} chars; no chart_snapshot."
        )
    return structure


def section_descriptions_from_structure(structure: dict) -> tuple[str, str, str]:
    """
    Read preface/prologue/epilogue descriptions from Architect output.

    No hardcoded fallbacks — missing descriptions fail hard.
    """
    if not isinstance(structure, dict):
        raise ValueError("structure missing or not an object (no section-description fallbacks)")
    struct_inner = structure.get("structure") if isinstance(structure.get("structure"), dict) else {}
    missing = []
    values = []
    for key in SECTION_DESC_KEYS:
        text = str(structure.get(key) or struct_inner.get(key) or "").strip()
        if not text:
            missing.append(key)
        values.append(text)
    if missing:
        raise ValueError(
            "missing section descriptions (no fallbacks): " + ", ".join(missing)
        )
    return values[0], values[1], values[2]


def _response_is_incomplete(resp) -> bool:
    if getattr(resp, "status", None) == "incomplete":
        return True
    md = getattr(resp, "model_dump", None)
    if callable(md):
        d = md()
        if isinstance(d, dict) and (d.get("status") == "incomplete" or d.get("incomplete_details")):
            return True
    return False


def build_style_input_material(astrology_data: dict) -> dict:
    """Keep only authorized chart .Data blobs for style analysis."""
    charts = (astrology_data or {}).get("CHARTS") or {}
    material = {}
    for key in STYLE_AUTHORIZED_CHART_KEYS:
        block = charts.get(key)
        if not isinstance(block, dict):
            continue
        data = block.get("Data")
        if data is not None:
            material[key] = data
    return {"CHARTS": material}


def render_style_analysis_prompt(template: str, focus: str, language: str, astrology_data: dict) -> str:
    """Render style prompt with authorized chart slices only."""
    material = build_style_input_material(astrology_data)
    replacements = {
        "__FOCUS__": focus,
        "__LANGUAGE__": language,
        "__ASTROLOGY_DATA__": json.dumps(material, ensure_ascii=False),
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


async def generate_writing_style(async_client: AsyncOpenAI, chart: dict, focus: str, language: str):
    """Generate compact 8-domain STYLE JSON for chapter/section injection."""
    prompt = render_style_analysis_prompt(STYLE_PROMPT_TEMPLATE, focus, language, chart)
    schema = writing_style_schema()
    print("  Generating writing style profile (Responses API, json_schema, emphasize/suppress)...")
    style_resp = await async_client.responses.create(
        model=MODEL_CONTENT,
        input=[{"role": "user", "content": prompt}],
        text=responses_text_format(
            "writing_style",
            schema,
            verbosity=TEXT_VERBOSITY_STYLE,
        ),
        reasoning={"effort": REASONING_EFFORT_STYLE},
        max_output_tokens=STYLE_MAX_OUTPUT_TOKENS,
    )
    if _response_is_incomplete(style_resp):
        raise ValueError(
            f"style analysis response incomplete: {getattr(style_resp, 'incomplete_details', None)}"
        )
    raw = _response_text_from_obj(style_resp).strip()
    if not raw:
        raise ValueError("empty style analysis response")
    try:
        style_obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"style analysis returned invalid JSON: {e}") from e
    errors = validate_writing_style_object(style_obj)
    if errors:
        raise ValueError("style analysis validation failed: " + "; ".join(errors))
    normalized = normalize_writing_style(style_obj)
    style_for_writer = style_json_for_writer(normalized)
    style_readable = flatten_writing_style(normalized)
    print(
        f"  Generated writing style profile "
        f"(writer JSON {len(style_for_writer)} chars, readable {len(style_readable)} chars, "
        f"{len(WRITING_STYLE_FIELDS)} domains, max {STYLE_FIELD_MAX_LENGTH} chars/field)"
    )
    return style_for_writer, normalized, style_readable


def _section_prompt_template(name: str) -> str:
    key = str(name or "").strip().lower()
    template = SECTION_PROMPT_TEMPLATES.get(key)
    if not template:
        raise ValueError(f"unknown section type for prompt template: {name!r}")
    return template


def render_section_prompt(name, description, style, language):
    replacements = {
        "__SECTION_TYPE__": name,
        "__LANGUAGE__": language,
        "__STYLE__": style,
        "__DYNAMIC_STYLE__": style,
        "__DESCRIPTION__": description,
        "__CONTEXT__": description,
        "__SECTION_WORD_TARGET__": str(SECTION_WORD_TARGET),
        "__SECTION_WORD_MIN__": str(SECTION_WORD_MIN),
        "__SECTION_WORD_MAX__": str(SECTION_WORD_MAX),
        "__WORD_TARGET__": str(SECTION_WORD_TARGET),
    }
    rendered = _section_prompt_template(name)
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    leftover = sorted(set(re.findall(r"__[A-Z0-9_]+__", rendered)))
    if leftover:
        print(f"  WARNING: {name} prompt still has unreplaced placeholders: {leftover}")
    return rendered


def _section_batch_prompt(name, description, style, language):
    return render_section_prompt(name, description, style, language)


def build_section_batch_tasks(structure, style, language):
    preface_desc, prologue_desc, epilogue_desc = section_descriptions_from_structure(structure)
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
                        "verbosity": TEXT_VERBOSITY_SECTION,
                    },
                    "reasoning": {"effort": REASONING_EFFORT_SECTION},
                    "max_output_tokens": SECTION_MAX_OUTPUT_TOKENS,
                },
            }
        )
        manifest[custom_id] = {"section_name": name.lower(), "description": description}
        print(
            f"    Task '{custom_id}': {name} "
            f"(reasoning={REASONING_EFFORT_SECTION}, verbosity={TEXT_VERBOSITY_SECTION}, "
            f"max_output_tokens={SECTION_MAX_OUTPUT_TOKENS})"
        )
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
# GPT-5.6 Sol /v1/responses helpers (sync, async, and Batch output bodies)
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
# STEP 2: Architect Book Structure (GPT-5.6 Sol Responses API)
# ===========================================================================

def architect_book(astrology_data, focus, language):
    print("\n" + "=" * 60)
    print("STEP 2: Architecting Book Structure for the focus: ", focus, " in language: ", language)
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
        .replace("__MIN_CHAPTERS__", str(ARCHITECT_MIN_CHAPTERS))
        .replace("__MAX_CHAPTERS__", str(ARCHITECT_MAX_CHAPTERS))
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    max_attempts = ARCHITECT_MAX_RETRIES + 1
    last_errors = []
    material_mode = chapter_material_mode()
    soft_validation_retries = 0
    fallback_structure = None
    fallback_errors = []

    for attempt in range(1, max_attempts + 1):
        print(f"  Calling OpenAI Responses API (attempt {attempt}/{max_attempts})...")
        print(f"  chapter_input_material_used mode: {material_mode}")
        if last_errors:
            print(
                f"  Attaching validation retry guide "
                f"({len(last_errors)} error(s) from prior attempt; no previous JSON)."
            )
        schema = book_structure_schema(
            ARCHITECT_MIN_CHAPTERS,
            ARCHITECT_MAX_CHAPTERS,
            chapter_material_mode=material_mode,
        )
        resp = client.responses.create(
            model=MODEL_CONTENT,
            input=build_architect_model_input(
                system_prompt,
                user_prompt,
                last_errors=last_errors or None,
            ),
            text=responses_text_format(
                "book_structure",
                schema,
                verbosity=TEXT_VERBOSITY_ARCHITECT,
                strict=book_structure_schema_strict(material_mode),
            ),
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

        # Freeform: hydrate source_paths → source_records before validation.
        structure = enrich_structure_with_chart_snapshots(structure, astrology_data)
        ok, errors = validate_book_structure(structure, astrology_data)
        if ok:
            forced_errors = (
                _architect_forced_retry_errors()
                if attempt == 1
                else []
            )
            if forced_errors:
                last_errors = forced_errors
                print(
                    "  FORCED LOCAL RETRY: injecting validation guide smoke-test "
                    f"error(s): {forced_errors}"
                )
                continue

            return _finish_architect_structure(
                structure,
                astrology_data,
                material_mode,
            )

        if _is_soft_architect_validation_errors(errors):
            fallback_structure = structure
            fallback_errors = errors
            if soft_validation_retries < 1:
                soft_validation_retries += 1
                last_errors = errors
                print(
                    f"  VALIDATION FAIL attempt {attempt}: {errors} "
                    "(chart-material guidance only; retrying once)"
                )
                continue

            return _finish_architect_structure(
                structure,
                astrology_data,
                material_mode,
                warning_errors=errors,
            )

        last_errors = errors
        print(f"  VALIDATION FAIL attempt {attempt}: {errors}")

    if fallback_structure is not None:
        return _finish_architect_structure(
            fallback_structure,
            astrology_data,
            material_mode,
            warning_errors=fallback_errors,
        )

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
        title = str(ch.get("title", "")).strip()
        theme = str(ch.get("theme", "")).strip()
        description = str(ch.get("description", "")).strip()
        material = ch.get("chapter_input_material_used")
        if not title:
            raise ValueError(f"chapter {chapter_num} missing title")
        if not theme:
            raise ValueError(
                f"chapter {chapter_num} missing theme "
                "(architect must provide theme distinct from title)"
            )
        if _normalize_chapter_label(title) == _normalize_chapter_label(theme):
            raise ValueError(
                f"chapter {chapter_num} theme equals title; refusing to default theme to title"
            )
        if not description:
            raise ValueError(f"chapter {chapter_num} missing description")
        material_errors = validate_chapter_input_material_used(material, chapter_num)
        if material_errors:
            raise ValueError("; ".join(material_errors))
        if not isinstance(material, dict):
            raise ValueError(
                f"chapter {chapter_num} chapter_input_material_used missing or not an object"
            )
        material = prepare_chapter_input_material(material, astrology_data)
        custom_id = f"chapter-{chapter_num}"
        material_json = json.dumps(material, ensure_ascii=False)
        full_chart_chars = len(json.dumps(astrology_data, ensure_ascii=False))
        print(f"    Task '{custom_id}': {title}")
        print(f"      Material: {chapter_material_preview(material)}")
        print(
            f"      chapter_input_material_used: {len(material_json):,} chars "
            f"(mode={chapter_material_mode()}; full chart dump removed: ~{full_chart_chars:,} chars saved)"
        )

        prompt = (
            f'Write Chapter {chapter_num}: "{title}".\n'
            f"**Language:** {language}\n"
            f"**Style (JSON):** {style}\n"
            f"**Focus:** {focus}\n"
            f"**Theme:** {theme}\n"
            f"**Summary:** {description}\n"
            f"**Chapter input material used:** {material_json}\n"
            f"{writer_chart_material_rule()}\n"
            f"**House system rule:** Western houses, Vedic planet houses, and Bhavabala "
            f"houses are different maps. Translate each family into lived language separately. "
            f"Do not merge them into one house story (do not write as if house 4 is both "
            f"Gemini and Aries).\n"
            f"**Language rule (critical):** Translate chart factors into clear lived language "
            f"(feelings, patterns, choices, relationships, habits). "
            f"Do NOT write like a chart reading. Avoid or minimize astrology jargon "
            f"(planet names, houses, aspects, signs, Midheaven, bhava, natal/transit labels) "
            f"unless a term is briefly useful; prefer everyday wording.\n"
            f"**Word Contract:** Target {word_target} words for this chapter. Mandatory range {CHAPTER_WORD_MIN}-{CHAPTER_WORD_MAX} words.\n"
            f"**Length Rule:** Keep writing until you satisfy the mandatory range. Do not stop early.\n"
            f"**Depth Rule:** Cover (1) core pattern, (2) roots, (3) present-day behavior, (4) relationship dynamics, "
            f"(5) shadow expression, (6) reframing, (7) practical integration prompts.\n"
            f"**Formatting:** Plain paragraphs. No bold. No headers.\n"
            f"**Paragraphing (critical for layout):** Write like a printed book chapter, not chat.\n"
            f"- **Vary paragraph length deliberately.** Keep a clear mix of short, medium, and very long paragraphs. "
            f"Do **not** settle into a steady rhythm where every paragraph is the same size.\n"
            f"- **Short paragraphs (2–4 printed lines):** for emphasis, a turn in thought, or a breath — use sparingly.\n"
            f"- **Medium paragraphs (5–8 printed lines):** the majority of the chapter.\n"
            f"- **Longer medium (9–13 printed lines):** use some of these between the very long blocks so the page is not only mid + giant.\n"
            f"- Use **single newlines** only when you must break a long paragraph; prefer joining sentences in the same paragraph with spaces.\n"
            f"- Use a blank line between paragraphs.\n"
            f"Hard rule — no orphan one-liners (normal prose only):\n"
            f"- Never place a single short line / one-sentence fragment as its own paragraph between longer narrative "
            f"paragraphs (e.g. one punchy sentence alone between two multi-sentence blocks).\n"
            f"- If a sentence is for emphasis inside normal prose, keep it inside the preceding or following paragraph "
            f"(same block, spaces — not a blank-line break).\n"
            f"- A standalone narrative paragraph must be at least 2–3 full sentences (several printed lines), "
            f"unless it is part of an intentional special block below.\n"
            f"Special multi-line blocks (use when the content needs them — do not suppress):\n"
            f"- When the chapter needs contrast, use short single-line / multi-line layout for: "
            f"dialogue or conversation, a short quoted exchange, practical steps, numbered or bulleted lists, "
            f"exercise prompts, or similar script-like sequences.\n"
            f"- Use these only when the content truly calls for it (not as decoration every page). "
            f"A few well-placed blocks add variety; do not turn the whole chapter into a list.\n"
            f"- Inside those blocks, use **single newlines** between lines/items. "
            f"Separate the block from surrounding prose with blank lines as needed.\n"
            f"- Do not flatten a needed conversation, list, or step sequence into one continuous paragraph "
            f"just to satisfy the orphan rule.\n"
            f"Very long paragraphs (mandatory count, soft length):\n"
            f"- Every chapter MUST contain **2 or 3** very long paragraphs — no fewer than **2**, and **never more than 3**.\n"
            f"- Prefer about **10–14 sentences** and roughly **14–18 printed lines** "
            f"(one continuous block; spaces between sentences, not blank lines inside it).\n"
            f"- Prefer the middle of that band (~15–17 lines) when the idea can land there.\n"
            f"- Thought preservation (critical): stay with one idea until it turns. "
            f"Do **not** split mid-thought just to hit a line count. "
            f"If a paragraph runs longer because it is still one continuous argument or story beat, that is allowed.\n"
            f"- If the block passes ~20 lines because a **second** idea has started, start a new paragraph at that turn — "
            f"not mid-sentence. Avoid 25+ line walls unless the idea genuinely cannot turn earlier.\n"
            f"- Place the 2–3 very long blocks in different parts of the chapter (early / middle / late), "
            f"not all in one stretch.\n"
            f"- Count cap (critical): if you have written a **4th** very long paragraph, "
            f"split the extra into medium paragraphs. Four or more very long paragraphs is too many.\n"
            f"- Count before you finish: confirm you have **exactly 2 or 3** very long paragraphs, "
            f"then fill the rest with a mix of short + medium + longer-medium. "
            f"Do not stop at one showcase long paragraph; do not flood the chapter with 4–6 giants.\n"
            f"- Do not break a long thought into two medium paragraphs just to create white space; "
            f"keep related sentences together until the idea turns — unless you are past the 3-count cap.\n"
            f"**Output Rule:** Return only final chapter prose. "
            f"Do not begin with \"Chapter {chapter_num}:\" or the chapter title. "
            f"Start directly with body prose; first characters must be narrative text, never a heading."
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

        print(f"    Task '{custom_id}': Chapter {chapter_num} - {title} | theme: {theme}")
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

        meta = manifest.get(custom_id) or {}
        chapter_title = meta.get("chapter_title")
        before_len = len(chapter_text)
        chapter_text = _strip_echoed_chapter_header(chapter_text, chapter_title)
        if len(chapter_text) < before_len:
            print("    Stripped echoed chapter header from model output")

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
        raise RuntimeError(
            f"Image batch ended with status={image_batch.status}; "
            "refusing to continue without images."
        )

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
        raise ValueError(
            "Chapter images incomplete after retries; missing or invalid: "
            + ", ".join(sorted(missing_images))
        )
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

    style_path = os.path.join(ARTIFACTS_DIR, "writing_style.txt")
    style_json_path = os.path.join(ARTIFACTS_DIR, "writing_style.json")
    if _env_flag("SKIP_STYLE") and os.path.isfile(style_json_path):
        with open(style_json_path, "r", encoding="utf-8") as f:
            normalized = json.load(f)
        errors = validate_writing_style_object(normalized)
        if errors:
            raise ValueError("SKIP_STYLE reused writing_style.json invalid: " + "; ".join(errors))
        normalized = normalize_writing_style(normalized)
        style = style_json_for_writer(normalized)
        print(f"  SKIP_STYLE: reusing {style_json_path} ({len(style)} JSON chars for writer)")
    elif _env_flag("SKIP_STYLE") and os.path.isfile(style_path):
        # Legacy: flattened text only — still injectable, but prefer JSON next run.
        with open(style_path, "r", encoding="utf-8") as f:
            style = f.read().strip()
        if not style:
            raise ValueError("SKIP_STYLE set but writing_style.txt is empty")
        print(f"  SKIP_STYLE: reusing flattened {style_path} ({len(style)} chars)")
    else:
        style, normalized, style_readable = await generate_writing_style(
            async_client, astrology_data, focus, language
        )
        with open(style_path, "w", encoding="utf-8") as f:
            f.write(style_readable)
        print(f"  Saved -> {style_path}")
        with open(style_json_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        print(f"  Saved -> {style_json_path}")
    print(f"  Style (JSON) for writer: {style[:160]}...")

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
    print(f"  chapter_input_material_used mode: {chapter_material_mode()}")
    print(f"  Birth:    {json.dumps(birth_data)}")
    start = time.time()

    astrology_path = os.path.join(ARTIFACTS_DIR, "astrology_data.json")
    structure_path = os.path.join(ARTIFACTS_DIR, "book_structure.json")
    if _env_flag("SKIP_FETCH"):
        if not os.path.isfile(astrology_path):
            raise FileNotFoundError(f"SKIP_FETCH set but missing {astrology_path}")
        with open(astrology_path, "r", encoding="utf-8") as f:
            astrology_data = json.load(f)
        validate_astrology_artifact(astrology_data)
        print(f"  SKIP_FETCH: reusing {astrology_path}")
    else:
        astrology_data = fetch_astrology(birth_data, order_id)

    if _env_flag("SKIP_ARCHITECT"):
        if not os.path.isfile(structure_path):
            raise FileNotFoundError(f"SKIP_ARCHITECT set but missing {structure_path}")
        with open(structure_path, "r", encoding="utf-8") as f:
            structure = json.load(f)
        # Hydrate freeform source_paths before validation.
        structure = enrich_structure_with_chart_snapshots(structure, astrology_data)
        ok, errors = validate_book_structure(structure, astrology_data)
        if not ok:
            raise ValueError("SKIP_ARCHITECT reused structure failed validation: " + "; ".join(errors))
        chapters = _chapters_from_structure(structure)
        print(f"  SKIP_ARCHITECT: reusing {structure_path} ({len(chapters)} chapters)")
        _log_cue_coverage_warnings(chapters, astrology_data)
        for i, ch in enumerate(chapters, start=1):
            title = str(ch.get("title", "") or "")
            material = prepare_chapter_input_material(
                ch.get("chapter_input_material_used"),
                astrology_data,
            )
            ch["chapter_input_material_used"] = material
            print(
                f"    Chapter {i}: {title} ({len(title)} chars) | "
                f"theme: {ch.get('theme', '')} | "
                f"{chapter_material_preview(material)}"
            )
        with open(structure_path, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=2, ensure_ascii=False)
        print(
            f"  Normalized chapter_input_material_used "
            f"(mode={chapter_material_mode()}, no chart_snapshot) -> {structure_path}"
        )
    else:
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
