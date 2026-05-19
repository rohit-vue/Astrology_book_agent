#!/usr/bin/env python3
"""
Image-only local test: OpenAI Batch API (/v1/images/generations, gpt-image-2).

Reads existing chapter text from output/artifacts/chapter_*.json (no architect,
no chapter-text batch, no PDF). Uses a short excerpt from each chapter as the
image prompt summary (no extra GPT call).

Usage:
  docker compose run --rm batch-images-from-artifacts
  docker compose run --rm batch-images-from-artifacts -- --chapters 1,3
"""
import argparse
import json
import os
import re
import sys

from dotenv import load_dotenv
from openai import OpenAI

# Reuse batch helpers from the full pipeline (no changes to that file).
from run_local_batch_pipeline import (
    ARTIFACTS_DIR,
    IMAGES_DIR,
    IMAGE_PROMPT_TEMPLATE,
    MAX_BATCH_RETRIES,
    MODEL_IMAGE,
    apply_images_to_chapters,
    collect_image_batch_results,
    poll_batch_until_done,
    submit_chapter_batch,
)

load_dotenv("/app/.env")

BATCH_ENDPOINT_IMAGES = "/v1/images/generations"
IMAGE_SIZE = "1024x1536"
ARTIFACT_PREFIX = "chapter_image_artifacts_test"


def _chapter_json_sort_key(path: str) -> int:
    m = re.search(r"chapter_(\d+)\.json$", path, re.I)
    return int(m.group(1)) if m else 0


def load_chapters_from_artifacts(chapter_filter: set[int] | None) -> list[dict]:
    """Load chapter_N.json into chapters_data shape expected by image batch helpers."""
    if not os.path.isdir(ARTIFACTS_DIR):
        print(f"ERROR: Artifacts directory not found: {ARTIFACTS_DIR}")
        sys.exit(1)

    chapter_files = [
        os.path.join(ARTIFACTS_DIR, fn)
        for fn in os.listdir(ARTIFACTS_DIR)
        if re.match(r"chapter_\d+\.json$", fn, re.I)
    ]
    chapter_files.sort(key=_chapter_json_sort_key)

    if not chapter_files:
        print(f"ERROR: No chapter_*.json in {ARTIFACTS_DIR}. Run the text pipeline first.")
        sys.exit(1)

    chapters_data = []
    for ch_path in chapter_files:
        idx = _chapter_json_sort_key(ch_path)
        if chapter_filter is not None and idx not in chapter_filter:
            continue

        with open(ch_path, "r", encoding="utf-8") as f:
            ch = json.load(f)

        text = (ch.get("chapter_text") or "").strip()
        title = (ch.get("chapter_title") or f"Chapter {idx}").strip()
        if not text:
            print(f"WARNING: Skipping {ch_path} — empty chapter_text")
            continue

        chapters_data.append({
            "chapter_index": idx,
            "chapter_title": title,
            "chapter_text": text,
            "image_path": None,
        })

    if not chapters_data:
        print("ERROR: No chapters matched your filter or all were empty.")
        sys.exit(1)

    return chapters_data


def build_image_batch_tasks_from_artifacts(chapters_data: list[dict]) -> tuple[list, dict]:
    """Build image batch JSONL tasks using chapter excerpt only (no GPT summary)."""
    print("\n" + "-" * 50)
    print(f"  IMAGE BATCH (artifacts): Building tasks (model={MODEL_IMAGE})...")
    print("-" * 50)

    tasks = []
    manifest = {}

    for ch in chapters_data:
        idx = ch["chapter_index"]
        title = ch["chapter_title"]
        text = ch["chapter_text"]
        custom_id = f"image-{idx}"

        summary = text[:700].strip()
        img_prompt = (
            IMAGE_PROMPT_TEMPLATE
            .replace("__CHAPTER_TITLE__", title)
            .replace("__SUMMARY__", summary)
        )

        task = {
            "custom_id": custom_id,
            "method": "POST",
            "url": BATCH_ENDPOINT_IMAGES,
            "body": {
                "model": MODEL_IMAGE,
                "prompt": img_prompt,
                "n": 1,
                "size": IMAGE_SIZE,
            },
        }
        tasks.append(task)
        manifest[custom_id] = {"chapter_index": idx, "chapter_title": title}

        print(f"    Task '{custom_id}': Chapter {idx} — {title}")
        print(f"      Prompt length: {len(img_prompt):,} chars (summary from excerpt, no GPT)")

    print(f"\n  IMAGE BATCH: {len(tasks)} image task(s) built.")
    return tasks, manifest


