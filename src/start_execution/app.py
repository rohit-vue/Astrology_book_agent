# src/start_execution/app.py
import boto3
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from botocore.exceptions import ClientError
from openai import OpenAI

# Initialize the Step Functions client
sfn_client = boto3.client('stepfunctions')
sqs_client = boto3.client('sqs')
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secrets_manager = boto3.client('secretsmanager')
openai_client = OpenAI(api_key="dummy")

# Get state machine ARNs from environment.
STATE_MACHINE_ARN = os.environ['STATE_MACHINE_ARN']
STATE_MACHINE_ARN_V2 = os.environ.get('STATE_MACHINE_ARN_V2')
USE_STATE_MACHINE_V2 = os.environ.get('USE_STATE_MACHINE_V2', 'false').strip().lower() in ('1', 'true', 'yes', 'on')
BOOK_ORDERS_QUEUE_URL = os.environ.get('BOOK_ORDERS_QUEUE_URL')
ORDERS_TABLE_NAME = os.environ.get('ORDERS_TABLE_NAME')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')


def get_selected_state_machine_arn() -> str:
    if USE_STATE_MACHINE_V2 and STATE_MACHINE_ARN_V2:
        return STATE_MACHINE_ARN_V2
    return STATE_MACHINE_ARN

def parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace('Z', '+00:00') if isinstance(value, str) else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def ensure_openai_api_key():
    if openai_client.api_key and openai_client.api_key != "dummy":
        return
    secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    secrets = json.loads(secret_payload['SecretString'])
    openai_client.api_key = secrets.get('OpenAIKey')

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
    ensure_openai_api_key()
    prompt = build_data_extraction_prompt(date_time_str, location_str)
    response = openai_client.chat.completions.create(
        model="gpt-5.4-mini-2026-03-17",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0
    )
    return json.loads(response.choices[0].message.content)

def parse_s3_uri(s3_uri: str):
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip('/')

def load_job_record(job_id: str):
    table = dynamodb.Table(ORDERS_TABLE_NAME)
    response = table.get_item(Key={"job_id": job_id})
    return response.get("Item")

def load_raw_shopify_payload(s3_uri: str):
    bucket, key = parse_s3_uri(s3_uri)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return json.loads(response['Body'].read().decode('utf-8'))

def build_workflow_payload(job_record: dict, payload: dict):
    order_id = job_record.get("order_id") or f"shpfy_{payload.get('id')}"
    webhook_created_at = job_record.get("webhook_created_at")
    factory_start_at = job_record.get("factory_start_at")
    factory_start_delay_hms = job_record.get("factory_start_delay_hms", "00:00:00")

    books_for_workflow = []
    shipping_lines = payload.get('shipping_lines', [])
    shipping_code = "STANDARD"
    if shipping_lines and len(shipping_lines) > 0:
        shipping_code = (shipping_lines[0].get('code') or "STANDARD").upper()

    for line_item in payload.get('line_items', []):
        line_item_id = str(line_item.get('id'))
        if line_item.get('gift_card', False):
            print(f"Skipping Gift Card: {line_item_id}")
            continue

        unstructured_props = {}
        for prop in line_item.get('properties', []):
            name = prop.get('name')
            value = prop.get('value')
            if name:
                unstructured_props[name] = value

        cover_title = unstructured_props.get("Name of the person") or unstructured_props.get("Name")
        if not cover_title:
            shipping = payload.get('shipping_address', {})
            cover_title = f"{shipping.get('first_name','')} {shipping.get('last_name','')}".strip() or "Luminary"

        location_str = unstructured_props.get("Address") or unstructured_props.get("Birthplace")
        date_time_str = unstructured_props.get("Delivery Date & Time") or unstructured_props.get("Birthdate and Birth Time")

        raw_variant_title = (line_item.get('variant_title') or '').strip()
        focus = "Personality" if (not raw_variant_title or raw_variant_title.lower() == "default title") else raw_variant_title

        requires_shipping = line_item.get('requires_shipping', True)
        language = unstructured_props.get("Language") or unstructured_props.get("Book Language", "English")

        if not all([location_str, date_time_str]):
            print(f"Skipping line item {line_item_id} - missing location/date.")
            continue

        structured_birth_data = parse_birth_data_with_ai(date_time_str, location_str)

        books_for_workflow.append({
            "line_item_id": line_item_id,
            "cover_title": cover_title,
            "focus": focus,
            "language": language,
            "requires_shipping": requires_shipping,
            "birth_data": structured_birth_data,
            "shipping_code": shipping_code
        })

    return {
        "job_id": job_record.get("job_id"),
        "order_id": order_id,
        "shopify_payload_s3_path": job_record.get("shopify_payload_s3_path"),
        "customer_details": payload.get('customer', {}),
        "shipping_address": payload.get('shipping_address', {}),
        "webhook_created_at": webhook_created_at,
        "factory_start_delay_hms": factory_start_delay_hms,
        "factory_start_at": factory_start_at,
        "books": books_for_workflow
    }

