import boto3
import json
import os
import re
from botocore.config import Config
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from urllib.parse import urlparse

from chart_material import (
    chapter_material_preview,
    enrich_structure_with_chart_snapshots,
    validate_book_chart_coverage,
    validate_chapter_input_material_used,
)
from structured_schemas import (
    CHAPTER_TITLE_MAX_LENGTH,
    book_structure_schema,
    book_structure_schema_strict,
    responses_text_format,
)


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


MODEL_ID = _env_str("MODEL_ARCHITECT", "gpt-5.6-sol")
BATCH_ENDPOINT_RESPONSES = _env_str("BATCH_ENDPOINT_RESPONSES", "/v1/responses")
REASONING_EFFORT_ARCHITECT = _env_str("REASONING_EFFORT_ARCHITECT", "high")
TEXT_VERBOSITY_ARCHITECT = _env_str("TEXT_VERBOSITY_ARCHITECT", "high")
ARCHITECT_MAX_OUTPUT_TOKENS = _env_int("ARCHITECT_MAX_OUTPUT_TOKENS", 24000)
ARCHITECT_MIN_CHAPTERS = _env_int("ARCHITECT_MIN_CHAPTERS", 1)
ARCHITECT_MAX_CHAPTERS = _env_int("ARCHITECT_MAX_CHAPTERS", 14)
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


class RetryableOpenAIError(RuntimeError):
    """Raised for OpenAI failures that Step Functions should retry."""