def run_image_batch_from_artifacts(chapters_data: list[dict]) -> list[dict]:
    """Submit image batch, poll, retry failures, write PNGs under output/images/."""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    image_tasks, image_manifest = build_image_batch_tasks_from_artifacts(chapters_data)
    image_batch_id = submit_chapter_batch(
        client,
        image_tasks,
        image_manifest,
        artifact_prefix=ARTIFACT_PREFIX,
        endpoint=BATCH_ENDPOINT_IMAGES,
    )
    image_batch = poll_batch_until_done(client, image_batch_id)

    if image_batch.status != "completed":
        if image_batch.status == "expired" and image_batch.output_file_id:
            print("  WARNING: Image batch expired — collecting partial results.")
        else:
            print(f"  ERROR: Image batch ended with status={image_batch.status}")
            sys.exit(1)

    merged_images_by_id, failed_image_ids = collect_image_batch_results(
        client,
        image_batch,
        image_manifest,
        artifact_prefix=ARTIFACT_PREFIX,
    )

    for retry_num in range(1, MAX_BATCH_RETRIES + 1):
        if not failed_image_ids:
            break

        retry_ids = sorted(failed_image_ids)
        print("\n" + "-" * 50)
        print(f"  IMAGE BATCH RETRY {retry_num}: Retrying {len(retry_ids)} failed/missing...")
        print("-" * 50)

        retry_tasks = [t for t in image_tasks if t["custom_id"] in failed_image_ids]
        if not retry_tasks:
            break

        retry_batch_id = submit_chapter_batch(
            client,
            retry_tasks,
            image_manifest,
            artifact_prefix=f"{ARTIFACT_PREFIX}_retry_{retry_num}",
            endpoint=BATCH_ENDPOINT_IMAGES,
        )
        retry_batch = poll_batch_until_done(client, retry_batch_id)

        if retry_batch.status != "completed":
            if retry_batch.status == "expired" and retry_batch.output_file_id:
                print(f"  WARNING: Retry batch {retry_num} expired — collecting partial results.")
            else:
                print(f"  WARNING: Retry batch {retry_num} status={retry_batch.status}")
                break

        retry_images_by_id, retry_failed_ids = collect_image_batch_results(
            client,
            retry_batch,
            image_manifest,
            artifact_prefix=f"{ARTIFACT_PREFIX}_retry_{retry_num}",
        )
        merged_images_by_id.update(retry_images_by_id)
        failed_image_ids = (set(retry_ids) - set(retry_images_by_id.keys())) | (
            set(retry_failed_ids) & set(retry_ids)
        )

    os.makedirs(IMAGES_DIR, exist_ok=True)
    chapters_data, missing = apply_images_to_chapters(
        chapters_data, image_manifest, merged_images_by_id,
    )

    if missing:
        print(f"\n  WARNING: Missing images after retries: {missing}")
        sys.exit(1)

    return chapters_data


def main():
    parser = argparse.ArgumentParser(description="Batch image generation from chapter artifacts")
    parser.add_argument(
        "--chapters",
        type=str,
        default="",
        help="Comma-separated chapter numbers to process (e.g. 1,3). Default: all chapter_*.json",
    )
    args = parser.parse_args()

    chapter_filter = None
    if args.chapters.strip():
        chapter_filter = {int(x.strip()) for x in args.chapters.split(",") if x.strip()}

    print("=" * 60)
    print("IMAGE BATCH TEST (from artifacts)")
    print("=" * 60)
    print(f"  Model:     {MODEL_IMAGE}")
    print(f"  Endpoint:  {BATCH_ENDPOINT_IMAGES}")
    print(f"  Artifacts: {ARTIFACTS_DIR}")
    print(f"  Images:    {IMAGES_DIR}")
    if chapter_filter:
        print(f"  Chapters:  {sorted(chapter_filter)}")
    else:
        print("  Chapters:  all chapter_*.json")

    chapters_data = load_chapters_from_artifacts(chapter_filter)
    print(f"\n  Loaded {len(chapters_data)} chapter(s) for image generation.")

    run_image_batch_from_artifacts(chapters_data)

    print("\n" + "=" * 60)
    print("IMAGE BATCH TEST COMPLETE")
    print("=" * 60)
    for ch in chapters_data:
        print(f"  Chapter {ch['chapter_index']}: {ch.get('image_path')}")


if __name__ == "__main__":
    main()
