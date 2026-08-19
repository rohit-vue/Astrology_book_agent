import boto3
import json
import os
import hmac
import hashlib
import base64
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, timezone

sqs = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
secrets_manager = boto3.client('secretsmanager')

ORDERS_TABLE_NAME = os.environ.get('ORDERS_TABLE_NAME')
BOOK_ORDERS_QUEUE_URL = os.environ.get('BOOK_ORDERS_QUEUE_URL')
RAW_PAYLOADS_BUCKET = os.environ.get('RAW_PAYLOADS_BUCKET')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
FACTORY_START_DELAY_HMS = os.environ.get('FACTORY_START_DELAY_HMS', '00:00:00')

SHOPIFY_WEBHOOK_SECRET = None

def parse_hms_delay(value: str) -> timedelta:
    parts = (value or "00:00:00").strip().split(":")
    if len(parts) != 3:
        raise ValueError("HH:MM:SS format expected.")
    hours, minutes, seconds = [int(x) for x in parts]
    if hours < 0 or minutes < 0 or seconds < 0 or minutes >= 60 or seconds >= 60:
        raise ValueError("Invalid HH:MM:SS value.")
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)

def parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace('Z', '+00:00') if isinstance(value, str) else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def build_job_id(headers, payload):
    shop_domain = (
        headers.get('x-shopify-shop-domain')
        or payload.get('shop_domain')
        or payload.get('domain')
        or "unknown-shop.myshopify.com"
    ).strip().lower()
    webhook_id = (headers.get('x-shopify-webhook-id') or "").strip()
    order_source_id = str(payload.get('id') or "")

    if webhook_id:
        return f"{shop_domain}:{webhook_id}"
    if order_source_id:
        return f"{shop_domain}:{order_source_id}"
    return f"{shop_domain}:{int(datetime.now(timezone.utc).timestamp())}"

def verify_shopify_webhook(data, hmac_header):
    if not SHOPIFY_WEBHOOK_SECRET: 
        print("DEBUG: No Secret Loaded!")
        return False
        
    digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode('utf-8'), data, hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest).decode('utf-8')
    return hmac.compare_digest(computed_hmac, hmac_header)

def lambda_handler(event, context):
    global SHOPIFY_WEBHOOK_SECRET
    if not SHOPIFY_WEBHOOK_SECRET:
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        SHOPIFY_WEBHOOK_SECRET = secrets.get('ShopifyWebhookSecret')

    headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
    hmac_header = headers.get('x-shopify-hmac-sha256')
    
    is_base64_encoded = event.get('isBase64Encoded', False)
    raw_body_bytes = base64.b64decode(event['body']) if is_base64_encoded else event['body'].encode('utf-8')

    if not hmac_header or not verify_shopify_webhook(raw_body_bytes, hmac_header): 
        print("ERROR: HMAC verification failed.")
        return {'statusCode': 401, 'body': 'Unauthorized'}
    
    payload_string = raw_body_bytes.decode('utf-8')
    payload = json.loads(payload_string)
    order_source_id = payload.get('id')
    if not order_source_id:
        print("ERROR: Missing Shopify order id in payload.")
        return {'statusCode': 400, 'body': 'Bad Request'}
    order_id = f"shpfy_{order_source_id}"
    job_id = build_job_id(headers, payload)

    webhook_created_at_raw = payload.get('created_at')
    try:
        webhook_created_at = parse_iso_datetime(webhook_created_at_raw) if webhook_created_at_raw else datetime.now(timezone.utc)
    except Exception:
        print(f"Invalid Shopify created_at '{webhook_created_at_raw}', using current UTC time.")
        webhook_created_at = datetime.now(timezone.utc)

    try:
        start_delay = parse_hms_delay(FACTORY_START_DELAY_HMS)
    except Exception:
        print(f"Invalid FACTORY_START_DELAY_HMS '{FACTORY_START_DELAY_HMS}', using 00:00:00.")
        start_delay = timedelta()

    factory_start_at = webhook_created_at + start_delay

    print(f"DEBUG SHIPPING LINES: {json.dumps(payload.get('shipping_lines', []))}")
    
    try:
        received_at = datetime.now(timezone.utc)
        s3_key = f"raw-payloads/{received_at:%Y/%m/%d}/{job_id}_{received_at.strftime('%H%M%S%f')}.json"
        s3.put_object(Bucket=RAW_PAYLOADS_BUCKET, Key=s3_key, Body=payload_string, ContentType='application/json')

        shop_domain = (
            headers.get('x-shopify-shop-domain')
            or payload.get('shop_domain')
            or payload.get('domain')
            or "unknown-shop.myshopify.com"
        ).strip().lower()
        webhook_event_id = headers.get('x-shopify-webhook-id')
        
        table = dynamodb.Table(ORDERS_TABLE_NAME)
        try:
            table.put_item(
                Item={
                    'job_id': job_id,
                    'order_id': order_id,
                    'state': 'RECEIVED',
                    'created_at': received_at.isoformat(),
                    'shop_domain': shop_domain,
                    'shopify_order_id': str(order_source_id),
                    'shopify_webhook_id': webhook_event_id or "",
                    'customer_email': payload.get('customer', {}).get('email'),
                    'shopify_payload_s3_path': f"s3://{RAW_PAYLOADS_BUCKET}/{s3_key}",
                    'webhook_created_at': webhook_created_at.isoformat(),
                    'factory_start_delay_hms': FACTORY_START_DELAY_HMS,
                    'factory_start_at': factory_start_at.isoformat()
                },
                ConditionExpression="attribute_not_exists(job_id)"
            )
        except ClientError as ddb_error:
            if ddb_error.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                print(f"Duplicate webhook/job ignored for job_id={job_id}.")
                return {'statusCode': 200, 'body': 'Duplicate already received'}
            raise

        sqs.send_message(
            QueueUrl=BOOK_ORDERS_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id})
        )
        print(f"Persisted and queued job_id={job_id}, order_id={order_id}")
        return {'statusCode': 200, 'body': 'OK'}
        
    except Exception as e:
        print(f"ERROR: {e}")
        return {'statusCode': 500, 'body': 'Error'}
