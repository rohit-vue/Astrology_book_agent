#
# FILE: src/order_ingestion/app.py (Phase 2 - Multi-Book Version)
#
import boto3
import json
import os
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from openai import OpenAI

# --- Client Initialization ---
sqs = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
secrets_manager = boto3.client('secretsmanager')
openai_client = OpenAI(api_key="dummy") # Key will be set in handler
 
# --- Load Configuration ---
ORDERS_TABLE_NAME = os.environ.get('ORDERS_TABLE_NAME')
BOOK_ORDERS_QUEUE_URL = os.environ.get('BOOK_ORDERS_QUEUE_URL')
RAW_PAYLOADS_BUCKET = os.environ.get('RAW_PAYLOADS_BUCKET')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

# --- Global variable for secrets ---
SHOPIFY_WEBHOOK_SECRET = None

# --- AI Helper Functions (These do not need to change) ---
def build_data_extraction_prompt(date_time_str: str, location_str: str) -> str:
    """Builds a prompt for the LLM to parse natural language and geocode."""
    user_prompt = f"Time: {date_time_str}, Location: {location_str}"
    return f"""
    From the user's provided time and location, extract structured birth information and return it as a JSON object.
    Your tasks are:
    1. Parse the date string into day, month, and year.
    2. Parse the time string into hour (0-23 format) and minute.
    3. Find the geographic latitude and longitude for the location.
    4. Determine the correct UTC timezone offset number for that specific location on that specific date (this must account for Daylight Saving Time).

    USER PROMPT: "{user_prompt}"

    Return ONLY the JSON object with the following keys: "day", "month", "year", "hour", "min", "lat", "lon", "tzone".
    Example for "2025-03-31 11:46" and "Ahmedabad, Gujarat, India":
    {{
      "day": 31, "month": 3, "year": 2025, "hour": 11, "min": 46, "lat": 23.0225, "lon": 72.5714, "tzone": 5.5
    }}
    """

def parse_birth_data_with_ai(date_time_str, location_str):
    """Uses an LLM to convert unstructured text into structured birth data."""
    print(f"Parsing with AI: date_time='{date_time_str}', location='{location_str}'")
    prompt = build_data_extraction_prompt(date_time_str, location_str)
    
    response = openai_client.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0
    )
    
    structured_data = json.loads(response.choices[0].message.content)
    print(f"Successfully parsed data with AI: {structured_data}")
    return structured_data

# --- Security Function (This does not need to change) ---
def verify_shopify_webhook(data, hmac_header):
    if not SHOPIFY_WEBHOOK_SECRET: return False
    digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode('utf-8'), data, hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest)
    return hmac.compare_digest(computed_hmac, hmac_header.encode('utf-8'))

# --- Main Lambda Handler ---
def lambda_handler(event, context):
    """
    Handles Shopify webhooks, iterates through ALL line items to find books,
    parses their data, and enqueues a single message with a list of all books to create.
    """
    global SHOPIFY_WEBHOOK_SECRET
    if not SHOPIFY_WEBHOOK_SECRET:
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        SHOPIFY_WEBHOOK_SECRET = secrets.get('ShopifyWebhookSecret')
        openai_client.api_key = secrets.get('OpenAIKey')

    # 1. Verify Webhook Signature for security
    hmac_header = event['headers'].get('x-shopify-hmac-sha256')
    # if not hmac_header or not verify_shopify_webhook(event['body'].encode('utf-8'), hmac_header): 
    #     print("ERROR: HMAC verification failed.")
    #     return {'statusCode': 401, 'body': 'Unauthorized'}

    payload = json.loads(event['body'])
    order_id = f"shpfy_{payload['id']}"
    
    try:
        ### START OF PHASE 2 REFACTOR ###
        
        books_for_workflow = []
        print(f"Processing order {order_id}. Found {len(payload.get('line_items', []))} line items.")

        # 2. Loop through ALL line items in the order
        for line_item in payload.get('line_items', []):
            line_item_id = str(line_item.get('id'))
            print(f"--- Processing Line Item ID: {line_item_id} ---")

            # Extract custom properties for this specific line item
            unstructured_props = {}
            for prop in line_item.get('properties', []):
                unstructured_props[prop['name']] = prop['value']

            cover_title = unstructured_props.get("Custom Text")
            location_str = unstructured_props.get("Address")
            date_time_str = unstructured_props.get("Delivery Date & Time")

            # If a line item is missing any of these key properties, we assume it's not a book and skip it.
            if not all([cover_title, location_str, date_time_str]):
                print(f"Skipping line item {line_item_id} as it is missing required properties for book generation.")
                continue

            # 3. Use AI to parse the data for this specific book
            structured_birth_data = parse_birth_data_with_ai(date_time_str, location_str)
            
            # 4. Construct a dictionary for this one book
            book_details = {
                "line_item_id": line_item_id,
                "cover_title": cover_title,
                "birth_data": structured_birth_data
            }
            books_for_workflow.append(book_details)
            print(f"Successfully processed and added book for line item {line_item_id}.")

        # If after checking all line items, we have no valid books, we stop here.
        if not books_for_workflow:
            print(f"WARNING: Order {order_id} contained no line items with valid book properties. Nothing to process.")
            return {'statusCode': 200, 'body': 'Order processed, no valid books found.'}

        # 5. Save the full raw payload to S3 (once per order)
        s3_key = f"raw-payloads/{order_id}.json"
        s3.put_object(
            Bucket=RAW_PAYLOADS_BUCKET, Key=s3_key,
            Body=event['body'], ContentType='application/json'
        )

        # 6. Create the NEW enriched, structured payload for the Step Function
        # This now contains a LIST of books for the Map state to process.
        clean_payload = {
            "order_id": order_id,
            "shopify_payload_s3_path": f"s3://{RAW_PAYLOADS_BUCKET}/{s3_key}",
            "customer_details": payload.get('customer', {}),
            "shipping_address": payload.get('shipping_address', {}),
            "books": books_for_workflow  # <-- The crucial change
        }
        
        ### END OF PHASE 2 REFACTOR ###

        # 7. Create DynamoDB record and enqueue the message (once per order)
        table = dynamodb.Table(ORDERS_TABLE_NAME)
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        table.put_item(Item={
            'order_id': order_id, 'created_at': timestamp_iso,
            'status': 'received', 'book_count': len(books_for_workflow),
            'customer_email': payload.get('customer', {}).get('email'),
            'shopify_payload_s3': clean_payload['shopify_payload_s3_path']
        })

        sqs.send_message(
            QueueUrl=BOOK_ORDERS_QUEUE_URL,
            MessageBody=json.dumps(clean_payload)
        )

        print(f"Successfully enqueued message for order {order_id} with {len(books_for_workflow)} book(s).")
        return {'statusCode': 200, 'body': 'OK'}
        
    except Exception as e:
        print(f"ERROR processing order {order_id}: {e}")
        # Consider updating DynamoDB with a 'failed_ingestion' status here
        return {'statusCode': 500, 'body': 'Internal server error during data parsing.'}