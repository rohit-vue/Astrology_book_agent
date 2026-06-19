import boto3
import json
import os
import asyncio
import time
import base64
import re
from botocore.config import Config
from openai import AsyncOpenAI, OpenAI
from urllib.parse import urlparse

API_KEYS_SECRET_ARN = os.environ.get("API_KEYS_SECRET_ARN")
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET")


def _env_str(key: str, default: str) -> str:
    val = os.environ.get(key)
    if val is None or not str(val).strip():
        return default
    return str(val).strip()


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None or not str(val).strip():
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None or not str(val).strip():
        return default
    try:
        return float(val)
    except ValueError:
        return default


MODEL_CONTENT = _env_str("MODEL_CONTENT", "gpt-5.5")
MODEL_IMAGE = _env_str("MODEL_IMAGE", "gpt-image-2")
BATCH_ENDPOINT_RESPONSES = _env_str("BATCH_ENDPOINT_RESPONSES", "/v1/responses")

REASONING_EFFORT_CHAPTER = _env_str("REASONING_EFFORT_CHAPTER", "high")
TEXT_VERBOSITY_CHAPTER = _env_str("TEXT_VERBOSITY_CHAPTER", "high")
CHAPTER_MAX_OUTPUT_TOKENS = _env_int("CHAPTER_MAX_OUTPUT_TOKENS", 48000)

REASONING_EFFORT_ARCHITECT = _env_str("REASONING_EFFORT_ARCHITECT", "high")
TEXT_VERBOSITY_ARCHITECT = _env_str("TEXT_VERBOSITY_ARCHITECT", "high")

REASONING_EFFORT_STYLE = _env_str("REASONING_EFFORT_STYLE", "medium")
TEXT_VERBOSITY_STYLE = _env_str("TEXT_VERBOSITY_STYLE", "low")
STYLE_MAX_OUTPUT_TOKENS = _env_int("STYLE_MAX_OUTPUT_TOKENS", 600)

SECTION_MAX_OUTPUT_TOKENS = _env_int("SECTION_MAX_OUTPUT_TOKENS", 4000)
SECTION_WORD_TARGET = _env_int("SECTION_WORD_TARGET", 550)
SECTION_WORD_MIN = _env_int("SECTION_WORD_MIN", 500)
SECTION_WORD_MAX = _env_int("SECTION_WORD_MAX", 600)
SECTION_GENERATION_MAX_RETRIES = _env_int("SECTION_GENERATION_MAX_RETRIES", 2)
IMAGE_SUMMARY_MAX_OUTPUT_TOKENS = _env_int("IMAGE_SUMMARY_MAX_OUTPUT_TOKENS", 200)

BATCH_POLL_INTERVAL = _env_int("BATCH_POLL_INTERVAL", 15)
BATCH_MAX_AGE_SECONDS = _env_int("BATCH_MAX_AGE_SECONDS", 84600)
BOOK_WORD_TARGET = _env_int("BOOK_WORD_TARGET", 50000)
CHAPTER_WORD_TARGET = _env_int("CHAPTER_WORD_TARGET", 7750)
CHAPTER_WORD_MIN = _env_int("CHAPTER_WORD_MIN", 7500)
CHAPTER_WORD_MAX = _env_int("CHAPTER_WORD_MAX", 8000)
CJK_MIN_LENGTH_RATIO = _env_float("CJK_MIN_LENGTH_RATIO", 0.62)
MAX_BATCH_RETRIES = _env_int("MAX_BATCH_RETRIES", 1)
IMAGE_MIN_BYTES = _env_int("IMAGE_MIN_BYTES", 50000)

# Scripts where each character is roughly one word unit (no whitespace between words).
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

