import boto3
import json
import os
import re
import time
import requests
from urllib.parse import urlparse

s3_client = boto3.client('s3')
secrets_manager = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

LULU_API_URL = "https://api.lulu.com"
LULU_AUTH_URL = "https://api.lulu.com/auth/realms/glasstree/protocol/openid-connect/token"
LULU_POD_PACKAGE_ID = os.environ.get(
    "LULU_POD_PACKAGE_ID", "0550X0850.BW.STD.LW.060UC444.MNG"
)
LULU_POD_PACKAGE_ID_HARDCOVER = os.environ.get(
    "LULU_POD_PACKAGE_ID_HARDCOVER", LULU_POD_PACKAGE_ID
)
LULU_POD_PACKAGE_ID_PAPERBACK = os.environ.get(
    "LULU_POD_PACKAGE_ID_PAPERBACK", "0550X0850.BW.STD.PB.060UC444.MXX"
)
LULU_CONTACT_EMAIL = os.environ.get("LULU_CONTACT_EMAIL", "orders@luminaryblueprint.com")
LULU_PRODUCTION_DELAY_MINUTES = int(os.environ.get("LULU_PRODUCTION_DELAY_MINUTES", "60"))
LULU_PRINT_JOB_MAX_RETRIES = int(os.environ.get("LULU_PRINT_JOB_MAX_RETRIES", "3"))
LULU_PRINT_JOB_RETRY_BACKOFF_SECONDS = float(
    os.environ.get("LULU_PRINT_JOB_RETRY_BACKOFF_SECONDS", "2")
)

# Lulu Print API shipping_level enum (luluApiDoc.yml)
LULU_SHIPPING_LEVELS = frozenset({
    "MAIL",
    "PRIORITY_MAIL",
    "GROUND_HD",
    "GROUND_BUS",
    "GROUND",
    "EXPEDITED",
    "EXPRESS",
})

# Shopify / storefront codes -> Lulu shipping_level
SHIPPING_CODE_TO_LULU = {
    "MAIL": "MAIL",
    "STANDARD": "MAIL",
    "ECONOMY": "MAIL",
    "PRIORITY_MAIL": "PRIORITY_MAIL",
    "PRIORITY": "PRIORITY_MAIL",
    "GROUND_HD": "GROUND_HD",
    "GROUND_BUS": "GROUND_BUS",
    "GROUND": "GROUND",
    "EXPEDITED": "EXPEDITED",
    "EXPRESS": "EXPRESS",
    "2DAY": "EXPEDITED",
    "OVERNIGHT": "EXPRESS",
}


def get_lulu_token(client_key, client_secret):
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    payload = {'grant_type': 'client_credentials'}
    response = requests.post(LULU_AUTH_URL, headers=headers, auth=(client_key, client_secret), data=payload)
    response.raise_for_status()
    return response.json()['access_token']

def create_presigned_url(s3_uri, expiration=3600):
    bucket_name, key = s3_uri.replace("s3://", "").split("/", 1)
    return s3_client.generate_presigned_url('get_object', Params={'Bucket': bucket_name, 'Key': key}, ExpiresIn=expiration)


def https_url_to_s3_uri(url: str) -> str | None:
    """Convert common S3 HTTPS URLs to s3://bucket/key for presigning."""
    if not url:
        return None
    if url.startswith("s3://"):
        return url
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    host = parsed.netloc
    path = parsed.path.lstrip("/")
    if not path:
        return None
    # bucket.s3.amazonaws.com/key or bucket.s3.region.amazonaws.com/key
    if ".s3." in host and host.endswith(".amazonaws.com"):
        bucket = host.split(".s3.", 1)[0]
        return f"s3://{bucket}/{path}"
    # s3.region.amazonaws.com/bucket/key
    if host.startswith("s3.") and host.endswith(".amazonaws.com"):
        parts = path.split("/", 1)
        if len(parts) == 2:
            return f"s3://{parts[0]}/{parts[1]}"
    # legacy string replace for virtual-hosted style without region
    legacy = url.replace("https://", "s3://").replace(".s3.amazonaws.com", "")
    if legacy.startswith("s3://") and "/" in legacy[5:]:
        return legacy
    return None


