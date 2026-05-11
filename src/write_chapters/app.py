import boto3
import json
import os
import asyncio
import time
import base64
from openai import AsyncOpenAI, OpenAI
from urllib.parse import urlparse


s3_client = boto3.client("s3")
secrets_manager_client = boto3.client("secretsmanager")
ssm_client = boto3.client("ssm")
async_openai_client = AsyncOpenAI(api_key="dummy")
sync_openai_client = OpenAI(api_key="dummy")

API_KEYS_SECRET_ARN = os.environ.get("API_KEYS_SECRET_ARN")
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET")
MODEL_TEXT = "gpt-5.2-2025-12-11"
MODEL_IMAGE = "gpt-image-1.5"
MODEL_STABLE = "gpt-4o"

BATCH_POLL_INTERVAL = 15
CHAPTER_WORD_TARGET = 10000
CHAPTER_WORD_MIN = 9000
CHAPTER_WORD_MAX = 10500
CHAPTER_MAX_COMPLETION_TOKENS = 12000
MAX_BATCH_RETRIES = 1

IMAGE_PROMPT_FALLBACK = (
    "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. "
    "Style: ethereal, cosmic, rich colors. CRITICAL: NO text, letters, or figures."
)


def unwrap_payload(event):
    """Step Functions lambda:invoke wraps handler return in Payload; nested tasks may repeat."""
    return event.get("Payload", event)


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


async def generate_section(name, description, style, language):
    if not description:
        return ""
    print(f"Generating {name}...")
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
        resp = await async_openai_client.chat.completions.create(
            model=MODEL_STABLE,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800,
        )
        text = resp.choices[0].message.content.strip()
        print(f"✅ {name} SUCCESS: {len(text)} chars")
        return text
    except Exception as e:
        print(f"❌ Error generating {name}: {e}")
        return ""


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


def build_chapter_batch_tasks(chapters_list, astrology_data, focus, style, language, word_target):
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
            f"Do **not** settle into a steady rhythm where every paragraph is the same size.\n"
            f"- **Short paragraphs are allowed** for emphasis, a turn in thought, or a breath between ideas—use them **sometimes**, not after every sentence.\n"
            f"- Longer paragraphs are fine when the idea needs room; neighbor paragraphs may be much shorter so the page does not look like uniform blocks.\n"
            f"- Use **single newlines** only when you must break a long paragraph; prefer joining sentences in the same paragraph with spaces.\n"
            f"- Use **double newlines (blank line)** ONLY between **major sections**. **At most 8–10 double-newlines in the whole chapter.**\n"
            f"**Output Rule:** Return only final chapter prose.\n"
            f"**Data:** {json.dumps(astrology_data)}"
        )

        tasks.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": MODEL_TEXT,
                    "temperature": 0.5,
                    "max_completion_tokens": CHAPTER_MAX_COMPLETION_TOKENS,
                    "messages": [{"role": "user", "content": prompt}],
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
        raise RuntimeError(f"Batch {batch.id} has no output file (status={batch.status})")
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
        chapter_text = response["body"]["choices"][0]["message"]["content"].strip()
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
            sum_resp = await async_openai_client.chat.completions.create(
                model=MODEL_TEXT,
                messages=[{"role": "user", "content": f"Summarize text for image: {text[:1200]}"}],
                max_completion_tokens=120,
            )
            summary = sum_resp.choices[0].message.content.strip()
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
        raise RuntimeError(f"Image batch {batch.id} has no output file (status={batch.status})")
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

    return image_bytes_by_id, failed_ids


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
    secret = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    api_key = json.loads(secret["SecretString"]).get("OpenAIKey")
    async_openai_client.api_key = api_key
    sync_openai_client.api_key = api_key