AWS_CONNECT_TIMEOUT_SECONDS = _env_int("AWS_CONNECT_TIMEOUT_SECONDS", 3)
AWS_READ_TIMEOUT_SECONDS = _env_int("AWS_READ_TIMEOUT_SECONDS", 30)
AWS_MAX_ATTEMPTS = _env_int("AWS_MAX_ATTEMPTS", 2)
OPENAI_TIMEOUT_SECONDS = _env_int("OPENAI_TIMEOUT_SECONDS", 90)
OPENAI_MAX_RETRIES = _env_int("OPENAI_MAX_RETRIES", 1)
ALLOW_LEGACY_PIPELINE = os.environ.get("ALLOW_LEGACY_PIPELINE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

_aws_config = Config(
    connect_timeout=AWS_CONNECT_TIMEOUT_SECONDS,
    read_timeout=AWS_READ_TIMEOUT_SECONDS,
    retries={"max_attempts": AWS_MAX_ATTEMPTS, "mode": "standard"},
    max_pool_connections=20,
)
s3_client = boto3.client("s3", config=_aws_config)
secrets_manager_client = boto3.client("secretsmanager", config=_aws_config)
ssm_client = boto3.client("ssm", config=_aws_config)
async_openai_client = AsyncOpenAI(
    api_key="dummy",
    timeout=OPENAI_TIMEOUT_SECONDS,
    max_retries=OPENAI_MAX_RETRIES,
)
sync_openai_client = OpenAI(
    api_key="dummy",
    timeout=OPENAI_TIMEOUT_SECONDS,
    max_retries=OPENAI_MAX_RETRIES,
)

_openai_configured = False
_chapter_prompt_template_cache: str | None = None
_image_prompt_template_cache: str | None = None

IMAGE_PROMPT_FALLBACK = (
    "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. "
    "Style: ethereal, cosmic, rich colors. CRITICAL: NO text, letters, or figures."
)

CHAPTER_PROMPT_SSM_NAME = "/AstrologyBookFactory/prompts/writer/chapter"
IMAGE_PROMPT_SSM_NAME = "/AstrologyBookFactory/prompts/writer/image"
CHAPTER_PROMPT_FALLBACK = """Write Chapter __CHAPTER_NUM__: "__CHAPTER_TITLE__".
**Language:** __LANGUAGE__
**Style:** __STYLE__
**Focus:** __FOCUS__
**Summary:** __SUMMARY__
**Book Contract:** The complete book targets ~__BOOK_WORD_TARGET__ words total across all chapters.
**Word Contract:** Target __WORD_TARGET__ words for this chapter. Mandatory range __CHAPTER_WORD_MIN__-__CHAPTER_WORD_MAX__ words(EXTREMELY IMPORTANT).
**Length Rule:** Keep writing until you satisfy the mandatory range. Do not stop early.
**Depth Rule:** Cover (1) core pattern, (2) roots, (3) present-day behavior, (4) relationship dynamics, (5) shadow expression, (6) reframing, (7) practical integration prompts.
**Formatting:** Plain paragraphs. No bold. No headers.
**Paragraphing (critical for layout):** Write like a printed book chapter, not chat.
- **Vary paragraph length deliberately.** Mix shorter paragraphs (often **3-5 sentences**, about **2–3 printed lines**) with medium and longer ones. Do **not** settle into a steady rhythm where every paragraph is the same size.
- **Short paragraphs are allowed** for emphasis, a turn in thought, or a breath between ideas—use them **sometimes**, not after every sentence.
- Longer paragraphs are fine when the idea needs room; neighbor paragraphs may be much shorter so the page does not look like uniform blocks.
- Use **single newlines** only when you must break a long paragraph; prefer joining sentences in the same paragraph with spaces.
- Use **double newlines (blank line)** ONLY between **major sections**. **At most 8–10 double-newlines in the whole chapter.**
**Output Rule:** Return only final chapter prose.
**Data:** __ASTROLOGY_DATA__"""


def get_chapter_prompt_template() -> str:
    global _chapter_prompt_template_cache
    if _chapter_prompt_template_cache:
        return _chapter_prompt_template_cache
    try:
        _chapter_prompt_template_cache = ssm_client.get_parameter(
            Name=CHAPTER_PROMPT_SSM_NAME, WithDecryption=True
        )["Parameter"]["Value"]
    except Exception as e:
        print(f"SSM chapter prompt not found; using fallback: {e}")
        _chapter_prompt_template_cache = CHAPTER_PROMPT_FALLBACK
    return _chapter_prompt_template_cache


def get_image_prompt_template() -> str:
    global _image_prompt_template_cache
    if _image_prompt_template_cache:
        return _image_prompt_template_cache
    try:
        _image_prompt_template_cache = ssm_client.get_parameter(
            Name=IMAGE_PROMPT_SSM_NAME, WithDecryption=True
        )["Parameter"]["Value"]
    except Exception as e:
        print(f"SSM image prompt not found; using fallback: {e}")
        _image_prompt_template_cache = IMAGE_PROMPT_FALLBACK
    return _image_prompt_template_cache


def render_chapter_prompt(
    template: str,
    chapter_num: int,
    title: str,
    language: str,
    style: str,
    focus: str,
    description: str,
    word_target: int,
    astrology_data: dict,
    chapter_theme: str | None = None,
) -> str:
    """Substitute SSM chapter template tokens (legacy + current naming)."""
    theme = (chapter_theme or title).strip() or title
    astrology_json = json.dumps(astrology_data, ensure_ascii=False)
    replacements = {
        "__CHAPTER_NUM__": str(chapter_num),
        "__CHAPTER_TITLE__": title,
        "__CHAPTER_THEME__": theme,
        "__LANGUAGE__": language,
        "__STYLE__": style,
        "__DYNAMIC_STYLE__": style,
        "__FOCUS__": focus,
        "__SUMMARY__": description,
        "__CHAPTER_SUMMARY__": description,
        "__BOOK_WORD_TARGET__": str(BOOK_WORD_TARGET),
        "__WORD_TARGET__": str(word_target),
        "__CHAPTER_WORD_MIN__": str(CHAPTER_WORD_MIN),
        "__CHAPTER_WORD_MAX__": str(CHAPTER_WORD_MAX),
        "__ASTROLOGY_DATA__": astrology_json,
        "__NATAL_CHART__": astrology_json,
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    leftover = sorted(set(re.findall(r"__[A-Z0-9_]+__", rendered)))
    if leftover:
        print(
            f"WARNING: chapter prompt still has unreplaced placeholders "
            f"for chapter {chapter_num}: {leftover}"
        )
    return rendered


def _extract_text_from_responses_body_dict(body: dict) -> str:
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
    tx = getattr(resp, "output_text", None)
    if isinstance(tx, str) and tx.strip():
        return tx.strip()
    md = getattr(resp, "model_dump", None)
    if callable(md):
        d = md()
        if isinstance(d, dict):
            return _extract_text_from_responses_body_dict(d)
    return ""


def unwrap_payload(event):
    """Step Functions lambda:invoke wraps handler return in Payload; nested tasks may repeat."""
    payload = event
    for _ in range(5):
        if isinstance(payload, dict) and "Payload" in payload and isinstance(payload["Payload"], dict):
            payload = payload["Payload"]
        else:
            break
    return payload


def state_prefix(order_id: str, line_item_id: str) -> str:
    return f"write-chapters-state/{order_id}/{line_item_id}"


def save_state(prefix: str, name: str, obj: dict) -> None:
    key = f"{prefix}/{name}"
    s3_put_json(ARTIFACTS_BUCKET, key, obj)


def load_state(prefix: str, name: str) -> dict | None:
    key = f"{prefix}/{name}"
    try:
        body = s3_client.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)["Body"].read().decode("utf-8")
        return json.loads(body)
    except Exception as e:
        print(f"load_state miss or error for {key}: {e}")
        return None


def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip("/")


def s3_get_json(s3_path):
    bucket, key = parse_s3_path(s3_path)
    return json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))


def s3_put_json(bucket, key, obj):
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(obj, ensure_ascii=False),
        ContentType="application/json",
    )


def _section_text_is_valid(text) -> bool:
    ok, _ = _validate_section_text(text)
    return ok


def _cjk_char_count(text: str) -> int:
    return len(_CJK_CHAR_RE.findall(str(text or "")))


def _latin_word_count(text: str) -> int:
    remainder = _CJK_CHAR_RE.sub(" ", str(text or ""))
    return len([w for w in remainder.split() if w.strip() and any(c.isalnum() for c in w)])


def _content_unit_count(text: str) -> int:
    """Length for validation: CJK chars + whitespace-separated words elsewhere."""
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


def filter_valid_text_results(
    merged: dict,
    chapter_manifest: dict,
    section_manifest: dict,
) -> tuple[dict, set[str]]:
    """Drop invalid chapter/section texts and return ids that should be retried."""
    invalid_ids: set[str] = set()
    for custom_id in list(merged.keys()):
        text = merged.get(custom_id)
        if custom_id in chapter_manifest:
            ok, reason = _validate_chapter_text(text)
            label = custom_id
        elif custom_id in section_manifest:
            ok, reason = _validate_section_text(text)
            label = custom_id
        else:
            continue
        if not ok:
            print(f"VALIDATION FAIL {label}: {reason}")
            invalid_ids.add(custom_id)
            merged.pop(custom_id, None)
    return merged, invalid_ids


def _section_descriptions_from_structure(structure: dict) -> tuple[str, str, str]:
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
    return preface_desc, prologue_desc, epilogue_desc


def default_style(focus: str, language: str) -> str:
    return (
        f"Language: {language}. "
        f"Focus: {focus}. "
        "Tone: Warm, psychologically precise, compassionate, direct second-person. "
        "Voice rules: concrete language; grounded interpretation; practical guidance; "
        "emotionally honest pacing; no fluff. "
        "Avoid: generic filler; moralizing; vague advice; melodrama."
    )


def _section_batch_prompt(name: str, description: str, style: str, language: str) -> str:
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