def lambda_handler(event, context):
    """
    Triggered by SQS. Loops through messages and starts a Step Function execution for each.
    """
    print(f"Received {len(event.get('Records', []))} records from SQS.")

    for record in event.get('Records', []):
        try:
            message_body = json.loads(record['body'])
            workflow_input = None
            order_id = None
            job_id = message_body.get('job_id')

            if message_body.get('order_id') and message_body.get('books'):
                workflow_input = message_body
                order_id = workflow_input.get('order_id')
            else:
                if not job_id:
                    print("ERROR: SQS message is missing 'job_id'. Skipping.")
                    continue

                if not ORDERS_TABLE_NAME:
                    raise ValueError("ORDERS_TABLE_NAME is required.")
                if not API_KEYS_SECRET_ARN:
                    raise ValueError("API_KEYS_SECRET_ARN is required.")

                job_record = load_job_record(job_id)
                if not job_record:
                    print(f"ERROR: No job record found for job_id={job_id}.")
                    continue

                scheduled_start = job_record.get('factory_start_at')
                if scheduled_start:
                    target_dt = None
                    try:
                        target_dt = parse_iso_datetime(scheduled_start)
                    except Exception as schedule_error:
                        print(f"WARNING: Could not evaluate factory_start_at for job {job_id}: {schedule_error}")

                    if target_dt:
                        now_utc = datetime.now(timezone.utc)
                        remaining_seconds = int((target_dt.astimezone(timezone.utc) - now_utc).total_seconds())
                        if remaining_seconds > 0:
                            if not BOOK_ORDERS_QUEUE_URL:
                                raise ValueError("BOOK_ORDERS_QUEUE_URL is required for delayed scheduling.")
                            delay_seconds = min(900, remaining_seconds)
                            print(f"Job {job_id} not due yet. Requeueing for {delay_seconds}s.")
                            sqs_client.send_message(
                                QueueUrl=BOOK_ORDERS_QUEUE_URL,
                                MessageBody=json.dumps({"job_id": job_id}),
                                DelaySeconds=delay_seconds
                            )
                            continue

                s3_uri = job_record.get("shopify_payload_s3_path")
                if not s3_uri:
                    raise ValueError(f"Missing shopify_payload_s3_path in job record for job_id={job_id}")

                raw_payload = load_raw_shopify_payload(s3_uri)
                workflow_input = build_workflow_payload(job_record, raw_payload)
                order_id = workflow_input.get('order_id')

                if not workflow_input.get("books"):
                    print(f"No valid books found for job_id={job_id}.")
                    continue

            if not order_id:
                print("ERROR: Missing order_id in workflow input. Skipping.")
                continue

            scheduled_start = workflow_input.get('factory_start_at')
            if scheduled_start:
                target_dt = None
                try:
                    target_dt = parse_iso_datetime(scheduled_start)
                except Exception as schedule_error:
                    print(f"WARNING: Could not evaluate factory_start_at for order {order_id}: {schedule_error}")

                if target_dt:
                    now_utc = datetime.now(timezone.utc)
                    remaining_seconds = int((target_dt.astimezone(timezone.utc) - now_utc).total_seconds())
                    if remaining_seconds > 0:
                        if not BOOK_ORDERS_QUEUE_URL:
                            raise ValueError("BOOK_ORDERS_QUEUE_URL is required for delayed scheduling.")
                        delay_seconds = min(900, remaining_seconds)
                        requeue_payload = {"job_id": job_id} if job_id else workflow_input
                        print(f"Order {order_id} not due yet. Requeueing for {delay_seconds}s.")
                        sqs_client.send_message(
                            QueueUrl=BOOK_ORDERS_QUEUE_URL,
                            MessageBody=json.dumps(requeue_payload),
                            DelaySeconds=delay_seconds
                        )
                        continue

            selected_state_machine_arn = get_selected_state_machine_arn()
            print(f"Starting Step Function execution for order_id: {order_id} using {selected_state_machine_arn}")

            try:
                sfn_client.start_execution(
                    stateMachineArn=selected_state_machine_arn,
                    name=order_id,
                    input=json.dumps(workflow_input)
                )
            except ClientError as sfn_error:
                if sfn_error.response.get('Error', {}).get('Code') == 'ExecutionAlreadyExists':
                    print(f"Execution already exists for order_id={order_id}, skipping.")
                    continue
                raise

        except Exception as e:
            print(f"ERROR: Failed to start execution for record: {record}. Error: {e}")
            raise e 

    return {'statusCode': 200, 'body': 'Successfully started executions.'}
