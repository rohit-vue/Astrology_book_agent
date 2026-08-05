import boto3
import json
import os
from botocore.config import Config
from openai import OpenAI
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


MODEL_ID = _env_str("MODEL_ARCHITECT", "gpt-5.5")
REASONING_EFFORT_ARCHITECT = _env_str("REASONING_EFFORT_ARCHITECT", "high")
TEXT_VERBOSITY_ARCHITECT = _env_str("TEXT_VERBOSITY_ARCHITECT", "high")
ARCHITECT_MAX_OUTPUT_TOKENS = _env_int("ARCHITECT_MAX_OUTPUT_TOKENS", 24000)
ARCHITECT_EXPECTED_CHAPTERS = _env_int("ARCHITECT_EXPECTED_CHAPTERS", 7)
ARCHITECT_MAX_RETRIES = _env_int("ARCHITECT_MAX_RETRIES", 2)
OPENAI_TIMEOUT_SECONDS = _env_int("OPENAI_TIMEOUT_SECONDS", 120)
OPENAI_MAX_RETRIES = _env_int("OPENAI_MAX_RETRIES", 1)
AWS_CONNECT_TIMEOUT_SECONDS = _env_int("AWS_CONNECT_TIMEOUT_SECONDS", 3)
AWS_READ_TIMEOUT_SECONDS = _env_int("AWS_READ_TIMEOUT_SECONDS", 30)
AWS_MAX_ATTEMPTS = _env_int("AWS_MAX_ATTEMPTS", 2)

_aws_config = Config(
    connect_timeout=AWS_CONNECT_TIMEOUT_SECONDS,
    read_timeout=AWS_READ_TIMEOUT_SECONDS,
    retries={"max_attempts": AWS_MAX_ATTEMPTS, "mode": "standard"},
    max_pool_connections=20,
)
s3_client = boto3.client("s3", config=_aws_config)
secrets_manager_client = boto3.client("secretsmanager", config=_aws_config)
ssm_client = boto3.client("ssm", config=_aws_config)
openai_client = OpenAI(
    api_key="dummy",
    timeout=OPENAI_TIMEOUT_SECONDS,
    max_retries=OPENAI_MAX_RETRIES,
)

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

ARCHITECT_SYSTEM_PROMPT_FALLBACK = """You are an ASI (Artificial Superintelligence) acting as a master psychological interpreter and book architect.
Your persona is wise, insightful, and empathetic.
**CRITICAL INSTRUCTION:** You MUST output your response in **__LANGUAGE__**."""

