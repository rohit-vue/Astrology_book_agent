import boto3
import json
import os
import asyncio
from openai import AsyncOpenAI
from urllib.parse import urlparse

# --- AWS & OpenAI Clients ---
s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
openai_client = AsyncOpenAI(api_key="dummy")

# --- Environment Variables ---
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
MODEL_TEXT = "gpt-4-1106-preview"
MODEL_IMAGE = "dall-e-3"

# --- Helper Functions (No changes here) ---
def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

def build_dynamic_chapter_prompt(chapter_details, natal_chart, word_target):
    # This is a placeholder. You should replace this with your actual, detailed prompt.
    chapter_theme = chapter_details.get('theme_title', 'Untitled Chapter')
    chapter_summary = chapter_details.get('summary', 'No summary provided.')
    return f"""
    As an expert psychological astrologer, write a chapter for a personal book.
    DO NOT use astrological jargon (planets, signs, houses). Translate all concepts into psychological and experiential language.
    The chapter's theme is: "{chapter_theme}"
    Here is a summary of the core ideas to explore: "{chapter_summary}"
    The chapter should be approximately {word_target} words.
    Base your interpretation on the following symbolic data:
    {json.dumps(natal_chart, indent=2)}
    """

def build_summarization_prompt(text):
    return f"Summarize the following text for an image generation prompt, focusing on the core feeling, symbols, and abstract concepts. Be concise and evocative. The summary should be in a single paragraph. Text: {text}"

async def write_and_illustrate_chapter(chapter_details, natal_chart, word_target, order_id, line_item_id, chapter_index):
    chapter_title = chapter_details['theme_title']
    print(f"--- Starting Chapter {chapter_index} for line item {line_item_id}: {chapter_title} ---")

    # 1. Generate chapter text
    chapter_prompt = build_dynamic_chapter_prompt(chapter_details, natal_chart, word_target)
    text_response = await openai_client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role": "user", "content": chapter_prompt}],
        temperature=0.3
    )
    chapter_text = text_response.choices[0].message.content.strip()

    # 2. Generate summary
    summary_prompt = build_summarization_prompt(chapter_text)
    summary_response = await openai_client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role": "user", "content": summary_prompt}],
        temperature=0.2,
        max_tokens=150
    )
    chapter_summary = summary_response.choices[0].message.content.strip()

    # 3. Generate image
    safe_summary = chapter_summary.replace("\n", " ")[:350] # DALL-E 3 has a longer prompt limit
    image_prompt = f"Digital art, ethereal and abstract, visually representing the core emotional and symbolic essence of this concept: '{safe_summary}'. Use a rich, deep color palette. Avoid text and human figures."
    image_url = None
    try:
        image_response = await openai_client.images.generate(
            model=MODEL_IMAGE,
            prompt=image_prompt,
            size="1024x1024",
            quality="standard",
            n=1
        )
        image_url = image_response.data[0].url
    except Exception as e:
        print(f"Image generation failed or was skipped for chapter {chapter_index}: {e}")

    # 4. Save full chapter content to its own S3 file
    # This is better than passing large text blobs through Step Functions
    chapter_s3_key = f"chapters-json/{order_id}/{line_item_id}/chapter_{chapter_index}.json"
    s3_client.put_object(
        Bucket=ARTIFACTS_BUCKET,
        Key=chapter_s3_key,
        Body=json.dumps({
            "chapter_title": chapter_title,
            "chapter_text": chapter_text,
            "image_prompt": image_prompt,
        }, indent=2),
        ContentType="application/json"
    )
    s3_path = f"s3://{ARTIFACTS_BUCKET}/{chapter_s3_key}"

    # Return a lightweight summary for the next step
    return {
        "chapter_index": chapter_index,
        "chapter_title": chapter_title,
        "chapter_text_s3_path": s3_path,
        "image_url": image_url
    }

# --- Lambda Handlers ---
def lambda_handler(event, context):
    return asyncio.run(async_lambda_handler(event, context))

async def async_lambda_handler(event, context):
    print(f"WriteChapters received event: {json.dumps(event, indent=2)}")

    # --- START OF THE FIX ---
    # The previous Lambda's result is wrapped in a "Payload" key.
    if 'Payload' in event:
        print("Detected nested 'Payload'. Unwrapping...")
        payload = event['Payload']
    else:
        payload = event

    # Extract all inputs from the 'payload' object
    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    astrology_s3_path = payload.get('astrology_json_s3_path')
    book_structure_s3_path = payload.get('book_structure_s3_path')
    # --- END OF THE FIX ---

    if not all([order_id, line_item_id, astrology_s3_path, book_structure_s3_path]):
        raise ValueError(f"Missing required S3 paths or IDs after processing payload. Full incoming event: {json.dumps(event)}")

    try:
        # Configure OpenAI client from Secrets Manager
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        openai_client.api_key = json.loads(secret_payload['SecretString']).get('OpenAIKey')

        # Download JSON files
        bucket_astrology, key_astrology = parse_s3_path(astrology_s3_path)
        natal_chart = json.loads(s3_client.get_object(Bucket=bucket_astrology, Key=key_astrology)['Body'].read().decode('utf-8'))

        bucket_structure, key_structure = parse_s3_path(book_structure_s3_path)
        book_structure = json.loads(s3_client.get_object(Bucket=bucket_structure, Key=key_structure)['Body'].read().decode('utf-8'))

        # Process chapters concurrently
        chapters = book_structure.get("chapters", [])
        chapters_output = await asyncio.gather(*[
            write_and_illustrate_chapter(ch, natal_chart, 800, order_id, line_item_id, idx + 1)
            for idx, ch in enumerate(chapters)
        ])

        # Pass all necessary data along to the next step (GeneratePDF)
        final_output = {
            "order_id": order_id,
            "line_item_id": line_item_id,
            "chapters_data": chapters_output
        }
        return final_output

    except Exception as e:
        print(f"ERROR: {e}")
        raise e