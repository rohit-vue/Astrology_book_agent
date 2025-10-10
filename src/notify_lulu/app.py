# FILE: src/notify_lulu/app.py (FINAL CORRECTED VERSION)

import boto3
import json
import os
import requests

# --- AWS Clients (Unchanged) ---
secrets_manager = boto3.client('secretsmanager')
s3_client = boto3.client('s3')

# --- Environment Variables (Unchanged) ---
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
LULU_SANDBOX_MODE = os.environ.get('LULU_SANDBOX_MODE', 'true').lower() == 'true'

# --- URL Configuration (UPDATED) ---
# The base API URL is the same for production and sandbox.
LULU_API_URL = "https://api.lulu.com"
if LULU_SANDBOX_MODE:
    print("RUNNING IN LULU SANDBOX MODE")
else:
    print("RUNNING IN LULU PRODUCTION MODE")


# --- Authentication Function (COMPLETELY REWRITTEN AND CORRECTED) ---
# THIS IS THE NEW, CORRECT FUNCTION, BASED ON THE CORRECT REALM
def get_lulu_token(client_key, client_secret):
    """Authenticates with the Lulu Print API and returns an access token."""
    # THIS is the new, correct URL with the correct "realm"
    token_url = "https://api.lulu.com/auth/realms/lulu-print-api/protocol/openid-connect/token"
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    # This payload format is correct for this endpoint
    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_key,
        'client_secret': client_secret
    }
    
    print("Requesting Lulu PRINT API access token from the final, correct URL...")
    response = requests.post(token_url, headers=headers, data=payload)
    response.raise_for_status()
    
    access_token = response.json()['access_token']
    print("Successfully received Lulu access token.")
    return access_token


# --- Presigned URL Function (This is excellent code and does not need to change) ---
def create_presigned_url(s3_uri, expiration=3600):
    """Generates a presigned URL for an S3 object."""
    bucket_name, key = s3_uri.replace("s3://", "").split("/", 1)
    
    print(f"Generating presigned URL for bucket '{bucket_name}' and key '{key}'...")
    url = s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket_name, 'Key': key},
        ExpiresIn=expiration
    )
    print("Successfully generated presigned URL.")
    return url


# --- Main Handler (Updated for clarity and correctness) ---
def lambda_handler(event, context):
    print(f"Received event to notify Lulu: {json.dumps(event, indent=2)}")
    
    order_id = event['order_id']
    shipping_address = event['shipping_address']
    processed_books = event['processed_books_results']

    try:
        # 1. Fetch secrets (Unchanged)
        print("Fetching secrets...")
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        client_key = secrets.get('LuluApiClientKey')
        client_secret = secrets.get('LuluApiClientSecret')
        
        if not client_key or not client_secret:
            raise ValueError("Lulu API credentials not found in Secrets Manager")

        # 2. Get Lulu authentication token (Now uses the new, working function)
        token = get_lulu_token(client_key, client_secret)

        # 3. Construct the line items for the Lulu print job (Mostly unchanged)
        line_items = []
        for book_result in processed_books:
            # The structure from the Map state is an array, we need to get the first item
            book = book_result[0] if isinstance(book_result, list) else book_result

            pdf_s3_uri = book.get('final_pdf_s3_path') # Corrected key from GeneratePDF output
            cover_title = book.get('cover_title', 'A Personal Portrait')

            if not pdf_s3_uri:
                print(f"WARNING: Skipping a book because its PDF path was not found.")
                continue

            interior_url = create_presigned_url(pdf_s3_uri)
            
            # URGENT: You must replace this placeholder with a real, public URL to your cover file
            cover_url = "https://s3.amazonaws.com/path/to/your/default_cover.pdf"

            line_items.append({
                "external_id": book.get('line_item_id', order_id),
                "printable_normalization": {
                    "cover": { "source_url": cover_url },
                    "interior": { "source_url": interior_url },
                },
                "pod_package_id": "0550X0850BWSTDLW060UC444MNG", 
                "quantity": 1,
                "title": cover_title
            })

        if not line_items:
            raise ValueError(f"Order {order_id} had no valid books to print.")

        # 4. Construct the full payload for the Lulu Print Job API (Unchanged)
        lulu_payload = {
            "external_id": order_id,
            "line_items": line_items,
            "shipping_level": "MAIL",
            "shipping_address": {
                "name": f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}".strip(),
                "street1": shipping_address.get('address1'),
                "city": shipping_address.get('city'),
                "state_code": shipping_address.get('province_code'),
                "country_code": shipping_address.get('country_code'),
                "postcode": shipping_address.get('zip')
            }
        }
        
        # 5. Call the Lulu API to create the print job (Unchanged)
        print(f"Sending print job to Lulu for order {order_id}...")
        print_job_url = f"{LULU_API_URL}/print-jobs/"
        
        # We add a header for sandbox mode if needed
        headers = { 'Authorization': f'Bearer {token}', 'Content-Type': 'application/json' }
        if LULU_SANDBOX_MODE:
            headers['X-Lulu-Sandbox'] = 'true'
        
        response = requests.post(print_job_url, headers=headers, json=lulu_payload)
        response.raise_for_status()
        
        lulu_response = response.json()
        print("Successfully created Lulu print job!")
        print(f"Lulu Job ID: {lulu_response.get('id')}")

        return { "status": "SUCCESS", "lulu_job_id": lulu_response.get('id') }

    except Exception as e:
        print(f"ERROR: Failed to create Lulu print job for order {order_id}. Error: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Lulu API Response Body: {response.text}")
        raise e