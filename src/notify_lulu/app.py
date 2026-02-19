import boto3
import json
import os
import requests
from datetime import datetime

s3_client = boto3.client('s3')
secrets_manager = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

LULU_API_URL = "https://api.lulu.com"
LULU_AUTH_URL = "https://api.lulu.com/auth/realms/glasstree/protocol/openid-connect/token"

def get_lulu_token(client_key, client_secret):
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    payload = {'grant_type': 'client_credentials'}
    response = requests.post(LULU_AUTH_URL, headers=headers, auth=(client_key, client_secret), data=payload)
    response.raise_for_status()
    return response.json()['access_token']

def create_presigned_url(s3_uri, expiration=3600):
    bucket_name, key = s3_uri.replace("s3://", "").split("/", 1)
    return s3_client.generate_presigned_url('get_object', Params={'Bucket': bucket_name, 'Key': key}, ExpiresIn=expiration)

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

        raw_ship_code = "STANDARD"
        raw_ship_title = "STANDARD"
        if processed_books_results and len(processed_books_results) > 0:
            raw_ship_code = processed_books_results[0].get('shipping_code', 'STANDARD').upper()
            raw_ship_title = processed_books_results[0].get('shipping_title', 'STANDARD').upper()
            
        lulu_shipping_level = "MAIL"
        
        ship_string = f"{raw_ship_code} {raw_ship_title}"
        
        if "EXPRESS" in ship_string or "PRIORITY" in ship_string:
            lulu_shipping_level = "EXPRESS" 
        elif "GROUND" in ship_string:
            lulu_shipping_level = "GROUND"

        print(f"Shipping Input: {ship_string} -> Lulu Level: {lulu_shipping_level}")

        line_items = []
        for book_result in processed_books_results:
            
            if not book_result.get('requires_shipping', True):
                print(f"Skipping Lulu for {book_result.get('line_item_id')} (Digital Only)")
                continue

            pdf_s3 = book_result.get('final_pdf_s3_path')
            cover_s3 = book_result.get('cover_image_s3_url', '').replace("https://", "s3://").replace(".s3.amazonaws.com", "")
            bd = book_result.get('birth_data', {})
            year = bd.get('year', 2000)
            month = bd.get('month', 1)
            day = bd.get('day', 1)
            hour = bd.get('hour', 0)
            minute = bd.get('min', 0)
            
            foil_title = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
            
            foil_author = "LUMINARY BLUEPRINT"

            if pdf_s3 and cover_s3:
                line_items.append({
                    "external_id": book_result.get('line_item_id', order_id),
                    "quantity": 1,
                    "title": book_result.get('cover_title', 'My Book'),
                    "printable_normalization": {
                        "pod_package_id": "0550X0850BWSTDLW060UC444MNG",
                        "cover": { "source_url": create_presigned_url(cover_s3) },
                        "interior": { "source_url": create_presigned_url(pdf_s3) },
                        "foil_stamp_title_text": foil_title,
                        "foil_stamp_author_text": foil_author
                    }
                })

        if not line_items:
            return {"status": "skipped", "reason": "digital_only"}

        name = shipping_address.get('name') or f"{shipping_address.get('first_name','')} {shipping_address.get('last_name','')}".strip()
        street1 = shipping_address.get('street1') or shipping_address.get('address1') or ""
        street2 = shipping_address.get('street2') or shipping_address.get('address2') or ""
        
        if len(street1) > 30:
            overflow = street1[30:]
            street1 = street1[:30]
            street2 = (overflow + " " + street2).strip()
        street2 = street2[:30]

        lulu_address = {
            "name": name[:30],
            "street1": street1,
            "street2": street2,
            "city": shipping_address.get('city'),
            "country_code": shipping_address.get('country_code'),
            "postcode": shipping_address.get('postcode') or shipping_address.get('zip'),
            "state_code": shipping_address.get('state_code') or shipping_address.get('province_code'),
            "phone_number": shipping_address.get("phone", "555-555-5555"),
            "email": customer_details.get('email') 
        }
        
        lulu_payload = {
            "external_id": clean_order_id,
            "line_items": line_items,
            "shipping_level": lulu_shipping_level,
            "shipping_address": lulu_address,
            "contact_email": "orders@luminaryblueprint.com",
            "production_delay": 0 
        }
        
        print(f"Sending to Lulu: {json.dumps(lulu_payload)}")
        
        resp = requests.post(f"{LULU_API_URL}/print-jobs/", headers={'Authorization': f'Bearer {token}'}, json=lulu_payload)
        print(f"Lulu Response: {resp.text}")
        resp.raise_for_status()
        
        return resp.json()

    except Exception as e:
        print(f"ERROR: {e}")
        raise e