def build_section_batch_tasks(structure: dict, style: str, language: str):
    preface_desc, prologue_desc, epilogue_desc = _section_descriptions_from_structure(structure)
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
    return tasks, manifest, epilogue_desc


def _load_text_manifests(prefix: str) -> tuple[dict, dict]:
    chapter_manifest = load_state(prefix, "text_chapter_manifest.json") or {}
    section_manifest = load_state(prefix, "text_section_manifest.json") or {}
    if not chapter_manifest:
        chapter_manifest = load_state(prefix, "text_manifest.json") or {}
    return chapter_manifest, section_manifest


def _sections_from_batch_results(merged: dict, section_manifest: dict, sections_meta: dict) -> dict:
    section_results = {}
    for custom_id, meta in section_manifest.items():
        section_name = meta["section_name"]
        text = merged.get(custom_id, "")
        if text:
            section_results[f"{section_name}_text"] = text
    return {
        **sections_meta,
        **section_results,
    }


async def _generate_section_once(name, description, style, language):
    prompt = f"""
    Generate narrative prose content for a personal astrology book section.
    Section Type: {name}
    Language: {language}
    Style: {style}
    Context: {description}
    **Word Contract:** Target {SECTION_WORD_TARGET} words. Mandatory range {SECTION_WORD_MIN}-{SECTION_WORD_MAX} words.
    **Length Rule:** Write until you satisfy the mandatory range, then stop. Do not exceed {SECTION_WORD_MAX} words.
    **Layout Rule:** This section must fit on two printed pages. End with a complete sentence.
    **Paragraphing:** Plain paragraphs only. Use at most 3-4 paragraph breaks (double newlines) in the whole section.
    STRICT RULES:
    - Output ONLY body text.
    - No headings or titles.
    - No markdown.
    - No labels.
    - Start directly with prose.
    - Second person POV ("You").
    """
    resp = await async_openai_client.responses.create(
        model=MODEL_CONTENT,
        input=[{"role": "user", "content": prompt}],
        text={"format": {"type": "text"}, "verbosity": TEXT_VERBOSITY_ARCHITECT},
        reasoning={"effort": REASONING_EFFORT_ARCHITECT},
        max_output_tokens=SECTION_MAX_OUTPUT_TOKENS,
    )
    if getattr(resp, "status", None) == "incomplete":
        print(f"WARNING: {name} response incomplete: {getattr(resp, 'incomplete_details', None)}")
    return _response_text_from_obj(resp).strip()


async def generate_section(name, description, style, language):
    if not description:
        return ""
    max_attempts = SECTION_GENERATION_MAX_RETRIES + 1
    print(f"Generating {name}...")
    for attempt in range(1, max_attempts + 1):
        try:
            text = await _generate_section_once(name, description, style, language)
            if _section_text_is_valid(text):
                word_count = len(text.split())
                print(f"✅ {name} SUCCESS (attempt {attempt}/{max_attempts}): {word_count} words, {len(text)} chars")
                return text
            print(f"WARNING: {name} attempt {attempt}/{max_attempts} returned empty text; retrying...")
        except Exception as e:
            print(f"❌ Error generating {name} (attempt {attempt}/{max_attempts}): {e}")
    print(f"ERROR: {name} failed after {max_attempts} attempts")
    return ""


async def ensure_section(name, description, style, language, existing=""):
    if _section_text_is_valid(existing):
        return str(existing).strip()
    print(f"{name} missing or empty in saved state; regenerating...")
    return await generate_section(name, description, style, language)


def build_style_chart_snapshot(astrology_data):
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


