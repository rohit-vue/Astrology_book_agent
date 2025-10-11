# FILE: src/notify_lulu/app.py (FINAL AND CORRECTED)

import boto3
import json
import os
import requests

s3_client = boto3.client('s3')
secrets_manager = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
LULU_SANDBOX_MODE = os.environ.get('LULU_SANDBOX_MODE', 'true').lower() == 'true'

# --- THIS IS THE FINAL FIX ---
# We now use the proven correct URL structure.
if LULU_SANDBOX_MODE:
    LULU_API_URL = "https://api.sandbox.lulu.com"
    LULU_AUTH_URL = "https://api.sandbox.lulu.com/auth/realms/glasstree/protocol/openid-connect/token"
    print("RUNNING IN LULU SANDBOX MODE")
else:
    LULU_API_URL = "https://api.lulu.com"
    LULU_AUTH_URL = "https://api.lulu.com/auth/realms/glasstree/protocol/openid-connect/token"
    print("RUNNING IN LULU PRODUCTION MODE")

def get_lulu_token(client_key, client_secret):
    """Authenticates with Lulu using Basic Auth, matching the successful Postman request."""
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    payload = {'grant_type': 'client_credentials'}
    
    print(f"Requesting Lulu API access token from {LULU_AUTH_URL}...")
    # This combination of `auth` and `data` perfectly mimics the successful Postman test.
    response = requests.post(LULU_AUTH_URL, headers=headers, auth=(client_key, client_secret), data=payload)
    response.raise_for_status()
    
    access_token = response.json()['access_token']
    print("Successfully received Lulu access token.")
    return access_token

# ... (The rest of your file is correct and does not need to change) ...

def create_presigned_url(s3_uri, expiration=3600):
    bucket_name, key = s3_uri.replace("s3://", "").split("/", 1)
    url = s3_client.generate_presigned_url('get_object', Params={'Bucket': bucket_name, 'Key': key}, ExpiresIn=expiration)
    return url

def lambda_handler(event, context):
    print(f"Received event to notify Lulu: {json.dumps(event, indent=2)}")
    
    # Your payload unwrapping logic is correct
    if 'Payload' in event and isinstance(event['Payload'], dict):
        payload = event['Payload']
    else:
        payload = event

    order_id = payload.get('order_id')
    shipping_address = payload.get('shipping_address')
    # This key comes from the Map state's ResultPath
    processed_books = payload.get('processed_books_results', []) 
    
    # We need the final output from the GeneratePDF step
    # Let's find the correct PDF path from the processed books array
    final_pdf_s3_path = None
    if processed_books and isinstance(processed_books, list) and 'Payload' in processed_books[0]:
         final_pdf_s3_path = processed_books[0]['Payload'].get('final_pdf_s3_path')
         # For multi-book orders, you'll iterate here. For now, let's assume one.
         # This logic will need to be expanded for multi-book print jobs.

    try:
        print("Fetching secrets...")
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        client_key = secrets.get('LuluApiClientKey')
        client_secret = secrets.get('LuluApiClientSecret')
        
        token = get_lulu_token(client_key, client_secret)

        # Simplified for a single book for this final test
        line_items = [{
            "external_id": payload.get('line_item_id', order_id),
            "printable_normalization": {
                "cover": { "source_url": "https://path.to/your/default/cover.pdf" }, # Placeholder
                "interior": { "source_url": create_presigned_url(final_pdf_s3_path) },
            },
            "pod_package_id": "0600X0900BWSTDPB060UW444MXX", # 6x9 B&W Paperback
            "quantity": 1,
            "title": payload.get('cover_title', 'A Personal Portrait')
        }]

        lulu_payload = {
            "external_id": order_id,
            "line_items": line_items,
            "production_delay": 1440,
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
        
        print(f"Sending print job to Lulu for order {order_id}...")
        print_job_url = f"{LULU_API_URL}/print-jobs/"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        
        response = requests.post(print_job_url, headers=headers, json=lulu_payload)
        response.raise_for_status()
        
        lulu_response = response.json()
        print("Successfully created Lulu print job! Lulu Job ID: {lulu_response.get('id')}")

        # Return the final payload, now including the Lulu result
        payload['lulu_submission_result'] = lulu_response
        return payload

    except Exception as e:
        print(f"ERROR: Failed to create Lulu print job for order {order_id}. Error: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Lulu API Response Body: {response.text}")
        raise e