async def prepare_style_and_sections(chart, structure, focus, language):
    style_chart = build_style_chart_snapshot(chart)
    try:
        style_resp = await async_openai_client.chat.completions.create(
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
                ),
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
        print(f"Style generation failed, using fallback style: {e}")
        style = (
            "Tone: Warm, psychologically precise, compassionate, direct second-person. "
            "Voice rules: concrete language; grounded interpretation; practical guidance; emotionally honest pacing; no fluff. "
            "Avoid: generic filler; moralizing; vague advice; melodrama."
        )

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

    preface_text = await generate_section("Preface", preface_desc, style, language)
    prologue_text = await generate_section("Prologue", prologue_desc, style, language)

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

    try:
        image_prompt_template = ssm_client.get_parameter(
            Name="/AstrologyBookFactory/prompts/writer/image",
            WithDecryption=True,
        )["Parameter"]["Value"]
    except Exception:
        print("SSM image prompt not found; using fallback.")
        image_prompt_template = IMAGE_PROMPT_FALLBACK

    sections = await prepare_style_and_sections(chart, structure, focus, language)

    struct_inner = structure.get("structure", {})
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    tasks, manifest = build_chapter_batch_tasks(
        chapters_list, chart, focus, sections["style"], language, CHAPTER_WORD_TARGET
    )

    save_state(prefix, "text_manifest.json", manifest)
    save_state(prefix, "text_tasks.json", {"tasks": tasks})
    save_state(prefix, "wc_sections.json", sections)
    save_state(prefix, "wc_structure_snapshot.json", {"chapters_list": chapters_list})

    batch_id = submit_batch(sync_openai_client, tasks, "/v1/chat/completions", "chapter_text")
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
    counts = batch.request_counts
    terminal = batch.status in {"completed", "failed", "expired", "cancelled"}
    print(
        f"check_text_batch status={batch.status} terminal={terminal} "
        f"total={counts.total} done={counts.completed} failed={counts.failed}"
    )

    out = {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_text_batch_id": batch_id,
        "wc_text_batch_status": batch.status,
        "wc_text_batch_terminal": terminal,
        "wc_text_request_counts": {
            "total": counts.total,
            "completed": counts.completed,
            "failed": counts.failed,
        },
    }
    return out