def build_chapter_batch_tasks(
    chapters_list,
    astrology_data,
    focus,
    style,
    language,
    word_target,
    chapter_prompt_template: str,
):
    tasks = []
    manifest = {}
    for idx, ch in enumerate(chapters_list):
        chapter_num = idx + 1
        title = ch["title"]
        description = ch["description"]
        chapter_theme = ch.get("theme") or title
        custom_id = f"chapter-{chapter_num}"

        prompt = render_chapter_prompt(
            chapter_prompt_template,
            chapter_num,
            title,
            language,
            style,
            focus,
            description,
            word_target,
            astrology_data,
            chapter_theme=chapter_theme,
        )

        tasks.append(
            {
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
        )
        manifest[custom_id] = {"chapter_index": chapter_num, "chapter_title": title}
    return tasks, manifest


def submit_batch(client, tasks, endpoint, artifact_prefix):
    lines = [json.dumps(task, ensure_ascii=False) for task in tasks]
    jsonl_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    tmp_path = f"/tmp/{artifact_prefix}_input.jsonl"
    with open(tmp_path, "wb") as f:
        f.write(jsonl_bytes)

    with open(tmp_path, "rb") as f:
        file_obj = client.files.create(file=f, purpose="batch")

    batch_job = client.batches.create(
        input_file_id=file_obj.id,
        endpoint=endpoint,
        completion_window="24h",
        metadata={"description": artifact_prefix},
    )
    print(f"Batch created: {batch_job.id} ({artifact_prefix})")
    return batch_job.id


def _batch_attr(batch, name, default=None):
    if isinstance(batch, dict):
        return batch.get(name, default)
    return getattr(batch, name, default)


def _batch_age_seconds(batch) -> int | None:
    created_at = _batch_attr(batch, "created_at")
    if created_at is None:
        return None
    try:
        return max(0, int(time.time() - int(created_at)))
    except (TypeError, ValueError):
        return None


def _batch_is_too_old(batch) -> bool:
    age = _batch_age_seconds(batch)
    return age is not None and age >= BATCH_MAX_AGE_SECONDS


def _request_counts_dict(batch) -> dict:
    counts = _batch_attr(batch, "request_counts")
    if isinstance(counts, dict):
        return {
            "total": counts.get("total", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
        }
    return {
        "total": getattr(counts, "total", 0) if counts is not None else 0,
        "completed": getattr(counts, "completed", 0) if counts is not None else 0,
        "failed": getattr(counts, "failed", 0) if counts is not None else 0,
    }


def _batch_status_requires_replacement(batch) -> bool:
    status = _batch_attr(batch, "status")
    if status in {"failed", "cancelled"}:
        return True
    if status == "expired" and not _batch_attr(batch, "output_file_id"):
        return True
    if status not in {"completed", "expired"} and _batch_is_too_old(batch):
        return True
    return False


def _submit_replacement_batch(
    prefix: str,
    pipeline_state_name: str,
    pipe: dict,
    tasks: list[dict],
    failed_custom_ids: set[str],
    endpoint: str,
    artifact_prefix: str,
    id_field: str,
    count_retry: bool = True,
) -> dict | None:
    if not failed_custom_ids:
        return None

    retry_count = int(pipe.get("retry_count") or 0)
    if count_retry and retry_count >= MAX_BATCH_RETRIES:
        return None

    retry_tasks = [t for t in tasks if t.get("custom_id") in failed_custom_ids]
    if not retry_tasks:
        print(f"No retry tasks found for {pipeline_state_name}: {sorted(failed_custom_ids)}")
        return None

    next_retry_count = retry_count + 1 if count_retry else retry_count
    new_batch_id = submit_batch(
        sync_openai_client,
        retry_tasks,
        endpoint,
        f"{artifact_prefix}_{next_retry_count}",
    )
    save_state(
        prefix,
        pipeline_state_name,
        {
            **pipe,
            "failed_custom_ids": sorted(failed_custom_ids),
            "current_batch_id": new_batch_id,
            "retry_count": next_retry_count,
            "replacement_reason": artifact_prefix,
        },
    )
    return {id_field: new_batch_id, "retry_count": next_retry_count}


def poll_batch_until_done(client, batch_id):
    terminal_states = {"completed", "failed", "expired", "cancelled"}
    start = time.time()
    n = 0
    while True:
        batch = client.batches.retrieve(batch_id)
        n += 1
        counts = batch.request_counts
        print(
            f"[{n:>3}] {time.time()-start:>6.0f}s "
            f"status={batch.status:<12} total={counts.total} done={counts.completed} failed={counts.failed}"
        )
        if batch.status in terminal_states:
            return batch
        time.sleep(BATCH_POLL_INTERVAL)


def collect_chapter_batch_results(client, batch):
    if not batch.output_file_id:
        print(f"Batch {batch.id} has no output file (status={batch.status}); retrying missing text ids.")
        return {}, set()
    result_content = client.files.content(batch.output_file_id).content
    results_by_id = {}
    failed_ids = set()

    for line in result_content.decode("utf-8").strip().split("\n"):
        if not line.strip():
            continue
        entry = json.loads(line)
        custom_id = entry["custom_id"]
        response = entry.get("response")
        error = entry.get("error")
        if error:
            failed_ids.add(custom_id)
            continue
        if not response or response.get("status_code") != 200:
            failed_ids.add(custom_id)
            continue
        body = response.get("body") or {}
        if _batch_body_is_incomplete(body):
            print(f"VALIDATION FAIL {custom_id}: batch response status incomplete")
            failed_ids.add(custom_id)
            continue
        chapter_text = _extract_text_from_responses_body_dict(body)
        if not chapter_text and isinstance(body, dict) and body.get("choices"):
            try:
                chapter_text = (body["choices"][0]["message"]["content"] or "").strip()
            except (KeyError, IndexError, TypeError):
                chapter_text = ""
        if not chapter_text:
            failed_ids.add(custom_id)
            continue
        results_by_id[custom_id] = chapter_text

    return results_by_id, failed_ids


def build_chapters_data_from_results(results_by_id, manifest, order_id, line_item_id):
    chapters_data = []
    for custom_id, info in manifest.items():
        idx = info["chapter_index"]
        title = info["chapter_title"]
        text = results_by_id.get(custom_id)
        if text is None:
            continue

        key = f"chapters-json/{order_id}/{line_item_id}/chapter_{idx}.json"
        s3_put_json(ARTIFACTS_BUCKET, key, {"chapter_title": title, "chapter_text": text})
        chapters_data.append(
            {
                "chapter_index": idx,
                "chapter_title": title,
                "chapter_text_s3_path": f"s3://{ARTIFACTS_BUCKET}/{key}",
                "image_url": None,
            }
        )
    chapters_data.sort(key=lambda x: x["chapter_index"])
    return chapters_data


def build_image_batch_tasks_from_structure(chapters_list, image_prompt_template):
    """Image prompts from chapter title + description only (parallel track; no chapter text)."""
    tasks = []
    manifest = {}
    for idx, ch in enumerate(chapters_list):
        chapter_num = idx + 1
        title = ch.get("title") or f"Chapter {chapter_num}"
        description = (ch.get("description") or "").strip()
        summary = description[:700] if description else ""
        custom_id = f"image-{chapter_num}"
        img_prompt = (
            image_prompt_template.replace("__CHAPTER_TITLE__", title).replace("__SUMMARY__", summary)
        )
        tasks.append(
            {
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
        )
        manifest[custom_id] = {"chapter_index": chapter_num, "chapter_title": title}
    return tasks, manifest


async def build_image_batch_tasks(chapters_data, image_prompt_template):
    tasks = []
    manifest = {}
    for ch in chapters_data:
        idx = ch["chapter_index"]
        title = ch["chapter_title"]
        chapter_json = s3_get_json(ch["chapter_text_s3_path"])
        text = chapter_json.get("chapter_text", "")
        custom_id = f"image-{idx}"

        summary = text[:700]
        try:
            sum_resp = await async_openai_client.responses.create(
                model=MODEL_CONTENT,
                input=[{"role": "user", "content": f"Summarize text for image: {text[:1200]}"}],
                text={"format": {"type": "text"}, "verbosity": "low"},
                reasoning={"effort": "low"},
                max_output_tokens=IMAGE_SUMMARY_MAX_OUTPUT_TOKENS,
            )
            summary = _response_text_from_obj(sum_resp).strip()
        except Exception as e:
            print(f"Image summary fallback for chapter {idx}: {e}")

        img_prompt = image_prompt_template.replace("__CHAPTER_TITLE__", title).replace("__SUMMARY__", summary)
        tasks.append(
            {
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
        )
        manifest[custom_id] = {"chapter_index": idx, "chapter_title": title}
    return tasks, manifest


def collect_image_batch_results(client, batch):
    if not batch.output_file_id:
        print(f"Image batch {batch.id} has no output file (status={batch.status}); retrying missing images.")
        return {}, set()
    result_content = client.files.content(batch.output_file_id).content
    image_bytes_by_id = {}
    failed_ids = set()

    for line in result_content.decode("utf-8").strip().split("\n"):
        if not line.strip():
            continue
        entry = json.loads(line)
        custom_id = entry["custom_id"]
        response = entry.get("response")
        error = entry.get("error")
        if error:
            failed_ids.add(custom_id)
            continue
        if not response or response.get("status_code") != 200:
            failed_ids.add(custom_id)
            continue
        data_items = response.get("body", {}).get("data", [])
        if not data_items or not data_items[0].get("b64_json"):
            failed_ids.add(custom_id)
            continue
        image_bytes_by_id[custom_id] = base64.b64decode(data_items[0]["b64_json"])
        ok, reason = _validate_image_bytes(image_bytes_by_id[custom_id])
        if not ok:
            print(f"VALIDATION FAIL {custom_id}: {reason}")
            failed_ids.add(custom_id)
            del image_bytes_by_id[custom_id]

    return image_bytes_by_id, failed_ids


def collect_and_store_image_results(client, batch, manifest, order_id, line_item_id, existing_keys=None):
    s3_keys_by_id = dict(existing_keys or {})
    if not batch.output_file_id:
        print(f"Image batch {batch.id} has no output file (status={batch.status}); retrying missing images.")
        return s3_keys_by_id, set(manifest.keys()) - set(s3_keys_by_id.keys())
    result_content = client.files.content(batch.output_file_id).content
    failed_ids = set()

    for line in result_content.decode("utf-8").strip().split("\n"):
        if not line.strip():
            continue
        entry = json.loads(line)
        custom_id = entry["custom_id"]
        if custom_id in s3_keys_by_id:
            continue
        response = entry.get("response")
        error = entry.get("error")
        if error:
            failed_ids.add(custom_id)
            continue
        if not response or response.get("status_code") != 200:
            failed_ids.add(custom_id)
            continue
        data_items = response.get("body", {}).get("data", [])
        b64 = data_items[0].get("b64_json") if data_items else None
        if not b64:
            failed_ids.add(custom_id)
            continue
        raw = base64.b64decode(b64)
        ok, reason = _validate_image_bytes(raw)
        if not ok:
            print(f"VALIDATION FAIL {custom_id}: {reason}")
            failed_ids.add(custom_id)
            continue
        idx = manifest[custom_id]["chapter_index"]
        key = f"chapter-images/{order_id}/{line_item_id}/chapter_{idx}.png"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=key,
            Body=raw,
            ContentType="image/png",
        )
        s3_keys_by_id[custom_id] = key

    return s3_keys_by_id, failed_ids


def _presigned_urls_from_s3_keys(s3_keys_by_id, manifest):
    urls_by_index = {}
    for custom_id, key in s3_keys_by_id.items():
        info = manifest.get(custom_id)
        if not info:
            continue
        idx = info["chapter_index"]
        image_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": ARTIFACTS_BUCKET, "Key": key},
            ExpiresIn=86400,
        )
        urls_by_index[str(idx)] = image_url
    return urls_by_index


def apply_images_to_chapters(chapters_data, image_manifest, image_bytes_by_id, order_id, line_item_id):
    by_idx = {ch["chapter_index"]: ch for ch in chapters_data}
    for custom_id, info in image_manifest.items():
        idx = info["chapter_index"]
        raw = image_bytes_by_id.get(custom_id)
        if raw is None:
            continue
        key = f"chapter-images/{order_id}/{line_item_id}/chapter_{idx}.png"
        s3_client.put_object(Bucket=ARTIFACTS_BUCKET, Key=key, Body=raw, ContentType="image/png")
        image_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": ARTIFACTS_BUCKET, "Key": key},
            ExpiresIn=86400,
        )
        if idx in by_idx:
            by_idx[idx]["image_url"] = image_url
    return chapters_data


async def generate_chapter_images_batch(chapters_data, image_prompt_template, order_id, line_item_id):
    image_tasks, image_manifest = await build_image_batch_tasks(chapters_data, image_prompt_template)
    image_batch_id = submit_batch(sync_openai_client, image_tasks, "/v1/images/generations", "chapter_image")
    image_batch = poll_batch_until_done(sync_openai_client, image_batch_id)
    if image_batch.status not in {"completed", "expired"}:
        print(f"Image batch ended with status={image_batch.status}; skipping images.")
        return chapters_data

    merged_images_by_id, failed_image_ids = collect_image_batch_results(sync_openai_client, image_batch)
    missing_ids = set(image_manifest.keys()) - set(merged_images_by_id.keys())
    failed_image_ids |= missing_ids

    for retry_num in range(1, MAX_BATCH_RETRIES + 1):
        if not failed_image_ids:
            break
        retry_ids = sorted(failed_image_ids)
        retry_tasks = [task for task in image_tasks if task["custom_id"] in failed_image_ids]
        retry_batch_id = submit_batch(
            sync_openai_client,
            retry_tasks,
            "/v1/images/generations",
            f"chapter_image_retry_{retry_num}",
        )
        retry_batch = poll_batch_until_done(sync_openai_client, retry_batch_id)
        if retry_batch.status not in {"completed", "expired"}:
            break
        retry_images_by_id, retry_failed_ids = collect_image_batch_results(sync_openai_client, retry_batch)
        merged_images_by_id.update(retry_images_by_id)
        failed_image_ids = set(retry_ids) - set(merged_images_by_id.keys())
        failed_image_ids |= (set(retry_failed_ids) & set(retry_ids))

    return apply_images_to_chapters(
        chapters_data, image_manifest, merged_images_by_id, order_id, line_item_id
    )


def configure_openai():
    global _openai_configured
    if _openai_configured:
        return
    secret = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    api_key = json.loads(secret["SecretString"]).get("OpenAIKey")
    if not api_key:
        raise ValueError("OpenAIKey missing from API key secret.")
    async_openai_client.api_key = api_key
    sync_openai_client.api_key = api_key
    _openai_configured = True


async def prepare_style_and_sections(chart, structure, focus, language):
    style_chart = build_style_chart_snapshot(chart)
    try:
        style_resp = await async_openai_client.responses.create(
            model=MODEL_CONTENT,
            input=[{
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
                ),
            }],
            text={"format": {"type": "json_object"}, "verbosity": TEXT_VERBOSITY_STYLE},
            reasoning={"effort": REASONING_EFFORT_STYLE},
            max_output_tokens=STYLE_MAX_OUTPUT_TOKENS,
        )
        style_json = json.loads(_response_text_from_obj(style_resp))
        tone = (style_json.get("tone") or "").strip()
        rules = [r.strip() for r in style_json.get("voice_rules", []) if isinstance(r, str) and r.strip()]
        avoids = [a.strip() for a in style_json.get("avoid", []) if isinstance(a, str) and a.strip()]
        style = (
            f"Tone: {tone}. "
            f"Voice rules: {'; '.join(rules[:5])}. "
            f"Avoid: {'; '.join(avoids[:4])}."
        )
    except Exception as e:
        print(f"Style generation failed, using fallback style: {e}")
        style = (
            "Tone: Warm, psychologically precise, compassionate, direct second-person. "
            "Voice rules: concrete language; grounded interpretation; practical guidance; emotionally honest pacing; no fluff. "
            "Avoid: generic filler; moralizing; vague advice; melodrama."
        )

    preface_desc, prologue_desc, epilogue_desc = _section_descriptions_from_structure(structure)

    preface_text = await generate_section("Preface", preface_desc, style, language)
    prologue_text = await generate_section("Prologue", prologue_desc, style, language)
    for section_name, section_text in (("Preface", preface_text), ("Prologue", prologue_text)):
        if not _section_text_is_valid(section_text):
            raise ValueError(f"{section_name} generation returned empty text after retries.")

    return {
        "style": style,
        "preface_text": preface_text,
        "prologue_text": prologue_text,
        "epilogue_desc": epilogue_desc,
    }


