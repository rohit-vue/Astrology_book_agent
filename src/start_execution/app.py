# src/start_execution/app.py
import boto3
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from botocore.exceptions import ClientError
from openai import OpenAI
import requests

from structured_schemas import BIRTH_DATA_SCHEMA, chat_response_format

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
ASTROLOGY_API_BASE = os.environ.get("ASTROLOGY_API_BASE", "https://json.astrologyapi.com/v1")
BOOK_FORMATS = frozenset({"hardcover", "paperback", "digital"})


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

def _first_prop(props: dict, *names: str) -> str:
    for name in names:
        val = props.get(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def parse_utc_offset_hours(value: str):
    """Parse Shopify offsets like '-05:00' or '+05:30' to float hours."""
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"([+-])?(\d{1,2}):(\d{2})", text)
    if match:
        sign = -1.0 if match.group(1) == "-" else 1.0
        hours = int(match.group(2))
        minutes = int(match.group(3))
        if minutes >= 60:
            return None
        return sign * (hours + minutes / 60.0)
    try:
        return float(text)
    except ValueError:
        return None


def parse_birth_datetime_parts(value: str):
    """Parse Shopify datetime like '1962-02-27T21:28' into day/month/year/hour/min."""
    text = str(value or "").strip()
    if not text:
        return None
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return {
        "day": dt.day,
        "month": dt.month,
        "year": dt.year,
        "hour": dt.hour,
        "min": dt.minute,
    }


def parse_birth_data_from_line_properties(props: dict):
    """
    Deterministic parse from Shopify line-item properties.
    Uses birth datetime + _Latitude / _Longitude — not shipping address.
    tzone is resolved separately via timezone_with_dst.
    """
    date_time_str = _first_prop(props, "Birthdate and Birth Time", "Delivery Date & Time")
    lat_raw = _first_prop(props, "_Latitude", "Latitude")
    lon_raw = _first_prop(props, "_Longitude", "Longitude")
    parts = parse_birth_datetime_parts(date_time_str)
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None
    if not parts:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {
        **parts,
        "lat": lat,
        "lon": lon,
    }


def get_vedic_auth():
    secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    secrets = json.loads(secret_payload['SecretString'])
    user_id = secrets.get('AstrologyVedicUserID')
    api_key = secrets.get('AstrologyVedicAPIKey')
    if not user_id or not api_key:
        raise KeyError("AstrologyVedicUserID/AstrologyVedicAPIKey missing from secret")
    return user_id, api_key


def fetch_timezone_with_dst(auth, lat: float, lon: float, day: int, month: int, year: int):
    """AstrologyAPI timezone_with_dst — offset in force on the birth date."""
    date_str = f"{int(month):02d}-{int(day):02d}-{int(year)}"
    url = f"{ASTROLOGY_API_BASE.rstrip('/')}/timezone_with_dst"
    try:
        response = requests.post(
            url,
            auth=auth,
            json={"latitude": lat, "longitude": lon, "date": date_str},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        tzone = payload.get("timezone")
        if tzone is None:
            return None
        tzone = float(tzone)
        if not (-14.0 <= tzone <= 14.0):
            return None
        return tzone
    except Exception as exc:
        print(f"timezone_with_dst failed for {date_str} ({lat}, {lon}): {exc}")
        return None


def enrich_birth_data_tzone(birth_data: dict, props: dict | None = None):
    """Set tzone from AstrologyAPI; fallback to Shopify _Utcoffset if API fails."""
    if not birth_data:
        return None
    required = ("day", "month", "year", "lat", "lon")
    if not all(key in birth_data for key in required):
        return None

    tzone = None
    if API_KEYS_SECRET_ARN:
        try:
            auth = get_vedic_auth()
            tzone = fetch_timezone_with_dst(
                auth,
                birth_data["lat"],
                birth_data["lon"],
                birth_data["day"],
                birth_data["month"],
                birth_data["year"],
            )
            if tzone is not None:
                print(
                    f"tzone from timezone_with_dst: {tzone} "
                    f"for {birth_data['year']}-{birth_data['month']:02d}-{birth_data['day']:02d}"
                )
        except Exception as exc:
            print(f"Could not resolve timezone_with_dst: {exc}")

    if tzone is None and props:
        offset_raw = _first_prop(props, "_Utcoffset", "_UTCOffset", "Utcoffset")
        tzone = parse_utc_offset_hours(offset_raw)
        if tzone is not None:
            print(f"tzone fallback to Shopify offset: {tzone}")

    if tzone is None:
        return None

    return {**birth_data, "tzone": tzone}


def parse_book_format(props: dict, shopify_requires_shipping: bool) -> str:
    raw = _first_prop(props, "Book Format", "book format", "Format")
    normalized = re.sub(r"[\s\-]+", "_", raw.strip().lower()) if raw else ""
    aliases = {
        "hard_cover": "hardcover",
        "hardcover": "hardcover",
        "paper_back": "paperback",
        "paperback": "paperback",
        "digital": "digital",
        "ebook": "digital",
        "e_book": "digital",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized in BOOK_FORMATS:
        return normalized
    return "digital" if not shopify_requires_shipping else "hardcover"


def parse_birth_data_with_ai(date_time_str, location_str):
    ensure_openai_api_key()
    prompt = build_data_extraction_prompt(date_time_str, location_str)
    response = openai_client.chat.completions.create(
        model="gpt-5.4-mini-2026-03-17",
        messages=[{"role": "user", "content": prompt}],
        response_format=chat_response_format("birth_data", BIRTH_DATA_SCHEMA),
        temperature=0.0
    )
    return json.loads(response.choices[0].message.content)


def resolve_birth_data(props: dict, date_time_str: str, location_str: str, line_item_id: str):
    parsed = parse_birth_data_from_line_properties(props)
    if parsed:
        enriched = enrich_birth_data_tzone(parsed, props)
        if enriched:
            print(f"Birth data parsed from Shopify properties for line_item_id={line_item_id}")
            return enriched
        print(f"Birth data missing timezone for line_item_id={line_item_id}")
        return None
    if date_time_str and location_str:
        print(f"Birth data falling back to OpenAI for line_item_id={line_item_id}")
        ai_parsed = parse_birth_data_with_ai(date_time_str, location_str)
        return enrich_birth_data_tzone(ai_parsed, props)
    return None

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

        shopify_requires_shipping = line_item.get('requires_shipping', True)
        book_format = parse_book_format(unstructured_props, shopify_requires_shipping)
        requires_shipping = book_format != "digital"
        language = unstructured_props.get("Language") or unstructured_props.get("Book Language", "English")

        structured_birth_data = resolve_birth_data(
            unstructured_props, date_time_str, location_str, line_item_id
        )
        if not structured_birth_data:
            print(f"Skipping line item {line_item_id} - missing location/date.")
            continue

        books_for_workflow.append({
            "line_item_id": line_item_id,
            "cover_title": cover_title,
            "focus": focus,
            "language": language,
            "book_format": book_format,
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
