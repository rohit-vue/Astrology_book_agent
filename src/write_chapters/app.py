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
    Write the {name} for a personal astrology book.
    Language: {language}
    Style: {style}
    Context: {description}
    Directive: Write in second person ("You"). Start directly. Plain text only.
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
        failed_image_ids = set(retry_ids) - set(retry_images_by_id.keys())
        failed_image_ids |= (set(retry_failed_ids) & set(retry_ids))

    return apply_images_to_chapters(
        chapters_data, image_manifest, merged_images_by_id, order_id, line_item_id
    )


async def async_lambda_handler(event, context):
    print(f"WriteChapters received event: {json.dumps(event, indent=2)}")
    payload = event.get("Payload", event)

    order_id = payload.get("order_id")
    line_item_id = payload.get("line_item_id")
    focus = payload.get("focus", "Personality")
    language = payload.get("language", "English")
    if not all([order_id, line_item_id, payload.get("astrology_json_s3_path"), payload.get("book_structure_s3_path")]):
        raise ValueError("Missing required fields.")

    secret = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    api_key = json.loads(secret["SecretString"]).get("OpenAIKey")
    async_openai_client.api_key = api_key
    sync_openai_client.api_key = api_key

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
        style = "Tone: Warm, psychologically precise, compassionate, direct second-person. Voice rules: concrete language; grounded interpretation; practical guidance; emotionally honest pacing; no fluff. Avoid: generic filler; moralizing; vague advice; melodrama."

    struct_inner = structure.get("structure", {})
    preface_desc = structure.get("preface_description") or struct_inner.get("preface_description") or \
        "Write a warm, welcoming preface setting the stage for a journey of self-discovery based on the user's astrology."
    prologue_desc = structure.get("prologue_description") or struct_inner.get("prologue_description") or \
        "Write an introduction that explains the core themes of the book and invites the reader to explore their inner world."
    epilogue_desc = structure.get("epilogue_description") or struct_inner.get("epilogue_description") or \
        "Write a concluding chapter that synthesizes the journey, offering encouragement and a call to action for the future."

    master_foreword = """Thank you. Thank you for taking the step. Thank you for opening a book that is not quite like any other book you have held in your hands. A self help book created with the help of artificial intelligence can feel strange. It can feel like a strange kind of mirror. It can feel like a strange kind of promise. It can feel like something between a tool and a prophecy. It may make you wonder if the human heart can truly be guided by something made of code. It may make you wonder if the answers you need can be generated. It may make you wonder if your pain can be understood by something that does not feel pain.

And that is a valid concern.

We are living in an age of profound disconnect. We are living in a time when loneliness has become an epidemic, when people feel isolated even in crowded rooms, when social media gives us the illusion of connection while quietly starving us of the real thing. We are living in a time when we perform our lives more than we live them. And in this noise, it is easy to lose the signal of who we actually are.

But here is the truth about this book: The intelligence may be artificial, but the data is yours. The patterns are yours. The story is yours.

Astrology is an ancient language, a way of mapping the invisible currents that shape a life. For centuries, it has been used to help people understand their nature, their cycles, and their potential. What we have done here is use modern technology to translate that ancient language into a narrative that speaks directly to you.

Think of this AI not as an author, but as a translator. It is taking the complex, mathematical snapshot of the sky at the moment you were born and translating it into words, sentences, and chapters. It is synthesizing vast amounts of astrological wisdom to find the specific threads that weave together to form the tapestry of your personality.

It is not magic. It is a reflection.

As you read these pages, take what resonates and leave what does not. Use this book as a starting point for your own inquiry. Let it provoke you, comfort you, challenge you, and validate you. Let it be a conversation starter between you and your own soul.

You are the only expert on your own life. This book is simply a guide, a map generated from the stars to help you navigate the terrain of your own becoming.

Welcome to your Blueprint."""
    final_foreword_text = master_foreword
    print("Foreword language fixed to English (translation skipped).")

    preface_text = await generate_section("Preface", preface_desc, style, language)
    prologue_text = await generate_section("Prologue", prologue_desc, style, language)

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

    epilogue_text = await generate_section("Epilogue", epilogue_desc, style, language)

    final_output = payload
    final_output["full_book_structure"] = structure
    final_output["generated_sections"] = {
        "preface": preface_text,
        "prologue": prologue_text,
        "foreword": final_foreword_text,
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