# --- v2 operation handlers ---


def _echo_payload(payload: dict) -> dict:
    """Forward fields for the next Step Functions Task; drop operation key only."""
    out = dict(payload)
    out.pop("operation", None)
    return out


async def op_submit_text_batch(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    focus = payload.get("focus", "Personality")
    language = payload.get("language", "English")
    prefix = state_prefix(order_id, line_item_id)

    save_state(prefix, "wc_base_payload.json", dict(payload))

    chart = s3_get_json(payload["astrology_json_s3_path"])
    structure = s3_get_json(payload["book_structure_s3_path"])
    style = default_style(focus, language)

    struct_inner = structure.get("structure", {})
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    chapter_prompt_template = get_chapter_prompt_template()
    chapter_tasks, chapter_manifest = build_chapter_batch_tasks(
        chapters_list,
        chart,
        focus,
        style,
        language,
        CHAPTER_WORD_TARGET,
        chapter_prompt_template,
    )
    section_tasks, section_manifest, epilogue_desc = build_section_batch_tasks(
        structure, style, language
    )
    tasks = chapter_tasks + section_tasks

    save_state(prefix, "text_chapter_manifest.json", chapter_manifest)
    save_state(prefix, "text_section_manifest.json", section_manifest)
    save_state(prefix, "wc_sections_meta.json", {"style": style, "epilogue_desc": epilogue_desc})
    save_state(prefix, "text_tasks.json", {"tasks": tasks})
    save_state(prefix, "wc_structure_snapshot.json", {"chapters_list": chapters_list})

    batch_id = submit_batch(sync_openai_client, tasks, BATCH_ENDPOINT_RESPONSES, "chapter_text")
    save_state(
        prefix,
        "text_pipeline.json",
        {
            "merged_results_by_id": {},
            "failed_custom_ids": [],
            "current_batch_id": batch_id,
            "retry_count": 0,
            "expect_retry_submit": False,
        },
    )

    out = {**_echo_payload(payload), "wc_state_prefix": prefix, "wc_text_batch_id": batch_id}
    return out


def op_check_text_batch(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    pipe = load_state(prefix, "text_pipeline.json") or {}
    batch_id = pipe.get("current_batch_id") or payload.get("wc_text_batch_id")
    if not batch_id:
        raise ValueError("No text batch id in state.")

    batch = sync_openai_client.batches.retrieve(batch_id)
    counts = _request_counts_dict(batch)
    age = _batch_age_seconds(batch)
    terminal = batch.status in {"completed", "failed", "expired", "cancelled"}
    print(
        f"check_text_batch status={batch.status} terminal={terminal} "
        f"age={age} total={counts['total']} done={counts['completed']} failed={counts['failed']}"
    )

    if _batch_status_requires_replacement(batch):
        chapter_manifest, section_manifest = _load_text_manifests(prefix)
        expected_ids = set(chapter_manifest.keys()) | set(section_manifest.keys())
        merged = pipe.get("merged_results_by_id") or {}
        remaining_ids = expected_ids - set(merged.keys())
        tasks_obj = load_state(prefix, "text_tasks.json") or {}
        replacement = _submit_replacement_batch(
            prefix,
            "text_pipeline.json",
            pipe,
            tasks_obj.get("tasks") or [],
            remaining_ids,
            BATCH_ENDPOINT_RESPONSES,
            "chapter_text_replacement",
            "wc_text_batch_id",
        )
        if replacement:
            return {
                **_echo_payload(payload),
                "wc_state_prefix": prefix,
                "wc_text_batch_id": replacement["wc_text_batch_id"],
                "wc_text_batch_status": "resubmitted",
                "wc_text_batch_terminal": False,
                "wc_text_batch_resubmitted": True,
                "wc_text_request_counts": counts,
            }

    out = {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_text_batch_id": batch_id,
        "wc_text_batch_status": batch.status,
        "wc_text_batch_terminal": terminal,
        "wc_text_request_counts": counts,
    }
    return out


async def op_collect_text_results(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    pipe = load_state(prefix, "text_pipeline.json") or {}
    chapter_manifest, section_manifest = _load_text_manifests(prefix)
    sections_meta = load_state(prefix, "wc_sections_meta.json") or {}
    batch_id = pipe.get("current_batch_id")

    batch = sync_openai_client.batches.retrieve(batch_id)
    if batch.status not in {"completed", "expired", "failed", "cancelled"}:
        raise RuntimeError(f"Chapter text batch ended with status={batch.status}")

    merged = pipe.get("merged_results_by_id") or {}
    new_results, failed_ids = collect_chapter_batch_results(sync_openai_client, batch)
    merged.update(new_results)
    merged, invalid_ids = filter_valid_text_results(merged, chapter_manifest, section_manifest)

    retry_count = int(pipe.get("retry_count") or 0)
    expected_ids = set(chapter_manifest.keys()) | set(section_manifest.keys())
    missing_ids = expected_ids - set(merged.keys())
    failed_custom = (failed_ids | missing_ids | invalid_ids) & expected_ids

    save_state(prefix, "text_pipeline.json", {
        **pipe,
        "merged_results_by_id": merged,
        "failed_custom_ids": sorted(failed_custom),
        "retry_count": retry_count,
    })

    if failed_custom and retry_count < MAX_BATCH_RETRIES:
        tasks_obj = load_state(prefix, "text_tasks.json") or {}
        tasks = tasks_obj.get("tasks") or []
        retry_tasks = [t for t in tasks if t["custom_id"] in failed_custom]
        retry_count += 1
        new_batch_id = submit_batch(
            sync_openai_client,
            retry_tasks,
            BATCH_ENDPOINT_RESPONSES,
            f"chapter_text_retry_{retry_count}",
        )
        save_state(
            prefix,
            "text_pipeline.json",
            {
                **pipe,
                "merged_results_by_id": merged,
                "failed_custom_ids": sorted(failed_custom),
                "current_batch_id": new_batch_id,
                "retry_count": retry_count,
                "expect_retry_submit": False,
            },
        )
        return {
            **_echo_payload(payload),
            "wc_state_prefix": prefix,
            "wc_text_batch_id": new_batch_id,
            "wc_text_track_need_wait": True,
            "wc_text_collect_complete": False,
        }

    chapter_results = {
        custom_id: text for custom_id, text in merged.items() if custom_id in chapter_manifest
    }
    chapters_data = build_chapters_data_from_results(
        chapter_results, chapter_manifest, order_id, line_item_id
    )
    missing_chapters = set(chapter_manifest.keys()) - set(chapter_results.keys())
    if missing_chapters:
        raise ValueError(
            "Chapter text validation failed after retries; missing or invalid: "
            + ", ".join(sorted(missing_chapters))
        )
    if not chapters_data:
        raise ValueError("No chapter texts were produced by batch jobs.")

    sections = _sections_from_batch_results(merged, section_manifest, sections_meta)
    missing_sections = [
        name
        for name, key in (
            ("Preface", "preface_text"),
            ("Prologue", "prologue_text"),
            ("Epilogue", "epilogue_text"),
        )
        if not _section_text_is_valid(sections.get(key))
    ]
    if missing_sections:
        raise ValueError(
            "Section text validation failed after retries; missing or invalid: "
            + ", ".join(missing_sections)
        )

    save_state(prefix, "wc_sections.json", sections)
    save_state(prefix, "text_chapters_data.json", {"chapters_data": chapters_data})

    return {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_text_collect_complete": True,
        "wc_text_track_need_wait": False,
    }


async def op_submit_image_batch(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    image_prompt_template = get_image_prompt_template()

    structure = s3_get_json(payload["book_structure_s3_path"])
    struct_inner = structure.get("structure", {})
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])

    tasks, manifest = build_image_batch_tasks_from_structure(chapters_list, image_prompt_template)
    save_state(prefix, "image_manifest.json", manifest)
    save_state(prefix, "image_tasks.json", {"tasks": tasks})

    batch_id = submit_batch(sync_openai_client, tasks, "/v1/images/generations", "chapter_image")
    save_state(
        prefix,
        "image_pipeline.json",
        {
            "image_s3_keys_by_id": {},
            "failed_custom_ids": [],
            "current_batch_id": batch_id,
            "retry_count": 0,
        },
    )

    return {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_image_batch_id": batch_id,
    }


def op_check_image_batch(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    pipe = load_state(prefix, "image_pipeline.json") or {}
    batch_id = pipe.get("current_batch_id") or payload.get("wc_image_batch_id")
    if not batch_id:
        raise ValueError("No image batch id in state.")

    batch = sync_openai_client.batches.retrieve(batch_id)
    counts = _request_counts_dict(batch)
    age = _batch_age_seconds(batch)
    terminal = batch.status in {"completed", "failed", "expired", "cancelled"}
    print(
        f"check_image_batch status={batch.status} terminal={terminal} "
        f"age={age} total={counts['total']} done={counts['completed']} failed={counts['failed']}"
    )

    if _batch_status_requires_replacement(batch):
        manifest = load_state(prefix, "image_manifest.json") or {}
        existing_keys = set((pipe.get("image_s3_keys_by_id") or {}).keys())
        remaining_ids = set(manifest.keys()) - existing_keys
        tasks_obj = load_state(prefix, "image_tasks.json") or {}
        replacement = _submit_replacement_batch(
            prefix,
            "image_pipeline.json",
            pipe,
            tasks_obj.get("tasks") or [],
            remaining_ids,
            "/v1/images/generations",
            "chapter_image_replacement",
            "wc_image_batch_id",
        )
        if replacement:
            return {
                **_echo_payload(payload),
                "wc_state_prefix": prefix,
                "wc_image_batch_id": replacement["wc_image_batch_id"],
                "wc_image_batch_status": "resubmitted",
                "wc_image_batch_terminal": False,
                "wc_image_batch_resubmitted": True,
                "wc_image_request_counts": counts,
            }

    return {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_image_batch_id": batch_id,
        "wc_image_batch_status": batch.status,
        "wc_image_batch_terminal": terminal,
        "wc_image_request_counts": counts,
    }


async def op_collect_image_results(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    pipe = load_state(prefix, "image_pipeline.json") or {}
    manifest = load_state(prefix, "image_manifest.json") or {}
    batch_id = pipe.get("current_batch_id")

    batch = sync_openai_client.batches.retrieve(batch_id)
    if batch.status not in {"completed", "expired", "failed", "cancelled"}:
        print(f"Image batch ended with status={batch.status}; skipping images.")
        save_state(prefix, "image_urls_by_index.json", {})
        return {
            **_echo_payload(payload),
            "wc_state_prefix": prefix,
            "wc_image_collect_complete": True,
            "wc_image_track_need_wait": False,
        }

    existing_keys = dict(pipe.get("image_s3_keys_by_id") or {})
    merged_keys, failed_ids = collect_and_store_image_results(
        sync_openai_client,
        batch,
        manifest,
        order_id,
        line_item_id,
        existing_keys=existing_keys,
    )

    retry_count = int(pipe.get("retry_count") or 0)
    missing_ids = set(manifest.keys()) - set(merged_keys.keys())
    failed_custom = (failed_ids | missing_ids) & set(manifest.keys())

    if failed_custom and retry_count < MAX_BATCH_RETRIES:
        tasks_obj = load_state(prefix, "image_tasks.json") or {}
        tasks = tasks_obj.get("tasks") or []
        retry_tasks = [t for t in tasks if t["custom_id"] in failed_custom]
        retry_count += 1
        new_batch_id = submit_batch(
            sync_openai_client,
            retry_tasks,
            "/v1/images/generations",
            f"chapter_image_retry_{retry_count}",
        )
        save_state(
            prefix,
            "image_pipeline.json",
            {
                **pipe,
                "image_s3_keys_by_id": merged_keys,
                "failed_custom_ids": sorted(failed_custom),
                "current_batch_id": new_batch_id,
                "retry_count": retry_count,
            },
        )
        return {
            **_echo_payload(payload),
            "wc_state_prefix": prefix,
            "wc_image_batch_id": new_batch_id,
            "wc_image_track_need_wait": True,
            "wc_image_collect_complete": False,
        }

    urls_by_index = _presigned_urls_from_s3_keys(merged_keys, manifest)
    save_state(prefix, "image_urls_by_index.json", urls_by_index)

    return {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_image_collect_complete": True,
        "wc_image_track_need_wait": False,
    }


async def op_finalize(payload: dict) -> dict:
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    base = load_state(prefix, "wc_base_payload.json")
    if not base:
        base = dict(payload)

    sections = load_state(prefix, "wc_sections.json") or {}
    chapters_obj = load_state(prefix, "text_chapters_data.json") or {}
    chapters_data = list(chapters_obj.get("chapters_data") or [])

    urls_obj = load_state(prefix, "image_urls_by_index.json") or {}
    urls_by_index = urls_obj if isinstance(urls_obj, dict) else {}

    by_idx = {ch["chapter_index"]: ch for ch in chapters_data}
    for idx_str, url in urls_by_index.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx in by_idx:
            by_idx[idx]["image_url"] = url

    structure = s3_get_json(base["book_structure_s3_path"])

    preface_text = str(sections.get("preface_text", "")).strip()
    prologue_text = str(sections.get("prologue_text", "")).strip()
    epilogue_text = str(sections.get("epilogue_text", "")).strip()
    missing = [
        name
        for name, text in (
            ("Preface", preface_text),
            ("Prologue", prologue_text),
            ("Epilogue", epilogue_text),
        )
        if not _section_text_is_valid(text)
    ]
    if missing:
        raise ValueError(
            "Missing generated section text from batch results: " + ", ".join(missing)
        )

    final_output = dict(base)
    final_output["full_book_structure"] = structure
    final_output["generated_sections"] = {
        "preface": preface_text,
        "prologue": prologue_text,
        # Foreword is sourced by generate_pdf from assets/foreword.txt.
        "foreword": "",
        "epilogue": epilogue_text,
    }
    final_output["full_book_structure"]["preface_text"] = preface_text
    final_output["full_book_structure"]["prologue_text"] = prologue_text
    final_output["full_book_structure"]["epilogue_text"] = epilogue_text
    if "metadata" not in final_output["full_book_structure"]:
        final_output["full_book_structure"]["metadata"] = structure.get(
            "metadata", structure.get("book_metadata", {})
        )
    final_output["chapters_data"] = chapters_data

    return final_output


async def async_lambda_handler(event, context):
    print(f"WriteChapters received event: {json.dumps(event, default=str, indent=2)}")
    payload = unwrap_payload(event)

    operation = payload.get("operation") if isinstance(payload, dict) else None
    if not operation:
        if ALLOW_LEGACY_PIPELINE:
            return await legacy_full_pipeline(payload)
        raise ValueError("Missing operation; refusing to run legacy pipeline implicitly.")

    if not all([
        payload.get("order_id"),
        payload.get("line_item_id"),
        payload.get("astrology_json_s3_path"),
        payload.get("book_structure_s3_path"),
    ]):
        raise ValueError("Missing required fields.")

    if operation == "submit_text_batch":
        return await op_submit_text_batch(payload)
    if operation == "check_text_batch":
        return op_check_text_batch(payload)
    if operation == "collect_text_results":
        return await op_collect_text_results(payload)
    if operation == "submit_image_batch":
        return await op_submit_image_batch(payload)
    if operation == "check_image_batch":
        return op_check_image_batch(payload)
    if operation == "collect_image_results":
        return await op_collect_image_results(payload)
    if operation == "finalize":
        return await op_finalize(payload)

    raise ValueError(f"Unknown operation: {operation}")


async def legacy_full_pipeline(payload):
    """v1 Step Functions: single Task, synchronous batch polling (may hit Lambda timeout)."""
    order_id = payload.get("order_id")
    line_item_id = payload.get("line_item_id")
    focus = payload.get("focus", "Personality")
    language = payload.get("language", "English")
    if not all([order_id, line_item_id, payload.get("astrology_json_s3_path"), payload.get("book_structure_s3_path")]):
        raise ValueError("Missing required fields.")

    configure_openai()
    chart = s3_get_json(payload["astrology_json_s3_path"])
    structure = s3_get_json(payload["book_structure_s3_path"])

    image_prompt_template = get_image_prompt_template()

    sections = await prepare_style_and_sections(chart, structure, focus, language)
    style = sections["style"]

    struct_inner = structure.get("structure", {})
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    chapter_prompt_template = get_chapter_prompt_template()
    tasks, manifest = build_chapter_batch_tasks(
        chapters_list,
        chart,
        focus,
        style,
        language,
        CHAPTER_WORD_TARGET,
        chapter_prompt_template,
    )

    batch_id = submit_batch(sync_openai_client, tasks, BATCH_ENDPOINT_RESPONSES, "chapter_text")
    batch = poll_batch_until_done(sync_openai_client, batch_id)
    if batch.status not in {"completed", "expired"}:
        raise RuntimeError(f"Chapter text batch ended with status={batch.status}")

    merged_results_by_id, failed_ids = collect_chapter_batch_results(sync_openai_client, batch)
    merged_results_by_id, invalid_ids = filter_valid_text_results(
        merged_results_by_id, manifest, {}
    )
    failed_ids |= invalid_ids
    missing_ids = set(manifest.keys()) - set(merged_results_by_id.keys())
    failed_ids |= missing_ids

    for retry_num in range(1, MAX_BATCH_RETRIES + 1):
        if not failed_ids:
            break
        retry_ids = sorted(failed_ids)
        retry_tasks = [task for task in tasks if task["custom_id"] in failed_ids]
        retry_batch_id = submit_batch(
            sync_openai_client,
            retry_tasks,
            BATCH_ENDPOINT_RESPONSES,
            f"chapter_text_retry_{retry_num}",
        )
        retry_batch = poll_batch_until_done(sync_openai_client, retry_batch_id)
        if retry_batch.status not in {"completed", "expired"}:
            break
        retry_results_by_id, retry_failed_ids = collect_chapter_batch_results(sync_openai_client, retry_batch)
        merged_results_by_id.update(retry_results_by_id)
        merged_results_by_id, retry_invalid_ids = filter_valid_text_results(
            merged_results_by_id, manifest, {}
        )
        failed_ids = (set(retry_ids) - set(merged_results_by_id.keys())) | retry_invalid_ids
        failed_ids |= (set(retry_failed_ids) & set(retry_ids))

    missing_chapters = set(manifest.keys()) - set(merged_results_by_id.keys())
    if missing_chapters:
        raise ValueError(
            "Chapter text validation failed after retries; missing or invalid: "
            + ", ".join(sorted(missing_chapters))
        )

    chapters_data = build_chapters_data_from_results(
        merged_results_by_id, manifest, order_id, line_item_id
    )
    if not chapters_data:
        raise ValueError("No chapter texts were produced by batch jobs.")

    chapters_data = await generate_chapter_images_batch(
        chapters_data, image_prompt_template, order_id, line_item_id
    )

    preface_desc, prologue_desc, epilogue_desc = _section_descriptions_from_structure(structure)
    epilogue_desc = sections.get("epilogue_desc") or epilogue_desc

    preface_text = await ensure_section(
        "Preface", preface_desc, style, language, sections.get("preface_text", "")
    )
    prologue_text = await ensure_section(
        "Prologue", prologue_desc, style, language, sections.get("prologue_text", "")
    )
    epilogue_text = await ensure_section("Epilogue", epilogue_desc, style, language, "")

    missing = [
        name for name, text in (
            ("Preface", preface_text),
            ("Prologue", prologue_text),
            ("Epilogue", epilogue_text),
        )
        if not _section_text_is_valid(text)
    ]
    if missing:
        raise ValueError(f"Failed to generate section text after retries: {', '.join(missing)}")

    final_output = payload
    final_output["full_book_structure"] = structure
    final_output["generated_sections"] = {
        "preface": preface_text,
        "prologue": prologue_text,
        # Foreword is sourced by generate_pdf from assets/foreword.txt.
        "foreword": "",
        "epilogue": epilogue_text,
    }
    final_output["full_book_structure"]["preface_text"] = preface_text
    final_output["full_book_structure"]["prologue_text"] = prologue_text
    final_output["full_book_structure"]["epilogue_text"] = epilogue_text
    if "metadata" not in final_output["full_book_structure"]:
        final_output["full_book_structure"]["metadata"] = structure.get(
            "metadata", structure.get("book_metadata", {})
        )
    final_output["chapters_data"] = chapters_data

    return final_output


def lambda_handler(event, context):
    return asyncio.run(async_lambda_handler(event, context))
