# FILE: src/architect_book/app.py (FINAL MASTER PROMPT VERSION)

import boto3
import json
import os
from openai import OpenAI
from urllib.parse import urlparse

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
openai_client = OpenAI(api_key="dummy")

def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

def build_book_structure_prompt(natal_chart_json: dict, num_chapters: int) -> str:
    user_astrology_data = json.dumps(natal_chart_json, indent=2)

    # THIS IS THE FINAL, CORRECTED "MASTER PROMPT"
    return f"""
You are a master psychological interpreter and book architect. Your task is to analyze the provided symbolic data (a natal chart) and design a thematic, narrative structure for a deeply personal book.

**CREATIVE MANDATE:**
- Analyze the data holistically to find core psychological patterns, tensions, and narratives.
- Create chapter titles that are thematic, evocative, and beautiful (e.g., "The Quest for Identity," "The Dance of Relationships").
- **CRITICAL: You MUST NOT use any astrological jargon** (e.g., planets, signs, houses, aspects, Gemini, Aries). Translate EVERYTHING into psychological and experiential language.

**TECHNICAL MANDATE:**
- Your final output MUST be a single, valid JSON object and nothing else.
- You MUST adhere strictly to the JSON schema below. DO NOT omit or rename any keys.

**JSON Schema:**
```json
{{
  "title": "The Architecture of You",
  "subtitle": "A Personal Interpretation",
  "foreword": "A short, warm, one-paragraph foreword for the book.",
  "preface": "A detailed, multi-paragraph preface explaining the concepts and purpose of the book. This should be at least 200 words.",
  "prologue": "A beautiful, multi-paragraph prologue that sets an introspective and mystical tone for the reader's journey. This should be at least 300 words.",
  "chapters": [
    {{ 
      "title": "A Creative, Thematic Chapter Title",
      "theme": "A one-sentence summary of this chapter's core theme.",
      "description": "A detailed one-paragraph description of what this chapter will cover, to be used as a prompt for the chapter writer. This description MUST also be free of astrological jargon."
    }}
  ],
  "epilogue": "A thoughtful, multi-paragraph concluding epilogue that summarizes the user's journey and offers a final, inspiring reflection. This should be at least 200 words."
}}

CRITICAL INSTRUCTIONS:
The root of the object MUST contain title, subtitle, foreword, preface, prologue, chapters, and epilogue.
The chapters value MUST be an array of objects.
Every object inside the chapters array MUST have a title, theme, and description key.
Generate exactly {num_chapters} chapters based on the most significant themes in the user's data.
Do not add any text, explanations, or markdown formatting like ```json outside of the JSON object itself. Your entire response must be only the JSON.
User's Astrological Data:
{user_astrology_data}
"""
# --- END OF THE FIX ---


def lambda_handler(event, context):
    print(f"ArchitectBook received event: {json.dumps(event)}")

    # Your existing payload handling logic (Unchanged)
    if 'Payload' in event:
        payload = event['Payload']
    else:
        payload = event

    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    astrology_s3_path = payload.get('astrology_json_s3_path')

    if not all([order_id, line_item_id, astrology_s3_path]):
        raise ValueError("Missing required fields after processing payload.")

    num_chapters = 4

    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        api_keys = json.loads(secret_payload['SecretString'])
        openai_client.api_key = api_keys.get('OpenAIKey')
        if not openai_client.api_key:
            raise ValueError("OpenAI API key not found in Secrets Manager")

        bucket, key = parse_s3_path(astrology_s3_path)
        s3_object = s3_client.get_object(Bucket=bucket, Key=key)
        astrology_data = json.loads(s3_object['Body'].read().decode('utf-8'))

        prompt = build_book_structure_prompt(astrology_data, num_chapters)
        
        response = openai_client.chat.completions.create(
            model="gpt-4-turbo-preview", # Using a more recent model
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3
        )

        book_structure_str = response.choices[0].message.content
        book_structure = json.loads(book_structure_str)

        output_key = f"book-structures/{order_id}/{line_item_id}.json"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=output_key,
            Body=json.dumps(book_structure, indent=2),
            ContentType='application/json'
        )
        
        # This ensures the original payload is passed through, with the new path added
        payload['book_structure_s3_path'] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
        return payload

    except Exception as e:
        print(f"ERROR on order {order_id}, line_item {line_item_id}: {e}")
        raise e
