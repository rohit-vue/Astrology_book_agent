import boto3
import json
import os
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from openai import OpenAI

sqs = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
secrets_manager = boto3.client('secretsmanager')
openai_client = OpenAI(api_key="dummy") 

ORDERS_TABLE_NAME = os.environ.get('ORDERS_TABLE_NAME')
BOOK_ORDERS_QUEUE_URL = os.environ.get('BOOK_ORDERS_QUEUE_URL')
RAW_PAYLOADS_BUCKET = os.environ.get('RAW_PAYLOADS_BUCKET')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

SHOPIFY_WEBHOOK_SECRET = None

def build_data_extraction_prompt(date_time_str: str, location_str: str) -> str:
    user_prompt = f"Time: {date_time_str}, Location: {location_str}"
    return f"""
    From the user's provided time and location, extract structured birth information.
    Tasks:
    1. Parse date (day, month, year).
    2. Parse time (hour 0-23, minute).
    3. Geocode location (lat, lon).
    4. Determine UTC timezone offset (float).

    USER PROMPT: "{user_prompt}"

    Return JSON: "day", "month", "year", "hour", "min", "lat", "lon", "tzone".
    """

def parse_birth_data_with_ai(date_time_str, location_str):
    print(f"Parsing with AI: date_time='{date_time_str}', location='{location_str}'")
    prompt = build_data_extraction_prompt(date_time_str, location_str)
    
    response = openai_client.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0
    )
    return json.loads(response.choices[0].message.content)

def verify_shopify_webhook(data, hmac_header):
    if not SHOPIFY_WEBHOOK_SECRET: 
        print("DEBUG: No Secret Loaded!")
        return False
        
    digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode('utf-8'), data, hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest).decode('utf-8') 
    
    print(f"DEBUG SECRET (First 4): {SHOPIFY_WEBHOOK_SECRET[:4]}...")
    print(f"DEBUG HMAC Received:   {hmac_header}")
    print(f"DEBUG HMAC Calculated: {computed_hmac}")
    
    return hmac.compare_digest(computed_hmac, hmac_header)

def lambda_handler(event, context):
    global SHOPIFY_WEBHOOK_SECRET
    if not SHOPIFY_WEBHOOK_SECRET:
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        SHOPIFY_WEBHOOK_SECRET = secrets.get('ShopifyWebhookSecret')
        openai_client.api_key = secrets.get('OpenAIKey')

    headers = {k.lower(): v for k, v in event['headers'].items()}
    hmac_header = headers.get('x-shopify-hmac-sha256')
    
    is_base64_encoded = event.get('isBase64Encoded', False)
    raw_body_bytes = base64.b64decode(event['body']) if is_base64_encoded else event['body'].encode('utf-8')

    if not hmac_header or not verify_shopify_webhook(raw_body_bytes, hmac_header): 
        print("ERROR: HMAC verification failed.")
        return {'statusCode': 401, 'body': 'Unauthorized'}
    
    payload_string = raw_body_bytes.decode('utf-8')
    payload = json.loads(payload_string)
    order_id = f"shpfy_{payload['id']}"

    print(f"DEBUG SHIPPING LINES: {json.dumps(payload.get('shipping_lines', []))}")
    
    try:
        books_for_workflow = []
        print(f"Processing order {order_id}. Found {len(payload.get('line_items', []))} line items.")

        shipping_lines = payload.get('shipping_lines', [])
        shipping_code = "STANDARD"
        shipping_title = "Standard"
        
        if shipping_lines and len(shipping_lines) > 0:
            shipping_code = shipping_lines[0].get('code', 'STANDARD').upper()
            shipping_title = shipping_lines[0].get('title', 'Standard').upper()

        for line_item in payload.get('line_items', []):
            line_item_id = str(line_item.get('id'))
            
            if line_item.get('gift_card', False):
                print(f"Skipping Gift Card: {line_item_id}")
                continue

            unstructured_props = {prop['name']: prop['value'] for prop in line_item.get('properties', [])}
            print(f"DEBUG ITEM {line_item_id} PROPERTIES: {json.dumps(unstructured_props)}")

            cover_title = unstructured_props.get("Name of the person") or unstructured_props.get("Name")
            if not cover_title:
                shipping = payload.get('shipping_address', {})
                cover_title = f"{shipping.get('first_name','')} {shipping.get('last_name','')}".strip() or "Luminary"
            
            location_str = unstructured_props.get("Address") or unstructured_props.get("Birthplace")
            date_time_str = unstructured_props.get("Delivery Date & Time") or unstructured_props.get("Birthdate and Birth Time")
            
            raw_variant_title = line_item.get('variant_title', '').strip()
            
            if not raw_variant_title or raw_variant_title.lower() == "default title":
                focus = "Personality"
            else:
                focus = raw_variant_title
            
            print(f"Book Focus determined as: '{focus}'")
            requires_shipping = line_item.get('requires_shipping', True)
            language = unstructured_props.get("Language") or unstructured_props.get("Book Language", "English")

            if not all([location_str, date_time_str]):
                print(f"Skipping line item {line_item_id} - missing location/date.")
                continue

            structured_birth_data = parse_birth_data_with_ai(date_time_str, location_str)
            
            book_details = {
                "line_item_id": line_item_id,
                "cover_title": cover_title,
                "focus": focus,
                "language": language,
                "requires_shipping": requires_shipping,
                "birth_data": structured_birth_data,
                "shipping_code": shipping_code,
                "shipping_title": shipping_title
            }
            books_for_workflow.append(book_details)

        if not books_for_workflow:
            print("No valid books found in order.")
            return {'statusCode': 200, 'body': 'No valid books found.'}

        s3_key = f"raw-payloads/{order_id}.json"
        s3.put_object(Bucket=RAW_PAYLOADS_BUCKET, Key=s3_key, Body=payload_string, ContentType='application/json')

        clean_payload = {
            "order_id": order_id,
            "shopify_payload_s3_path": f"s3://{RAW_PAYLOADS_BUCKET}/{s3_key}",
            "customer_details": payload.get('customer', {}),
            "shipping_address": payload.get('shipping_address', {}),
            "books": books_for_workflow
        }
        
        table = dynamodb.Table(ORDERS_TABLE_NAME)
        table.put_item(Item={
            'order_id': order_id, 
            'created_at': datetime.now(timezone.utc).isoformat(), 
            'status': 'received', 
            'book_count': len(books_for_workflow), 
            'customer_email': payload.get('customer', {}).get('email')
        })

        sqs.send_message(QueueUrl=BOOK_ORDERS_QUEUE_URL, MessageBody=json.dumps(clean_payload))
        return {'statusCode': 200, 'body': 'OK'}
        
    except Exception as e:
        print(f"ERROR: {e}")
        return {'statusCode': 500, 'body': 'Error'}