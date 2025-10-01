import boto3
import json
import os
from openai import OpenAI
from urllib.parse import urlparse

# Initialize AWS clients
s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')

# Load configuration from environment variables
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
openai_client = OpenAI(api_key="dummy")

# --- Helper function to parse S3 paths ---
def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

# --- Prompt Builder Logic (No changes here) ---
def build_book_structure_prompt(natal_chart_json: dict, num_chapters: int) -> str:
    return f"""
    You are a master psychological interpreter and book architect. Your task is to analyze the provided symbolic data (a natal chart) and design a thematic structure for a deeply personal book.
    CRITICAL INSTRUCTIONS:
    1. Analyze Holistically: Do not simply list placements. Instead, identify the most significant and distinct psychological patterns, energetic tensions, and core narratives.
    2. Generate Exact Chapter Count: Your primary goal is to generate exactly {num_chapters} distinct, meaningful chapter themes. You MUST NOT generate more or fewer than this number.
    3. No Jargon: Your output themes and summaries MUST NOT contain any astrological jargon (planets, signs, houses, aspects, etc.). Translate everything into psychological and experiential language.
    4. Output JSON: Return ONLY a JSON object with a single key "chapters". This key must contain a list of exactly {num_chapters} chapter objects. Each chapter object must have these keys: "theme_title", "summary", "keywords".
    SYMBOLIC DATA TO ANALYZE:
    ---
    {json.dumps(natal_chart_json, indent=2)}
    ---
    REMINDER: You must return exactly {num_chapters} chapters in your JSON response.
    """

def lambda_handler(event, context):
    print(f"ArchitectBook received event: {json.dumps(event)}")

    # --- START OF THE FIX ---
    # The previous Lambda's result is wrapped in a "Payload" key.
    # We check for its existence and use it as our main data object.
    if 'Payload' in event:
        print("Detected nested 'Payload'. Unwrapping...")
        payload = event['Payload']
    else:
        # If there's no 'Payload', we're likely in a direct test, so use the event itself.
        payload = event

    # Now, we extract everything from the 'payload' object, not the 'event' object.
    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    astrology_s3_path = payload.get('astrology_json_s3_path')
    # --- END OF THE FIX ---

    # --- Check for missing fields ---
    if not all([order_id, line_item_id, astrology_s3_path]):
        raise ValueError(f"Missing required fields after processing payload. Full incoming event: {json.dumps(event)}")

    num_chapters = 4

    try:
        # Retrieve OpenAI API key from Secrets Manager
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        api_keys = json.loads(secret_payload['SecretString'])
        openai_client.api_key = api_keys.get('OpenAIKey')
        if not openai_client.api_key:
            raise ValueError("OpenAI API key not found in Secrets Manager")

        # Download astrology data from S3
        print(f"Downloading astrology data from: {astrology_s3_path}")
        bucket, key = parse_s3_path(astrology_s3_path)
        s3_object = s3_client.get_object(Bucket=bucket, Key=key)
        astrology_data = json.loads(s3_object['Body'].read().decode('utf-8'))
        print("Successfully downloaded astrology data from S3.")

        # Call OpenAI to generate book structure
        prompt = build_book_structure_prompt(astrology_data, num_chapters)
        print(f"Calling OpenAI for order {order_id}, line item {line_item_id}...")
        response = openai_client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        book_structure_str = response.choices[0].message.content
        book_structure = json.loads(book_structure_str)
        print("Successfully received book structure from OpenAI.")

        # Save book structure to S3
        output_key = f"book-structures/{order_id}/{line_item_id}.json"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=output_key,
            Body=json.dumps(book_structure, indent=2),
            ContentType='application/json'
        )
        print(f"Saved book structure to s3://{ARTIFACTS_BUCKET}/{output_key}")

        # Return the original payload with the new S3 path added
        payload['book_structure_s3_path'] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
        return payload

    except Exception as e:
        print(f"ERROR on order {order_id}, line_item {line_item_id}: {e}")
        raise e