def _is_retryable_openai_error(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        return status in {408, 409, 429} or (isinstance(status, int) and status >= 500)
    return False


def _openai_error_summary(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    parts = [exc.__class__.__name__]
    if status is not None:
        parts.append(f"status={status}")
    if request_id:
        parts.append(f"request_id={request_id}")
    return " ".join(parts)


def _call_openai(operation: str, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        if _is_retryable_openai_error(exc):
            summary = _openai_error_summary(exc)
            print(f"Retryable OpenAI error during {operation}: {summary}")
            raise RetryableOpenAIError(
                f"Retryable OpenAI error during {operation}: {summary}"
            ) from exc
        raise


def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip("/")


def unwrap_payload(event):
    payload = event
    for _ in range(5):
        if isinstance(payload, dict) and "Payload" in payload and isinstance(payload["Payload"], dict):
            payload = payload["Payload"]
        else:
            break
    return payload


def s3_put_json(bucket: str, key: str, obj: dict) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(obj, indent=2),
        ContentType="application/json",
    )


def s3_get_json(s3_path: str) -> dict:
    bucket, key = parse_s3_path(s3_path)
    s3_object = s3_client.get_object(Bucket=bucket, Key=key)
    return json.loads(s3_object["Body"].read().decode("utf-8"))


def state_prefix(order_id: str, line_item_id: str) -> str:
    return f"architect-state/{order_id}/{line_item_id}"


def save_state(prefix: str, name: str, obj: dict) -> None:
    s3_put_json(ARTIFACTS_BUCKET, f"{prefix}/{name}", obj)


def load_state(prefix: str, name: str) -> dict | None:
    key = f"{prefix}/{name}"
    try:
        body = s3_client.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)["Body"].read().decode("utf-8")
        return json.loads(body)
    except Exception as exc:
        print(f"load_state miss or error for {key}: {exc}")
        return None


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


def _normalize_chapter_label(value: str) -> str:
    """Normalize title/theme for equality checks (case/punct/whitespace insensitive)."""
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def validate_book_structure(
    data: dict,
    astrology_data: dict | None = None,
) -> tuple[bool, list[str]]:
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
    if not ARCHITECT_MIN_CHAPTERS <= len(chapters) <= ARCHITECT_MAX_CHAPTERS:
        errors.append(
            f"expected {ARCHITECT_MIN_CHAPTERS}-{ARCHITECT_MAX_CHAPTERS} "
            f"chapters, got {len(chapters)}"
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
        errors.extend(
            validate_chapter_input_material_used(material, idx, astrology_data)
        )
        if title and theme and _normalize_chapter_label(title) == _normalize_chapter_label(theme):
            errors.append(
                f"chapter {idx} theme must differ from title "
                f"(got theme equal to title: {title!r})"
            )

    return len(errors) == 0, errors


def get_prompts_from_ssm(astrology_data: dict, focus: str, language: str, qanda: str) -> tuple[str, str]:
    sys_param = ssm_client.get_parameter(
        Name="/AstrologyBookFactory/prompts/architect/system", WithDecryption=True
    )
    user_param = ssm_client.get_parameter(
        Name="/AstrologyBookFactory/prompts/architect/user", WithDecryption=True
    )
    system_template = (sys_param["Parameter"]["Value"] or "").strip()
    user_template = (user_param["Parameter"]["Value"] or "").strip()
    if not system_template:
        raise ValueError("SSM architect system prompt is empty")
    if not user_template:
        raise ValueError("SSM architect user prompt is empty")

    system_prompt = (
        system_template
        .replace("__LANGUAGE__", language)
        .replace("__FOCUS__", focus)
    )
    user_prompt = user_template.replace("__FOCUS__", focus)
    user_prompt = user_prompt.replace("__LANGUAGE__", language)
    user_prompt = user_prompt.replace("__ASTROLOGY_DATA__", json.dumps(astrology_data, indent=2))
    user_prompt = user_prompt.replace("__MIN_CHAPTERS__", str(ARCHITECT_MIN_CHAPTERS))
    user_prompt = user_prompt.replace("__MAX_CHAPTERS__", str(ARCHITECT_MAX_CHAPTERS))
    safe_qanda = qanda[:15000] if qanda else "No Q&A provided."
    user_prompt = user_prompt.replace("__QANDA__", safe_qanda)
    return system_prompt, user_prompt


def _log_coverage_status(chapters: list, astrology_data: dict | None) -> None:
    """Coverage retired for freeform; keep a clear log line for CloudWatch."""
    warnings = validate_book_chart_coverage(chapters, astrology_data)
    if warnings:
        print(f"WARNING: chart coverage ({len(warnings)}):")
        for item in warnings:
            print(f"  - {item}")
    else:
        print("Chart coverage checks disabled (freeform source_paths).")


def architect_response_body(system_prompt: str, user_prompt: str) -> dict:
    return {
        "model": MODEL_ID,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "text": responses_text_format(
            "book_structure",
            book_structure_schema(ARCHITECT_MIN_CHAPTERS, ARCHITECT_MAX_CHAPTERS),
            verbosity=TEXT_VERBOSITY_ARCHITECT,
            strict=book_structure_schema_strict(),
        ),
        "reasoning": {"effort": REASONING_EFFORT_ARCHITECT},
        "max_output_tokens": ARCHITECT_MAX_OUTPUT_TOKENS,
    }


def validate_architect_raw_text(
    raw_text: str,
    astrology_data: dict | None,
) -> tuple[dict | None, list[str]]:
    if not raw_text:
        return None, ["empty model response"]
    try:
        full_structure = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc}"]

    full_structure = enrich_structure_with_chart_snapshots(
        full_structure, astrology_data
    )
    ok, errors = validate_book_structure(full_structure, astrology_data)
    if not ok:
        return None, errors
    return full_structure, []


def log_valid_structure(full_structure: dict, astrology_data: dict | None) -> None:
    chapters = _chapters_from_structure(full_structure)
    print(f"Generated valid structure with {len(chapters)} chapters.")
    for i, ch in enumerate(chapters, start=1):
        title = str(ch.get("title", "") or "")
        material = ch.get("chapter_input_material_used") or {}
        preview = chapter_material_preview(material)
        print(
            f"  Chapter {i}: {title} ({len(title)} chars) | "
            f"theme: {ch.get('theme', '')} | "
            f"{preview}"
        )
    _log_coverage_status(chapters, astrology_data)


def save_book_structure(payload: dict, full_structure: dict) -> dict:
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    output_key = f"book-structures/{order_id}/{line_item_id}.json"
    s3_put_json(ARTIFACTS_BUCKET, output_key, full_structure)
    out = dict(payload)
    out["book_structure_s3_path"] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
    return out


def architect_book_structure(
    system_prompt: str,
    user_prompt: str,
    astrology_data: dict | None = None,
) -> dict:
    max_attempts = ARCHITECT_MAX_RETRIES + 1
    last_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        print(f"Calling OpenAI to architect book structure (attempt {attempt}/{max_attempts})...")
        print("chapter_input_material_used mode: freeform")
        response = _call_openai(
            "architect responses.create",
            openai_client.responses.create,
            **architect_response_body(system_prompt, user_prompt),
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

        # Freeform: hydrate source_paths → source_records before validation.
        full_structure = enrich_structure_with_chart_snapshots(
            full_structure, astrology_data
        )
        ok, errors = validate_book_structure(full_structure, astrology_data)
        if ok:
            chapters = _chapters_from_structure(full_structure)
            print(f"Generated valid structure with {len(chapters)} chapters.")
            for i, ch in enumerate(chapters, start=1):
                title = str(ch.get("title", "") or "")
                material = ch.get("chapter_input_material_used") or {}
                preview = chapter_material_preview(material)
                print(
                    f"  Chapter {i}: {title} ({len(title)} chars) | "
                    f"theme: {ch.get('theme', '')} | "
                    f"{preview}"
                )
            _log_coverage_status(chapters, astrology_data)
            return full_structure

        last_errors = errors
        print(f"VALIDATION FAIL attempt {attempt}: {errors}")

    raise ValueError(
        "Book structure invalid after "
        f"{max_attempts} attempt(s): " + "; ".join(last_errors)
    )


def configure_openai() -> None:
    secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    api_key = json.loads(secret_payload["SecretString"]).get("OpenAIKey")
    if not api_key:
        raise ValueError("OpenAIKey missing from API key secret.")
    openai_client.api_key = api_key


def _echo_payload(payload: dict) -> dict:
    out = dict(payload)
    out.pop("operation", None)
    return out


def _qanda_text(payload: dict) -> str:
    qanda = payload.get("qanda_content")
    if isinstance(qanda, dict):
        return str(qanda.get("qanda_content", "") or "")
    return str(qanda or "")


def _request_counts_dict(batch) -> dict:
    counts = getattr(batch, "request_counts", None)
    return {
        "total": getattr(counts, "total", 0) if counts is not None else 0,
        "completed": getattr(counts, "completed", 0) if counts is not None else 0,
        "failed": getattr(counts, "failed", 0) if counts is not None else 0,
    }


def submit_architect_batch(task: dict, artifact_prefix: str) -> str:
    tmp_path = f"/tmp/{artifact_prefix}_input.jsonl"
    with open(tmp_path, "wb") as f:
        f.write((json.dumps(task, ensure_ascii=False) + "\n").encode("utf-8"))

    with open(tmp_path, "rb") as f:
        file_obj = _call_openai(
            f"{artifact_prefix} files.create",
            openai_client.files.create,
            file=f,
            purpose="batch",
        )

    batch_job = _call_openai(
        f"{artifact_prefix} batches.create",
        openai_client.batches.create,
        input_file_id=file_obj.id,
        endpoint=BATCH_ENDPOINT_RESPONSES,
        completion_window="24h",
        metadata={"description": artifact_prefix},
    )
    print(
        f"Architect batch created: {batch_job.id} "
        f"input_file_id={getattr(batch_job, 'input_file_id', None)}"
    )
    return batch_job.id


def _load_architect_context(payload: dict) -> tuple[dict, str, str]:
    astrology_data = s3_get_json(payload["astrology_json_s3_path"])
    system_prompt, user_prompt = get_prompts_from_ssm(
        astrology_data,
        payload.get("focus", "Personality"),
        payload.get("language", "English"),
        _qanda_text(payload),
    )
    return astrology_data, system_prompt, user_prompt


def _build_architect_task(system_prompt: str, user_prompt: str) -> dict:
    return {
        "custom_id": "architect-structure",
        "method": "POST",
        "url": BATCH_ENDPOINT_RESPONSES,
        "body": architect_response_body(system_prompt, user_prompt),
    }


def op_submit_architect_batch(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    astrology_data, system_prompt, user_prompt = _load_architect_context(payload)
    task = _build_architect_task(system_prompt, user_prompt)
    batch_id = submit_architect_batch(task, "architect_structure")

    save_state(prefix, "architect_base_payload.json", _echo_payload(payload))
    save_state(prefix, "architect_task.json", {"task": task})
    save_state(
        prefix,
        "architect_pipeline.json",
        {
            "current_batch_id": batch_id,
            "retry_count": 0,
            "last_errors": [],
            "model": MODEL_ID,
            "reasoning_effort": REASONING_EFFORT_ARCHITECT,
            "astrology_json_s3_path": payload["astrology_json_s3_path"],
        },
    )
    print(f"Submitted Architect batch for {order_id}/{line_item_id}")
    return {
        **_echo_payload(payload),
        "architect_state_prefix": prefix,
        "architect_batch_id": batch_id,
    }


def op_check_architect_batch(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)
    pipe = load_state(prefix, "architect_pipeline.json") or {}
    batch_id = pipe.get("current_batch_id") or payload.get("architect_batch_id")
    if not batch_id:
        raise ValueError("No Architect batch id in state.")

    batch = _call_openai(
        "check architect batch retrieve",
        openai_client.batches.retrieve,
        batch_id,
    )
    counts = _request_counts_dict(batch)
    terminal = batch.status in {"completed", "failed", "expired", "cancelled"}
    print(
        f"check_architect_batch status={batch.status} terminal={terminal} "
        f"total={counts['total']} done={counts['completed']} failed={counts['failed']}"
    )
    return {
        **_echo_payload(payload),
        "architect_state_prefix": prefix,
        "architect_batch_id": batch_id,
        "architect_batch_status": batch.status,
        "architect_batch_terminal": terminal,
        "architect_request_counts": counts,
    }


def _architect_errors_from_batch(batch) -> list[str]:
    errors = getattr(batch, "errors", None)
    data = getattr(errors, "data", None) if errors is not None else None
    if not data:
        return []
    out = []
    for item in data:
        message = getattr(item, "message", None) or str(item)
        out.append(message)
    return out


def _extract_architect_batch_text(client, batch) -> tuple[str | None, list[str]]:
    if not getattr(batch, "output_file_id", None):
        return None, _architect_errors_from_batch(batch) or [
            f"batch ended with status={batch.status} and no output file"
        ]

    result_content = _call_openai(
        "architect batch output file content",
        client.files.content,
        batch.output_file_id,
    ).content
    errors: list[str] = []
    for line in result_content.decode("utf-8").strip().split("\n"):
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("custom_id") != "architect-structure":
            continue
        if entry.get("error"):
            return None, [f"architect batch request error: {entry['error']}"]
        response = entry.get("response") or {}
        if response.get("status_code") != 200:
            return None, [f"architect batch response status={response.get('status_code')}"]
        body = response.get("body") or {}
        if body.get("status") == "incomplete" or body.get("incomplete_details"):
            return None, [f"response incomplete: {body.get('incomplete_details')}"]
        raw_text = _extract_text_from_responses_body_dict(body)
        if raw_text:
            return raw_text, []
        errors.append("empty model response")
    return None, errors or ["architect-structure result not found in batch output"]


def _resubmit_architect_batch(prefix: str, pipe: dict, errors: list[str]) -> dict | None:
    retry_count = int(pipe.get("retry_count") or 0)
    if retry_count >= ARCHITECT_MAX_RETRIES:
        return None
    task_obj = load_state(prefix, "architect_task.json") or {}
    task = task_obj.get("task")
    if not task:
        return None
    retry_count += 1
    new_batch_id = submit_architect_batch(task, f"architect_structure_retry_{retry_count}")
    save_state(
        prefix,
        "architect_pipeline.json",
        {
            **pipe,
            "current_batch_id": new_batch_id,
            "retry_count": retry_count,
            "last_errors": errors,
        },
    )
    return {"architect_batch_id": new_batch_id, "retry_count": retry_count}


def op_collect_architect_result(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)
    pipe = load_state(prefix, "architect_pipeline.json") or {}
    batch_id = pipe.get("current_batch_id") or payload.get("architect_batch_id")
    if not batch_id:
        raise ValueError("No Architect batch id in state.")

    batch = _call_openai(
        "collect architect batch retrieve",
        openai_client.batches.retrieve,
        batch_id,
    )
    if batch.status not in {"completed", "failed", "expired", "cancelled"}:
        raise RuntimeError(f"Architect batch ended with status={batch.status}")

    astrology_data = s3_get_json(payload["astrology_json_s3_path"])
    raw_text, batch_errors = _extract_architect_batch_text(openai_client, batch)
    if batch_errors:
        print(f"Architect batch error(s): {batch_errors}")
    full_structure, validation_errors = validate_architect_raw_text(raw_text or "", astrology_data)
    errors = batch_errors + validation_errors
    if full_structure is None:
        replacement = _resubmit_architect_batch(prefix, pipe, errors)
        if replacement:
            return {
                **_echo_payload(payload),
                "architect_state_prefix": prefix,
                "architect_batch_id": replacement["architect_batch_id"],
                "architect_track_need_wait": True,
                "architect_collect_complete": False,
                "architect_retry_count": replacement["retry_count"],
            }
        raise ValueError(
            "Book structure invalid after async Architect retries: "
            + "; ".join(errors)
        )

    log_valid_structure(full_structure, astrology_data)
    out = save_book_structure(_echo_payload(payload), full_structure)
    out.update(
        {
            "architect_state_prefix": prefix,
            "architect_batch_id": batch_id,
            "architect_collect_complete": True,
            "architect_track_need_wait": False,
        }
    )
    save_state(
        prefix,
        "architect_pipeline.json",
        {
            **pipe,
            "current_batch_id": batch_id,
            "last_errors": [],
            "book_structure_s3_path": out["book_structure_s3_path"],
        },
    )
    return out


def lambda_handler(event, context):
    print(f"ArchitectBook received event: {json.dumps(event)}")
    payload = unwrap_payload(event)
    operation = payload.get("operation") if isinstance(payload, dict) else None

    if operation == "submit_architect_batch":
        return op_submit_architect_batch(payload)
    if operation == "check_architect_batch":
        return op_check_architect_batch(payload)
    if operation == "collect_architect_result":
        return op_collect_architect_result(payload)

    order_id = payload.get("order_id")
    line_item_id = payload.get("line_item_id")
    astrology_s3_path = payload.get("astrology_json_s3_path")
    focus = payload.get("focus", "Personality")
    language = payload.get("language", "English")
    qanda_content = _qanda_text(payload)

    if not all([order_id, line_item_id, astrology_s3_path]):
        raise ValueError("Missing required fields.")

    try:
        configure_openai()

        astrology_data = s3_get_json(astrology_s3_path)

        system_prompt, user_prompt = get_prompts_from_ssm(astrology_data, focus, language, qanda_content)
        full_structure = architect_book_structure(system_prompt, user_prompt, astrology_data)

        return save_book_structure(payload, full_structure)

    except Exception as e:
        print(f"ERROR: {e}")
        raise e