def resolve_lulu_shipping_level(raw_code: str | None) -> str:
    if raw_code is None or not str(raw_code).strip():
        return "MAIL"
    normalized = re.sub(r"[\s\-]+", "_", str(raw_code).strip().upper())
    if normalized in LULU_SHIPPING_LEVELS:
        return normalized
    mapped = SHIPPING_CODE_TO_LULU.get(normalized)
    if mapped:
        return mapped
    # Substring fallback (e.g. shopify "USPS_PRIORITY")
    for token, level in (
        ("EXPRESS", "EXPRESS"),
        ("EXPEDITED", "EXPEDITED"),
        ("PRIORITY", "PRIORITY_MAIL"),
        ("GROUND", "GROUND"),
    ):
        if token in normalized:
            return level
    print(f"WARNING: Unknown shipping_code '{raw_code}' -> defaulting to MAIL")
    return "MAIL"


def _is_retryable_lulu_status(status_code: int) -> bool:
    if status_code == 429:
        return True
    if status_code >= 500:
        return True
    return False


def resolve_pod_package_id(book_format: str | None) -> str:
    fmt = (book_format or "hardcover").strip().lower()
    if fmt == "paperback":
        return LULU_POD_PACKAGE_ID_PAPERBACK
    return LULU_POD_PACKAGE_ID_HARDCOVER


def is_print_format(book_format: str | None, requires_shipping: bool) -> bool:
    fmt = (book_format or ("digital" if not requires_shipping else "hardcover")).strip().lower()
    return fmt in {"hardcover", "paperback"}


def build_line_items(processed_books_results, order_id: str) -> list[dict]:
    """Build Lulu line items with fresh presigned URLs (call again on each retry)."""
    line_items = []
    for book_result in processed_books_results:
        book_format = book_result.get("book_format")
        requires_shipping = book_result.get("requires_shipping", True)
        if not is_print_format(book_format, requires_shipping):
            print(
                f"Skipping Lulu for {book_result.get('line_item_id')} "
                f"(format={book_format or 'digital'})"
            )
            continue

        pdf_s3 = book_result.get("final_pdf_s3_path")
        cover_s3 = https_url_to_s3_uri(book_result.get("cover_image_s3_url", ""))
        if not (pdf_s3 and cover_s3):
            print(
                f"Skipping line item {book_result.get('line_item_id')}: "
                f"pdf_s3={bool(pdf_s3)} cover_s3={bool(cover_s3)} "
                f"cover_url={book_result.get('cover_image_s3_url', '')[:120]}"
            )
            continue

        bd = book_result.get("birth_data", {})
        year = bd.get("year", 2000)
        month = bd.get("month", 1)
        day = bd.get("day", 1)
        foil_title = f"{year}-{month:02d}-{day:02d}"
        fmt = (book_format or "hardcover").strip().lower()
        pod_package_id = resolve_pod_package_id(fmt)

        printable = {
            "pod_package_id": pod_package_id,
            "cover": {"source_url": create_presigned_url(cover_s3)},
            "interior": {"source_url": create_presigned_url(pdf_s3)},
        }
        if fmt == "hardcover":
            printable["foil_stamp_title_text"] = foil_title
            printable["foil_stamp_author_text"] = "LUMINARY BLUEPRINT PUBLISHING"

        line_items.append({
            "external_id": book_result.get("line_item_id", order_id),
            "quantity": 1,
            "title": book_result.get("cover_title", "My Book"),
            "printable_normalization": printable,
        })
    return line_items


def _log_lulu_payload(lulu_payload: dict) -> None:
    log_payload = json.loads(json.dumps(lulu_payload))
    for item in log_payload.get("line_items", []):
        pn = item.get("printable_normalization", {})
        for part in ("cover", "interior"):
            if part in pn and "source_url" in pn[part]:
                pn[part]["source_url"] = "<presigned>"
    print(f"Sending to Lulu: {json.dumps(log_payload)}")


