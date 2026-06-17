#!/usr/bin/env python3
"""Dry-run write_chapters submit_text_batch: real S3/SSM/OpenAI style, no batch submit."""
import asyncio
import json
import os
import re
import sys

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ARTIFACTS_BUCKET", "astrology-artifacts-luminary-prod-v1")
os.environ.setdefault(
    "API_KEYS_SECRET_ARN",
    "arn:aws:secretsmanager:us-east-1:926890291123:secret:AstrologyBookFactory-ApiKeys-V2-RkGN3q",
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "write_chapters"))

import app as wc  # noqa: E402

STEP_FN_EVENT = {
    "Payload": {
        "cover_title": "Tyler Gumpta",
        "line_item_id": "16770688057594",
        "focus": "Personality",
        "language": "english",
        "order_id": "shpfy_7032977359098",
        "astrology_json_s3_path": "s3://astrology-artifacts-luminary-prod-v1/astrology-json/shpfy_7032977359098/16770688057594.json",
        "book_structure_s3_path": "s3://astrology-artifacts-luminary-prod-v1/book-structures/shpfy_7032977359098/16770688057594.json",
    }
}

LOCAL_ARTIFACTS = os.path.join(os.path.dirname(__file__), "output")
DRY_STATE_DIR = os.path.join(LOCAL_ARTIFACTS, "dry_run_state")


def _patch_for_dry_run():
    original_save = wc.save_state
    original_submit = wc.submit_batch

    def dry_save_state(prefix, name, obj):
        os.makedirs(DRY_STATE_DIR, exist_ok=True)
        path = os.path.join(DRY_STATE_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        print(f"[dry-run] saved state -> {path}")

    def dry_submit_batch(client, tasks, endpoint, artifact_prefix):
        print(f"[dry-run] skipped OpenAI batch submit ({len(tasks)} tasks, endpoint={endpoint})")
        return "dry_run_batch_id"

    wc.save_state = dry_save_state
    wc.submit_batch = dry_submit_batch
    wc.configure_openai = lambda: None
    return original_save, original_submit


def _audit_prompts(tasks, template):
    issues = []
    placeholders_in_template = sorted(set(re.findall(r"__[A-Z0-9_]+__", template)))
    print(f"\nSSM template placeholders: {placeholders_in_template}")

    for task in tasks:
        custom_id = task["custom_id"]
        prompt = task["body"]["input"][0]["content"]
        leftover = sorted(set(re.findall(r"__[A-Z0-9_]+__", prompt)))
        if leftover:
            issues.append(f"{custom_id}: unreplaced {leftover}")
        else:
            print(f"  OK {custom_id}: prompt length={len(prompt)} chars, no leftover tokens")
        if custom_id == "chapter-1":
            snippet = prompt[:500].replace("\n", " ")
            print(f"  chapter-1 preview: {snippet[:400]}...")

    return issues


async def main():
    _patch_for_dry_run()
    payload = wc.unwrap_payload(STEP_FN_EVENT)
    payload["operation"] = "submit_text_batch"

    print("=== write_chapters dry run ===")
    print(f"order_id={payload['order_id']} line_item_id={payload['line_item_id']}")
    print(f"focus={payload.get('focus')} language={payload.get('language')}")

    try:
        result = await wc.op_submit_text_batch(payload)
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        raise

    print("\n=== submit_text_batch result ===")
    print(json.dumps(
        {k: result.get(k) for k in ("order_id", "line_item_id", "wc_state_prefix", "wc_text_batch_id")},
        indent=2,
    ))

    tasks_path = os.path.join(DRY_STATE_DIR, "text_tasks.json")
    with open(tasks_path, encoding="utf-8") as f:
        tasks = json.load(f)["tasks"]

    template = wc.get_chapter_prompt_template()
    issues = _audit_prompts(tasks, template)

    meta_path = os.path.join(DRY_STATE_DIR, "wc_sections_meta.json")
    with open(meta_path, encoding="utf-8") as f:
        sections_meta = json.load(f)
    section_manifest_path = os.path.join(DRY_STATE_DIR, "text_section_manifest.json")
    with open(section_manifest_path, encoding="utf-8") as f:
        section_manifest = json.load(f)

    print(f"\nDeterministic style ({len(sections_meta.get('style', ''))} chars):")
    print(sections_meta.get("style", "")[:300])
    print(f"Section batch tasks: {len(section_manifest)} ({', '.join(section_manifest.keys())})")
    print(f"Total batch tasks (chapters + sections): {len(tasks)}")

    if issues:
        print("\nPROMPT ISSUES:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print("\nDry run PASSED: no unreplaced prompt placeholders; batch submit was skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