async def op_collect_text_results(payload: dict) -> dict:
    configure_openai()
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]
    prefix = state_prefix(order_id, line_item_id)

    pipe = load_state(prefix, "text_pipeline.json") or {}
    manifest = load_state(prefix, "text_manifest.json") or {}
    batch_id = pipe.get("current_batch_id")

    batch = sync_openai_client.batches.retrieve(batch_id)
    if batch.status not in {"completed", "expired"}:
        raise RuntimeError(f"Chapter text batch ended with status={batch.status}")

    merged = pipe.get("merged_results_by_id") or {}
    new_results, failed_ids = collect_chapter_batch_results(sync_openai_client, batch)
    merged.update(new_results)

    retry_count = int(pipe.get("retry_count") or 0)
    missing_ids = set(manifest.keys()) - set(merged.keys())
    failed_custom = (failed_ids | missing_ids) & set(manifest.keys())

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
            "/v1/chat/completions",
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

    chapters_data = build_chapters_data_from_results(merged, manifest, order_id, line_item_id)
    if not chapters_data:
        raise ValueError("No chapter texts were produced by batch jobs.")

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

    try:
        image_prompt_template = ssm_client.get_parameter(
            Name="/AstrologyBookFactory/prompts/writer/image",
            WithDecryption=True,
        )["Parameter"]["Value"]
    except Exception:
        image_prompt_template = IMAGE_PROMPT_FALLBACK

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
            "merged_images_b64_by_id": {},
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
    counts = batch.request_counts
    terminal = batch.status in {"completed", "failed", "expired", "cancelled"}
    print(
        f"check_image_batch status={batch.status} terminal={terminal} "
        f"total={counts.total} done={counts.completed} failed={counts.failed}"
    )

    return {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_image_batch_id": batch_id,
        "wc_image_batch_status": batch.status,
        "wc_image_batch_terminal": terminal,
        "wc_image_request_counts": {
            "total": counts.total,
            "completed": counts.completed,
            "failed": counts.failed,
        },
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
    if batch.status not in {"completed", "expired"}:
        print(f"Image batch ended with status={batch.status}; skipping images.")
        save_state(prefix, "image_urls_by_index.json", {})
        return {
            **_echo_payload(payload),
            "wc_state_prefix": prefix,
            "wc_image_collect_complete": True,
            "wc_image_track_need_wait": False,
        }

    merged_b64 = pipe.get("merged_images_b64_by_id") or {}
    merged = {k: base64.b64decode(v) for k, v in merged_b64.items()}
    new_images, failed_ids = collect_image_batch_results(sync_openai_client, batch)
    merged.update(new_images)

    retry_count = int(pipe.get("retry_count") or 0)
    missing_ids = set(manifest.keys()) - set(merged.keys())
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
        merged_b64_out = {k: base64.b64encode(v).decode("ascii") for k, v in merged.items()}
        save_state(
            prefix,
            "image_pipeline.json",
            {
                **pipe,
                "merged_images_b64_by_id": merged_b64_out,
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

    urls_by_index = {}
    for custom_id, info in manifest.items():
        idx = info["chapter_index"]
        raw = merged.get(custom_id)
        if raw is None:
            continue
        key = f"chapter-images/{order_id}/{line_item_id}/chapter_{idx}.png"
        s3_client.put_object(Bucket=ARTIFACTS_BUCKET, Key=key, Body=raw, ContentType="image/png")
        image_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": ARTIFACTS_BUCKET, "Key": key},
            ExpiresIn=86400,
        )
        urls_by_index[str(idx)] = image_url

    save_state(prefix, "image_urls_by_index.json", urls_by_index)

    return {
        **_echo_payload(payload),
        "wc_state_prefix": prefix,
        "wc_image_collect_complete": True,
        "wc_image_track_need_wait": False,
    }


async def op_finalize(payload: dict) -> dict:
    configure_openai()
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
    epilogue_desc = sections.get("epilogue_desc") or ""

    epilogue_text = await generate_section("Epilogue", epilogue_desc, sections.get("style", ""), base.get("language", "English"))

    final_output = dict(base)
    final_output["full_book_structure"] = structure
    final_output["generated_sections"] = {
        "preface": sections.get("preface_text", ""),
        "prologue": sections.get("prologue_text", ""),
        # Foreword is sourced by generate_pdf from assets/foreword.txt.
        "foreword": "",
        "epilogue": epilogue_text,
    }
    final_output["full_book_structure"]["preface_text"] = sections.get("preface_text", "")
    final_output["full_book_structure"]["prologue_text"] = sections.get("prologue_text", "")
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
        return await legacy_full_pipeline(payload)

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

    try:
        image_prompt_template = ssm_client.get_parameter(
            Name="/AstrologyBookFactory/prompts/writer/image",
            WithDecryption=True,
        )["Parameter"]["Value"]
    except Exception:
        print("SSM image prompt not found; using fallback.")
        image_prompt_template = IMAGE_PROMPT_FALLBACK

    sections = await prepare_style_and_sections(chart, structure, focus, language)
    style = sections["style"]

    struct_inner = structure.get("structure", {})
    chapters_list = structure.get("chapters") or struct_inner.get("chapters", [])
    tasks, manifest = build_chapter_batch_tasks(
        chapters_list, chart, focus, style, language, CHAPTER_WORD_TARGET
    )

    batch_id = submit_batch(sync_openai_client, tasks, "/v1/chat/completions", "chapter_text")
    batch = poll_batch_until_done(sync_openai_client, batch_id)
    if batch.status not in {"completed", "expired"}:
        raise RuntimeError(f"Chapter text batch ended with status={batch.status}")

    merged_results_by_id, failed_ids = collect_chapter_batch_results(sync_openai_client, batch)
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
            "/v1/chat/completions",
            f"chapter_text_retry_{retry_num}",
        )
        retry_batch = poll_batch_until_done(sync_openai_client, retry_batch_id)
        if retry_batch.status not in {"completed", "expired"}:
            break
        retry_results_by_id, retry_failed_ids = collect_chapter_batch_results(sync_openai_client, retry_batch)
        merged_results_by_id.update(retry_results_by_id)
        failed_ids = set(retry_ids) - set(retry_results_by_id.keys())
        failed_ids |= (set(retry_failed_ids) & set(retry_ids))

    chapters_data = build_chapters_data_from_results(
        merged_results_by_id, manifest, order_id, line_item_id
    )
    if not chapters_data:
        raise ValueError("No chapter texts were produced by batch jobs.")

    chapters_data = await generate_chapter_images_batch(
        chapters_data, image_prompt_template, order_id, line_item_id
    )

    epilogue_text = await generate_section(
        "Epilogue",
        sections["epilogue_desc"],
        style,
        language,
    )

    final_output = payload
    final_output["full_book_structure"] = structure
    final_output["generated_sections"] = {
        "preface": sections["preface_text"],
        "prologue": sections["prologue_text"],
        # Foreword is sourced by generate_pdf from assets/foreword.txt.
        "foreword": "",
        "epilogue": epilogue_text,
    }
    final_output["full_book_structure"]["preface_text"] = sections["preface_text"]
    final_output["full_book_structure"]["prologue_text"] = sections["prologue_text"]
    final_output["full_book_structure"]["epilogue_text"] = epilogue_text
    if "metadata" not in final_output["full_book_structure"]:
        final_output["full_book_structure"]["metadata"] = structure.get(
            "metadata", structure.get("book_metadata", {})
        )
    final_output["chapters_data"] = chapters_data

    return final_output


def lambda_handler(event, context):
    return asyncio.run(async_lambda_handler(event, context))