def create_print_job_with_retries(
    token: str,
    lulu_payload_template: dict,
    processed_books_results: list,
    order_id: str,
) -> dict:
    """POST /print-jobs/ with retries on transient failures; refresh presigned URLs each attempt."""
    max_attempts = max(1, LULU_PRINT_JOB_MAX_RETRIES + 1)
    last_error = None

    for attempt in range(1, max_attempts + 1):
        line_items = build_line_items(processed_books_results, order_id)
        if not line_items:
            raise ValueError("No printable line items for Lulu submission.")

        lulu_payload = {**lulu_payload_template, "line_items": line_items}
        if attempt == 1:
            _log_lulu_payload(lulu_payload)
        else:
            print(f"Lulu print-job retry attempt {attempt}/{max_attempts}")

        try:
            resp = requests.post(
                f"{LULU_API_URL}/print-jobs/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=lulu_payload,
                timeout=60,
            )
            print(f"Lulu Response ({resp.status_code}) attempt {attempt}: {resp.text}")

            if resp.ok:
                if attempt > 1:
                    print(f"Lulu print-job succeeded on attempt {attempt}")
                return resp.json()

            if not _is_retryable_lulu_status(resp.status_code) or attempt >= max_attempts:
                raise requests.exceptions.HTTPError(
                    f"{resp.status_code} from Lulu print-jobs: {resp.text}",
                    response=resp,
                )

            last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"

        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            if attempt >= max_attempts:
                raise

        backoff = LULU_PRINT_JOB_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
        print(
            f"Lulu print-job failed (attempt {attempt}/{max_attempts}), "
            f"retrying in {backoff:.1f}s. Last error: {last_error}"
        )
        time.sleep(backoff)

    raise RuntimeError(f"Lulu print-job failed after {max_attempts} attempts: {last_error}")


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event, indent=2)}")
    payload = event.get('Payload', event)

    order_id = payload.get('order_id')
    shipping_address = payload.get('shipping_address')
    customer_details = payload.get('customer_details', {})
    processed_books_results = payload.get('processed_books_results')

    if not all([order_id, shipping_address, processed_books_results]):
        raise ValueError("Missing critical data for Lulu submission.")

    try:
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        client_key = secrets.get('LuluApiClientKey')
        client_secret = secrets.get('LuluApiClientSecret')
        token = get_lulu_token(client_key, client_secret)
        raw_order_id = payload.get('order_id', '')
        clean_order_id = raw_order_id.replace("shpfy_", "")

        lulu_shipping_level = "MAIL"
        if processed_books_results:
            lulu_shipping_level = resolve_lulu_shipping_level(
                processed_books_results[0].get("shipping_code")
            )
        print(f"Lulu shipping_level: {lulu_shipping_level}")

        line_items = build_line_items(processed_books_results, order_id)
        if not line_items:
            return {"status": "skipped", "reason": "digital_only"}

        name = shipping_address.get('name') or (
            f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}".strip()
        )
        street1 = shipping_address.get('street1') or shipping_address.get('address1') or ""
        street2 = shipping_address.get('street2') or shipping_address.get('address2') or ""

        if len(street1) > 30:
            overflow = street1[30:]
            street1 = street1[:30]
            street2 = (overflow + " " + street2).strip()
        street2 = street2[:30]

        ship_email = (
            customer_details.get('email')
            or shipping_address.get('email')
            or LULU_CONTACT_EMAIL
        )

        lulu_address = {
            "name": name[:30],
            "street1": street1,
            "street2": street2,
            "city": shipping_address.get('city'),
            "country_code": shipping_address.get('country_code'),
            "postcode": shipping_address.get('postcode') or shipping_address.get('zip'),
            "state_code": shipping_address.get('state_code') or shipping_address.get('province_code'),
            "phone_number": shipping_address.get("phone", "555-555-5555"),
            "email": ship_email,
        }

        production_delay = max(60, min(2880, LULU_PRODUCTION_DELAY_MINUTES))

        lulu_payload_template = {
            "external_id": clean_order_id,
            "shipping_level": lulu_shipping_level,
            "shipping_address": lulu_address,
            "contact_email": LULU_CONTACT_EMAIL,
            "production_delay": production_delay,
        }

        return create_print_job_with_retries(
            token,
            lulu_payload_template,
            processed_books_results,
            order_id,
        )

    except Exception as e:
        print(f"ERROR: {e}")
        raise e