ARCHITECT_USER_PROMPT_FALLBACK = """**CRITICAL LANGUAGE REQUIREMENT:**
The Book Title, Chapter Titles, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

**TASK:**
Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

**STRUCTURE RULES:**
You must generate a book outline with EXACTLY 7 CHAPTERS.
Each chapter must be thematically distinct and explore a specific facet of "__FOCUS__".

Use this Q&A context to personalize the outline where relevant:
__QANDA__

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


def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip("/")


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


def _response_is_incomplete(resp) -> bool:
    if getattr(resp, "status", None) == "incomplete":
        return True
    md = getattr(resp, "model_dump", None)
    if callable(md):
        d = md()
        if isinstance(d, dict) and (d.get("status") == "incomplete" or d.get("incomplete_details")):
            return True
    return False


def _chapters_from_structure(data: dict) -> list:
    struct = data.get("structure") if isinstance(data.get("structure"), dict) else {}
    chapters = struct.get("chapters")
    if isinstance(chapters, list):
        return chapters
    top = data.get("chapters")
    return top if isinstance(top, list) else []


def validate_book_structure(data: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
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
        errors.append(
            f"expected {ARCHITECT_EXPECTED_CHAPTERS} chapters, got {len(chapters)}"
        )
    for idx, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            errors.append(f"chapter {idx} is not an object")
            continue
        if not str(chapter.get("title", "")).strip():
            errors.append(f"chapter {idx} title missing or empty")
        if not str(chapter.get("description", "")).strip():
            errors.append(f"chapter {idx} description missing or empty")

    return len(errors) == 0, errors


def get_prompts_from_ssm(astrology_data: dict, focus: str, language: str, qanda: str) -> tuple[str, str]:
    try:
        sys_param = ssm_client.get_parameter(
            Name="/AstrologyBookFactory/prompts/architect/system", WithDecryption=True
        )
        user_param = ssm_client.get_parameter(
            Name="/AstrologyBookFactory/prompts/architect/user", WithDecryption=True
        )
        system_template = sys_param["Parameter"]["Value"]
        user_template = user_param["Parameter"]["Value"]
    except Exception as e:
        print(f"Error fetching prompts from SSM, using fallback prompts: {e}")
        system_template = ARCHITECT_SYSTEM_PROMPT_FALLBACK
        user_template = ARCHITECT_USER_PROMPT_FALLBACK

    system_prompt = system_template.replace("__LANGUAGE__", language)
    user_prompt = user_template.replace("__FOCUS__", focus)
    user_prompt = user_prompt.replace("__LANGUAGE__", language)
    user_prompt = user_prompt.replace("__ASTROLOGY_DATA__", json.dumps(astrology_data, indent=2))
    safe_qanda = qanda[:15000] if qanda else "No Q&A provided."
    user_prompt = user_prompt.replace("__QANDA__", safe_qanda)
    return system_prompt, user_prompt


def architect_book_structure(system_prompt: str, user_prompt: str) -> dict:
    max_attempts = ARCHITECT_MAX_RETRIES + 1
    last_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        print(f"Calling OpenAI to architect book structure (attempt {attempt}/{max_attempts})...")
        response = openai_client.responses.create(
            model=MODEL_ID,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text={"format": {"type": "json_object"}, "verbosity": TEXT_VERBOSITY_ARCHITECT},
            reasoning={"effort": REASONING_EFFORT_ARCHITECT},
            max_output_tokens=ARCHITECT_MAX_OUTPUT_TOKENS,
        )

        if _response_is_incomplete(response):
            detail = getattr(response, "incomplete_details", None)
            last_errors = [f"response incomplete: {detail}"]
            print(f"VALIDATION FAIL attempt {attempt}: {last_errors[0]}")
            continue

        raw_text = _response_text_from_obj(response)
        if not raw_text:
            last_errors = ["empty model response"]
            print(f"VALIDATION FAIL attempt {attempt}: empty model response")
            continue

        try:
            full_structure = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            last_errors = [f"invalid JSON: {exc}"]
            print(f"VALIDATION FAIL attempt {attempt}: {last_errors[0]}")
            continue

        ok, errors = validate_book_structure(full_structure)
        if ok:
            chapter_count = len(_chapters_from_structure(full_structure))
            print(f"Generated valid structure with {chapter_count} chapters.")
            return full_structure

        last_errors = errors
        print(f"VALIDATION FAIL attempt {attempt}: {errors}")

    raise ValueError(
        "Book structure invalid after "
        f"{max_attempts} attempt(s): " + "; ".join(last_errors)
    )


def lambda_handler(event, context):
    print(f"ArchitectBook received event: {json.dumps(event)}")
    payload = event.get("Payload", event)

    order_id = payload.get("order_id")
    line_item_id = payload.get("line_item_id")
    astrology_s3_path = payload.get("astrology_json_s3_path")
    focus = payload.get("focus", "Personality")
    language = payload.get("language", "English")
    qanda_content = payload.get("qanda_content", {}).get("qanda_content", "")

    if not all([order_id, line_item_id, astrology_s3_path]):
        raise ValueError("Missing required fields.")

    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        openai_client.api_key = json.loads(secret_payload["SecretString"]).get("OpenAIKey")

        bucket, key = parse_s3_path(astrology_s3_path)
        s3_object = s3_client.get_object(Bucket=bucket, Key=key)
        astrology_data = json.loads(s3_object["Body"].read().decode("utf-8"))

        system_prompt, user_prompt = get_prompts_from_ssm(astrology_data, focus, language, qanda_content)
        full_structure = architect_book_structure(system_prompt, user_prompt)

        output_key = f"book-structures/{order_id}/{line_item_id}.json"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=output_key,
            Body=json.dumps(full_structure, indent=2),
            ContentType="application/json",
        )

        payload["book_structure_s3_path"] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
        return payload

    except Exception as e:
        print(f"ERROR: {e}")
        raise e
